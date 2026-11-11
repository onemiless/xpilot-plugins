from __future__ import annotations
import math
import secrets
from dataclasses import replace
from .lane_types import *

class NavTurnSignalCoordinator:
  """Requests a physical lamp for a route turn without authorizing a lane change."""

  SIGNAL_TIMEOUT_NS = 60_000_000_000
  PLAN_GAP_GRACE_NS = 1_500_000_000
  SOURCE_GAP_GRACE_NS = 4_000_000_000  # 2 s SDK update + 1 s source recovery + delivery margin.
  LOOKAHEAD_TIME_S = 8.0
  MIN_LOOKAHEAD_M = 40.0
  MAX_LOOKAHEAD_M = 250.0
  LOOKAHEAD_MARGIN_M = 20.0
  TURN_CLEAR_STABLE_NS = 500_000_000
  MANEUVER_DIRECTIONS = {
    "slightLeft": LaneIntentDirection.left,
    "turnLeft": LaneIntentDirection.left,
    "sharpLeft": LaneIntentDirection.left,
    "uTurnLeft": LaneIntentDirection.left,
    "exitLeft": LaneIntentDirection.left,
    "rampLeft": LaneIntentDirection.left,
    "mergeLeft": LaneIntentDirection.left,
    "slightRight": LaneIntentDirection.right,
    "turnRight": LaneIntentDirection.right,
    "sharpRight": LaneIntentDirection.right,
    "uTurnRight": LaneIntentDirection.right,
    "exitRight": LaneIntentDirection.right,
    "rampRight": LaneIntentDirection.right,
    "mergeRight": LaneIntentDirection.right,
  }

  def __init__(self) -> None:
    self._active_key: tuple[str, int, int] | None = None
    self._active_direction = LaneIntentDirection.none
    self._active_since_ns = 0
    self._plan_gap_since_ns: int | None = None
    self._completion_hold = False
    self._turn_clear_since_ns = 0

  def _reset(self, reason: str = "idle") -> NavLaneIntent:
    self._active_key = None
    self._active_direction = LaneIntentDirection.none
    self._active_since_ns = 0
    self._plan_gap_since_ns = None
    self._completion_hold = False
    self._turn_clear_since_ns = 0
    return NavLaneIntent(reason=reason)

  def update(self, plan: NavTurnPlan, *, speed_mps: float, now_ns: int,
             turn_geometry_active: bool = False, lookahead_time_s: float | None = None) -> NavLaneIntent:
    direction = self.MANEUVER_DIRECTIONS.get(plan.maneuver, LaneIntentDirection.none)
    event_key = (plan.session_id, plan.route_revision, plan.maneuver_event_id)

    if self._active_key is not None:
      same_active_event = event_key == self._active_key and direction == self._active_direction
      if plan.valid and same_active_event and plan.turn_signal_hold:
        # A fresh direction-matched red countdown or the vehicle's measured
        # standstill keeps this same navigation turn session alive.
        self._active_since_ns = now_ns
      elif now_ns - self._active_since_ns > self.SIGNAL_TIMEOUT_NS:
        return self._reset("turnSignalTimeout")
      same_route_direction = ((plan.session_id, plan.route_revision) == self._active_key[:2]
                              and direction == self._active_direction)
      source_gap = bool(plan.source_interrupted and plan.maneuver_event_id == 0 and same_route_direction)
      if not plan.valid and plan.maneuver_event_id == 0 and not source_gap:
        return self._reset("turnUnavailable")
      if not plan.valid and (event_key == self._active_key or source_gap):
        if self._plan_gap_since_ns is None:
          self._plan_gap_since_ns = now_ns
        grace_ns = self.SOURCE_GAP_GRACE_NS if source_gap else self.PLAN_GAP_GRACE_NS
        if now_ns - self._plan_gap_since_ns <= grace_ns:
          return NavLaneIntent(signal_requested=True, direction=self._active_direction,
                               request_id=self._active_key[2], target_lane_index=-1, reason="turnApproachGrace")
        return self._reset("turnUnavailable")
      self._plan_gap_since_ns = None
      if event_key != self._active_key or direction != self._active_direction:
        route_continuous = (plan.session_id, plan.route_revision) == self._active_key[:2]
        if not route_continuous:
          return self._reset("turnChanged")
        if turn_geometry_active:
          self._completion_hold = True
          self._turn_clear_since_ns = 0
        elif self._completion_hold:
          if self._turn_clear_since_ns == 0:
            self._turn_clear_since_ns = now_ns
          elif now_ns - self._turn_clear_since_ns > self.TURN_CLEAR_STABLE_NS:
            return self._reset("turnChanged")
        else:
          return self._reset("turnChanged")
        return NavLaneIntent(
          signal_requested=True,
          direction=self._active_direction,
          request_id=plan.maneuver_event_id,
          target_lane_index=-1,
          reason="turnCompletion",
        )
      self._completion_hold = False
      self._turn_clear_since_ns = 0
      if plan.valid:
        self._plan_gap_since_ns = None
        return NavLaneIntent(
          signal_requested=True,
          direction=self._active_direction,
          request_id=plan.maneuver_event_id,
          target_lane_index=-1,
          reason="turnApproach",
        )

    if not plan.valid or direction == LaneIntentDirection.none or plan.maneuver_event_id == 0:
      return self._reset("turnUnavailable")

    lookahead_m = max(self.MIN_LOOKAHEAD_M, min(
      self.MAX_LOOKAHEAD_M, max(0.0, speed_mps) * (self.LOOKAHEAD_TIME_S if lookahead_time_s is None else lookahead_time_s) + self.LOOKAHEAD_MARGIN_M,
    ))
    if not 0.0 < plan.distance_m <= lookahead_m:
      return self._reset("turnOutsideWindow")

    self._active_key = event_key
    self._active_direction = direction
    self._active_since_ns = now_ns
    self._plan_gap_since_ns = None
    return NavLaneIntent(
      signal_requested=True,
      direction=direction,
      request_id=plan.maneuver_event_id,
      target_lane_index=-1,
      reason="turnApproach",
    )
