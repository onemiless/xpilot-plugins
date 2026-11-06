"""Route-compatible overtaking decision; execution remains in the host."""
import math
from dataclasses import replace
from .control_settings import NavAssistSettings
from .lane_types import LaneIntentDirection, NavLanePlan, ObservedLaneChangeState
from .radar_geometry import lane_leads, side_lead_unsafe

class EfficiencyLaneSelector:
  """One adjacent change, persistent benefit, then a fresh evaluation after cooldown."""
  COOLDOWN_NS = 10_000_000_000
  FAILED_COOLDOWN_NS = 4_000_000_000

  def __init__(self):
    self.settings = NavAssistSettings()
    self.active = None
    self.started = False
    self.candidate = None
    self.since = 0
    self.cooldown_until = 0
    self.sequence = 0
    self.reason = 'efficiencyIdle'
    self.unconfirmed_session = None
    self.terminal_reason = 'efficiencyCompletionUnconfirmed'
    self.navigation_priority_key = None
    self.navigation_priority_until = 0
    self.navigation_goal_key = None

  def cancel(self, now_ns, reason):
    if self.active is not None:
      if self.started and reason not in ('efficiencyComplete', 'efficiencyCancelled'):
        self.unconfirmed_session = self.active.session_id
        self.terminal_reason = 'efficiencyCompletionUnconfirmed'
      self.cooldown_until = now_ns + self.settings.overtake_failed_cooldown_s * 1_000_000_000
    self.active = None
    self.started = False
    self.candidate = None
    self.reason = reason

  def select(self, route_plan, nav, topology, vehicle, radar, model, oem, *,
             healthy, enabled, cruise_mps, route_reserved, now_ns, settings=None, execution_healthy=None):
    if settings is not None:
      self.settings = settings
    policy = self.settings
    route_key = (str(nav.sessionId), int(nav.routeRevision))
    goal_key = (*route_key, int(getattr(nav, 'maneuverEventId', 0)),
                int(getattr(nav, 'currentStepIndex', -1)), str(getattr(nav, 'maneuver', 'none')))
    distance = float(getattr(nav, 'maneuverDistanceM', math.nan))
    goal_source_valid = (enabled and nav.valid and not nav.stale and nav.routeActive and nav.routeMatched
                         and str(nav.mode) == 'realtime' and goal_key[2] != 0
                         and math.isfinite(distance)
                         and 0. < distance <= max(policy.turn_lane_lookahead_m, policy.exit_lane_lookahead_m))
    if self.navigation_goal_key != goal_key or not goal_source_valid:
      self.navigation_goal_key = None
    if (goal_source_valid and route_plan.navigation_valid and route_plan.valid and route_plan.recommended_indices
        and (route_plan.edge_direction != LaneIntentDirection.none or route_reserved)):
      # Reaching the requested edge does not release the approaching maneuver.
      # Remember only the inhibition, not a lane index/plan or control permission:
      # hint/vision gaps must not create another action or revive a cancelled one.
      self.navigation_goal_key = goal_key
    goal_reserved = self.navigation_goal_key == goal_key
    if self.navigation_priority_key != route_key:
      self.navigation_priority_key = None
      self.navigation_priority_until = 0
    if (self.active is not None and vehicle.lane_change_state == ObservedLaneChangeState.starting
        and vehicle.lane_change_direction == self.active.edge_direction):
      self.started = True
    source_valid = (enabled and healthy and nav.valid and not nav.stale and nav.routeActive
             and nav.routeMatched and str(nav.mode) == 'realtime' and nav.roadClass in (0, 6)
             and vehicle.lateral_active
             and not (vehicle.brake_pressed or vehicle.steering_pressed)
             and math.isfinite(vehicle.speed_mps) and vehicle.speed_mps >= 60 / 3.6)
    valid = source_valid and topology.valid_for_control
    execution_valid = (enabled and (healthy if execution_healthy is None else execution_healthy)
                       and nav.valid and not nav.stale and nav.routeActive and nav.routeMatched
                       and str(nav.mode) == 'realtime' and vehicle.lateral_active
                       and not (vehicle.brake_pressed or vehicle.steering_pressed)
                       and math.isfinite(vehicle.speed_mps))
    # An interrupted attempt is not a completed change. Wait for the original
    # model and physical lamps to settle, then use the existing failure cooldown
    # and a fresh benefit evaluation (jihui finishOvertake(false)). A route/session
    # change alone cannot establish that the previous manoeuvre has stopped.
    if self.unconfirmed_session is not None:
      if (not valid or not execution_valid or not route_key[0]
          or vehicle.lane_change_state != ObservedLaneChangeState.off
          or vehicle.lane_change_direction != LaneIntentDirection.none
          or vehicle.left_blinker or vehicle.right_blinker):
        self.reason = self.terminal_reason
        return route_plan, False
      self.unconfirmed_session = None
    # Radar, road class, start speed and topology qualify a new request only.
    if (self.active is not None and self.started and execution_valid
        and (self.active.session_id, self.active.route_revision) == route_key):
      return replace(self.active, lane_count=topology.visible_lane_count), True
    if self.active is not None and self.started and not execution_valid:
      self.cancel(now_ns, 'efficiencyUnavailable')
      return route_plan, False
    if not valid:
      self.navigation_priority_key = None
      self.navigation_priority_until = 0
      self.cancel(now_ns, 'efficiencyUnavailable')
      return route_plan, False
    if self.active is not None:
      if (self.active.session_id, self.active.route_revision) != route_key:
        self.cancel(now_ns, 'efficiencyRouteChanged')
        return route_plan, False
      if self.started:
        # Do not replace an executing manoeuvre with a new navigation request.
        return replace(self.active, lane_count=topology.visible_lane_count), True
    if route_reserved:
      # jihui c523c47 NAVI_TAKEOVER_HOLD_MS: bridge short KEEP/guidance gaps.
      # This suppresses new overtake requests only; never reopens a navigation event.
      self.navigation_priority_key = route_key
      self.navigation_priority_until = now_ns + 3_000_000_000
    if goal_reserved or route_reserved or (self.navigation_priority_key == route_key and now_ns < self.navigation_priority_until):
      self.cancel(now_ns, 'efficiencyNavigationPriority')
      return route_plan, False
    if now_ns < self.cooldown_until:
      self.reason = 'efficiencyCooldown'
      return route_plan, False
    if not math.isfinite(cruise_mps) or cruise_mps <= 0:
      self.cancel(now_ns, 'efficiencyCruiseUnavailable')
      return route_plan, False
    # Each eligible side is associated separately, so its unrelated neighbor
    # cannot turn a known forward gap into an unknown one.
    active_direction = self.active.edge_direction if self.active is not None else LaneIntentDirection.none
    choices = []
    gains = {}
    observations = {}
    for lane, side, direction in ((0, 'left', LaneIntentDirection.left), (2, 'right', LaneIntentDirection.right)):
      if (active_direction not in (LaneIntentDirection.none, direction)
          or side == 'right' and not policy.overtake_allow_right):
        continue
      permitted = (not (oem.get('permissionValid', False) and oem.get(side + 'SafetyBlocked', False))
                   and getattr(topology, side + '_neighbor_exists') is True
                   and getattr(topology, side + '_crossing_allowed')
                   and not getattr(vehicle, side + '_blindspot'))
      if not permitted:
        continue
      targets = lane_leads(radar, model, left_neighbor=side == 'left', right_neighbor=side == 'right', all_targets=True)
      observations[direction] = targets
      if targets is None:
        continue
      current = min(targets[1], key=lambda point: point.dRel, default=None)
      if current is None:
        continue
      demand = (policy.overtake_min_distance_m <= current.dRel <= policy.overtake_max_distance_m
                and vehicle.speed_mps + current.vRel >= policy.overtake_lead_min_kph / 3.6
                and (current.vRel < -policy.overtake_closing_kph / 3.6
                     or current.dRel / vehicle.speed_mps <= policy.overtake_time_gap_tenths / 10
                     or vehicle.speed_mps < cruise_mps * policy.overtake_cruise_percent / 100))
      if not demand:
        continue
      lead = min(targets[lane], key=lambda point: point.dRel, default=None)
      # The nearest target sets expected speed; every measured side target must
      # pass the same CP forward-gap rule before requesting the original SP path.
      gap_ok = all(not side_lead_unsafe(point, vehicle.speed_mps) for point in targets[lane])
      target_speed = cruise_mps if lead is None else min(cruise_mps, vehicle.speed_mps + lead.vRel)
      gain = target_speed - min(cruise_mps, vehicle.speed_mps + current.vRel)
      if gap_ok and gain > 0:
        choices.append(direction)
        gains[direction] = gain
    direction = (choices[0] if policy.overtake_prefer_left else max(choices, key=gains.get)) if choices else LaneIntentDirection.none
    if self.active is not None:
      if self.active.edge_direction in choices:
        return self.active, True
      side = 'left' if active_direction == LaneIntentDirection.left else 'right'
      targets = observations.get(active_direction)
      side_targets = targets[0 if side == 'left' else 2] if targets is not None else []
      reason = ('efficiencyRightDisabled' if side == 'right' and not policy.overtake_allow_right
                else 'efficiencySafetyBlocked' if (oem.get('permissionValid', False) and oem.get(side + 'SafetyBlocked', False)) or getattr(vehicle, side + '_blindspot')
                else 'efficiencyCrossingLost' if not getattr(topology, side + '_crossing_allowed')
                else 'efficiencyNeighborLost' if getattr(topology, side + '_neighbor_exists') is not True
                else 'efficiencyLaneGeometryUnknown' if targets is None
                else 'efficiencyTargetUnknown' if not targets[1]
                else 'efficiencyGapLost' if any(side_lead_unsafe(point, vehicle.speed_mps) for point in side_targets)
                else 'efficiencyBenefitLost')
      self.cancel(now_ns, reason)
      return route_plan, False
    if direction == LaneIntentDirection.none:
      self.candidate = None
      self.reason = ('efficiencyLaneGeometryUnknown' if observations and all(value is None for value in observations.values()) else
                     'efficiencyNoConfirmedBenefit')
      return route_plan, False
    if vehicle.left_blinker or vehicle.right_blinker or vehicle.lane_change_state != ObservedLaneChangeState.off:
      self.candidate = None
      self.reason = 'efficiencyManualOrActiveChange'
      return route_plan, False
    # Confirm continuing benefit in the chosen direction, as in jihui's demand
    # timer. Radar track identity changes are not a loss of benefit or safety;
    # all current targets were checked above on this frame.
    key = (route_key, direction)
    if key != self.candidate:
      self.candidate, self.since = key, now_ns
    self.reason = 'efficiencyBenefitStabilizing'
    if now_ns - self.since < policy.overtake_stable_ms * 1_000_000:
      return route_plan, False
    self.sequence += 1
    target = 0 if direction == LaneIntentDirection.left else max(0, topology.visible_lane_count - 1)
    self.active = NavLanePlan(True, *route_key, (1 << 63) + self.sequence,
                              topology.visible_lane_count, (target,), heuristic=True, edge_direction=direction)
    self.reason = 'efficiencyRequest'
    return self.active, True

  def observe(self, intent, now_ns):
    if self.active is None:
      return
    if intent.reason == 'laneChangeCancelled':
      self.cancel(now_ns, 'efficiencyCancelled')
    elif intent.reason in ('laneChangeCompletionUnconfirmed', 'laneChangeAnnouncementFailed'):
      self.unconfirmed_session = self.active.session_id
      self.cancel(now_ns, 'efficiencyCompletionUnconfirmed')
      self.terminal_reason = ('efficiencyCancelled' if intent.reason == 'laneChangeCancelled'
                              else 'efficiencyAnnouncementFailed' if intent.reason == 'laneChangeAnnouncementFailed'
                              else 'efficiencyCompletionUnconfirmed')
      self.reason = self.terminal_reason
    elif intent.reason in ('laneChangeObserved', 'laneChangeComplete'):
      self.cancel(now_ns, 'efficiencyComplete')
      self.cooldown_until = now_ns + self.settings.overtake_success_cooldown_s * 1_000_000_000
    elif intent.reason in ('blockedEvent', 'health', 'noNeighbor', 'routeChanged',
                           'directionMismatch', 'physicalSignalLost', 'laneChangeTimeout', 'crossingWaitTimeout'):
      self.cancel(now_ns, 'efficiencyAborted:' + intent.reason)
