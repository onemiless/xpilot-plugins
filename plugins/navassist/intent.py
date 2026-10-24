"""Navigation lane target and physical-lamp request, bounded to one visible lane at a time.

Phone LaneInfo indices are absolute road lanes. This module only maps them to the local visible index when the
phone's total count equals the visible count and the recommendation touches the maneuver-side road edge. The
fallback (no LaneInfo) only asks for the extreme visible lane for a supported turn/exit/ramp inside its
lookahead window. It never supplies curvature or acceleration."""
from __future__ import annotations

from dataclasses import dataclass

MANEUVER_DIRECTIONS = {
  "slight_left": "left", "turn_left": "left", "sharp_left": "left", "u_turn_left": "left",
  "exit_left": "left", "ramp_left": "left", "merge_left": "left",
  "slight_right": "right", "turn_right": "right", "sharp_right": "right", "u_turn_right": "right",
  "exit_right": "right", "ramp_right": "right", "merge_right": "right",
}
EDGE_MANEUVERS = frozenset(("turn_left", "turn_right", "sharp_left", "sharp_right", "u_turn_left", "u_turn_right",
                            "exit_left", "exit_right", "ramp_left", "ramp_right", "merge_left", "merge_right",
                            "roundabout"))
MIN_LANE_CHANGE_SPEED_MPS = 20 * 0.44704
MAX_LANE_CHANGE_SPEED_MPS = 33.33
MAX_LANE_CHANGES_PER_EVENT = 5


def maneuver_direction(maneuver: str) -> str:
  return MANEUVER_DIRECTIONS.get(maneuver, "none")


@dataclass
class LanePlan:
  direction: str = "none"
  target_lane: int = -1
  absolute_target: int = -1
  reason: str = "idle"
  edge_anchored: bool = False


def lane_plan(nav: dict, topology: dict, settings, *, v_ego: float) -> LanePlan:
  """Target one visible lane toward the maneuver; -1 when the data cannot justify a request."""
  if not nav.get("valid") or nav.get("stale") or not settings.lane_change_enabled:
    return LanePlan(reason="navUnavailable")
  if not topology.get("valid"):
    return LanePlan(reason="topologyUnavailable")
  maneuver = str(nav.get("maneuver", "none"))
  direction = maneuver_direction(maneuver)
  if direction == "none" or int(nav.get("maneuverEventId", 0)) == 0:
    return LanePlan(reason="noDirectionalManeuver")
  distance = float(nav.get("distanceM", 0.0))
  lookahead = (settings.exit_lane_lookahead_m if maneuver.startswith(("exit", "ramp", "merge"))
               else settings.turn_lane_lookahead_m)
  if not 0.0 < distance <= lookahead:
    return LanePlan(reason="outsideLaneWindow")
  if not MIN_LANE_CHANGE_SPEED_MPS <= v_ego <= MAX_LANE_CHANGE_SPEED_MPS:
    return LanePlan(reason="speed")
  visible = int(topology.get("visibleLaneCount", 0))
  ego = int(topology.get("egoLaneIndexFromLeft", -1))
  if visible < 2 or not 0 <= ego < visible:
    return LanePlan(reason="topologyUnavailable")
  # Edge fallback: ask for the extreme visible lane in the maneuver direction.
  target = 0 if direction == "left" else visible - 1
  plan = LanePlan(direction=direction, target_lane=target, reason="edgeFallback")
  # Trust phone LaneInfo only when its absolute count matches the local visible count and the
  # recommendation is on the maneuver-side edge. Otherwise the indices are not comparable.
  total = int(nav.get("laneTotalCount", -1))
  lanes = nav.get("lanes") or []
  if total == visible:
    recommended = [int(lane["index"]) for lane in lanes if lane.get("recommended")]
    if direction == "left" and 0 in recommended:
      plan = LanePlan(direction=direction, target_lane=0, absolute_target=1, reason="phoneEdge", edge_anchored=True)
    elif direction == "right" and (total - 1) in recommended:
      plan = LanePlan(direction=direction, target_lane=visible - 1, absolute_target=total,
                      reason="phoneEdge", edge_anchored=True)
  if plan.target_lane == ego:
    return LanePlan(reason="alreadyInTargetLane")
  plan.direction = "left" if plan.target_lane < ego else "right"
  if abs(plan.target_lane - ego) > 1:
    plan.target_lane = ego + (-1 if plan.direction == "left" else 1)
  return plan


class TurnSignalCoordinator:
  """Requests the physical lamp for a route turn without authorizing a lane change."""
  SIGNAL_TIMEOUT_NS = 60_000_000_000
  PLAN_GAP_GRACE_NS = 1_500_000_000
  MIN_LOOKAHEAD_M = 40.0
  MAX_LOOKAHEAD_M = 250.0
  LOOKAHEAD_MARGIN_M = 20.0
  TURN_CLEAR_STABLE_NS = 500_000_000

  def __init__(self) -> None:
    self._key = None
    self._direction = "none"
    self._since_ns = 0
    self._gap_since_ns = None
    self._completion_hold = False
    self._clear_since_ns = 0

  def reset(self) -> None:
    self._key = None
    self._direction = "none"
    self._since_ns = 0
    self._gap_since_ns = None
    self._completion_hold = False
    self._clear_since_ns = 0

  def update(self, nav: dict, *, speed_mps: float, now_ns: int, lookahead_time_s: float = 8.0) -> dict:
    direction = maneuver_direction(str(nav.get("maneuver", "none")))
    key = (str(nav.get("sessionId", "")), int(nav.get("routeRevision", 0)), int(nav.get("maneuverEventId", 0)))
    if self._key is not None:
      if now_ns - self._since_ns > self.SIGNAL_TIMEOUT_NS:
        self.reset()
        return {"signalRequested": False, "direction": "none", "targetLaneIndex": -1, "reason": "turnSignalTimeout"}
      if key != self._key or direction != self._direction:
        if key[:2] != self._key[:2]:
          self.reset()
          return {"signalRequested": False, "direction": "none", "targetLaneIndex": -1, "reason": "turnChanged"}
        return {"signalRequested": True, "direction": self._direction, "targetLaneIndex": -1,
                "reason": "turnCompletion", "requestId": key[2]}
      if not nav.get("valid"):
        if self._gap_since_ns is None:
          self._gap_since_ns = now_ns
        if now_ns - self._gap_since_ns <= self.PLAN_GAP_GRACE_NS:
          return {"signalRequested": True, "direction": self._direction, "targetLaneIndex": -1,
                  "reason": "turnApproachGrace", "requestId": key[2]}
        self.reset()
        return {"signalRequested": False, "direction": "none", "targetLaneIndex": -1, "reason": "turnUnavailable"}
      self._gap_since_ns = None
      return {"signalRequested": True, "direction": self._direction, "targetLaneIndex": -1,
              "reason": "turnApproach", "requestId": key[2]}
    if not nav.get("valid") or direction == "none" or key[2] == 0:
      return {"signalRequested": False, "direction": "none", "targetLaneIndex": -1, "reason": "turnUnavailable"}
    lookahead = max(self.MIN_LOOKAHEAD_M, min(self.MAX_LOOKAHEAD_M,
                                              max(0.0, speed_mps) * lookahead_time_s + self.LOOKAHEAD_MARGIN_M))
    distance = float(nav.get("distanceM", 0.0))
    if not 0.0 < distance <= lookahead:
      return {"signalRequested": False, "direction": "none", "targetLaneIndex": -1, "reason": "outsideTurnWindow"}
    self._key, self._direction, self._since_ns = key, direction, now_ns
    self._gap_since_ns = None
    return {"signalRequested": True, "direction": direction, "targetLaneIndex": -1,
            "reason": "turnApproach", "requestId": key[2]}


class LaneIntentCoordinator:
  """Debounces lane requests, caps them per maneuver, and never requests while a side is vetoed."""
  COOLDOWN_NS = 750_000_000

  def __init__(self, *, max_changes: int = MAX_LANE_CHANGES_PER_EVENT) -> None:
    self.max_changes = max_changes
    self._event_key = None
    self._changes = 0
    self._last_request_ns = 0
    self._request_id = 0
    self._target = -1

  def reset(self) -> None:
    self._event_key = None
    self._changes = 0
    self._last_request_ns = 0
    self._request_id = 0
    self._target = -1

  def update(self, nav: dict, plan: LanePlan, *, now_ns: int, topology_valid: bool,
             left_blocked: bool, right_blocked: bool, blindspot_left: bool, blindspot_right: bool,
             lateral_active: bool) -> dict:
    event_key = (str(nav.get("sessionId", "")), int(nav.get("routeRevision", 0)),
                 int(nav.get("maneuverEventId", 0)))
    if event_key != self._event_key:
      self._event_key = event_key
      self._changes = 0
      self._target = -1
    base = {"valid": bool(nav.get("valid")), "sessionId": event_key[0], "routeRevision": event_key[1],
            "maneuverEventId": event_key[2], "signalRequested": False, "direction": "none",
            "targetLaneIndex": -1, "spLaneChangeReady": False, "reason": plan.reason,
            "requestId": self._request_id, "laneChangeAllowed": False,
            "forkNow": False, "allowUnknownCrossing": False, "ignoreSolidBoundary": False}
    if not nav.get("valid") or plan.direction == "none" or plan.target_lane < 0:
      return base
    side_blocked = left_blocked if plan.direction == "left" else right_blocked
    side_blindspot = blindspot_left if plan.direction == "left" else blindspot_right
    if side_blocked:
      return base | {"reason": "boundaryVeto"}
    if side_blindspot:
      return base | {"reason": "blindspot"}
    if not topology_valid:
      return base | {"reason": "topologyUnavailable"}
    if not lateral_active:
      return base | {"reason": "lateralInactive"}
    if plan.target_lane == self._target and self._request_id > 0:
      # Hold the same lamp through SP's own lane-change cycle; do not open a new request.
      return base | {"signalRequested": True, "direction": plan.direction,
                     "targetLaneIndex": plan.target_lane, "spLaneChangeReady": True,
                     "reason": plan.reason, "laneChangeAllowed": True}
    if self._changes >= self.max_changes:
      return base | {"reason": "changeLimit"}
    if now_ns - self._last_request_ns < self.COOLDOWN_NS:
      return base | {"reason": "cooldown"}
    # Only the lamp is requested here; SP's own DesireHelper/ALC still requires its physical-lamp feedback,
    # blind-spot check and configured automatic lane-change mode before any lateral motion.
    self._request_id += 1
    self._last_request_ns = now_ns
    self._changes += 1
    self._target = plan.target_lane
    return base | {"signalRequested": True, "direction": plan.direction, "targetLaneIndex": plan.target_lane,
                   "spLaneChangeReady": True, "reason": plan.reason, "requestId": self._request_id,
                   "laneChangeAllowed": True}

  def note_completed(self) -> None:
    if self._event_key is not None:
      self._changes = min(self._changes + 1, self.max_changes + 1)


def build_intent(nav: dict, topology: dict, *, settings, v_ego: float, now_ns: int,
                 left_blocked: bool, right_blocked: bool, blindspot_left: bool, blindspot_right: bool,
                 lateral_active: bool, turns: TurnSignalCoordinator, lanes: LaneIntentCoordinator) -> dict:
  """Align to the maneuver-side lane first; request the turn lamp once no lane change is needed."""
  out = {}
  plan = lane_plan(nav, topology, settings, v_ego=v_ego)
  if plan.direction != "none" and plan.target_lane >= 0:
    out = lanes.update(nav, plan, now_ns=now_ns, topology_valid=bool(topology.get("valid")),
                       left_blocked=left_blocked, right_blocked=right_blocked,
                       blindspot_left=blindspot_left, blindspot_right=blindspot_right,
                       lateral_active=lateral_active)
    if out.get("signalRequested"):
      return out
  turn = turns.update(nav, speed_mps=v_ego, now_ns=now_ns,
                      lookahead_time_s=float(settings.signal_lead_time_s))
  if turn.get("signalRequested"):
    return turn | {"valid": bool(nav.get("valid")), "sessionId": str(nav.get("sessionId", "")),
                   "routeRevision": int(nav.get("routeRevision", 0)),
                   "maneuverEventId": int(nav.get("maneuverEventId", 0)),
                   "spLaneChangeReady": False, "laneChangeAllowed": False}
  return out | {"reason": plan.reason}
