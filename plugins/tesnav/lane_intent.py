from __future__ import annotations
import math
import secrets
from dataclasses import replace
from .lane_types import *
from .lane_state import LaneCoordinatorState
from .turn_signal import NavTurnSignalCoordinator

class NavLaneIntentCoordinator(LaneCoordinatorState):
  def update(self, plan: NavLanePlan, topology: LaneTopologyInput, vehicle: LaneVehicleInput,
             *, now_ns: int, allow_new_lane_change: bool = True, spoken_announcement_id: str = "") -> NavLaneIntent:
    plan = self.hold_active_fork(plan, topology)
    if self.fork_active and self._candidate[0] == self._event_key(plan) and plan.valid:
      # The distance window qualifies a new action, not an already selected fork.
      allow_new_lane_change = True
    if self._terminal_event_key is not None:
      session, _revision, event = self._terminal_event_key
      final_retry = bool(
        plan.force_fork and not self._terminal_fork
        and self._terminal_reason in ("laneChangeCancelled", "laneChangeCompletionUnconfirmed")
        and not self._terminal_driver_cancelled
        and self._event_key(plan) == self._terminal_event_key
      )
      # A source gap (including event=0) is not a new manoeuvre. Revision
      # changes alone must not revive a cancelled or unconfirmed action.
      if final_retry:
        self._terminal_event_key = None
        self._terminal_reason = ""
        self._terminal_fork = False
        self._terminal_driver_cancelled = False
        self._blocked_key = None
      elif (not (plan.valid or (self._terminal_fork and plan.navigation_valid))
          or not plan.session_id or plan.maneuver_event_id == 0
          or (plan.session_id == session and plan.maneuver_event_id == event)):
        return self._idle(self._terminal_reason)
      else:
        self._terminal_event_key = None
        self._terminal_reason = ""
        self._terminal_fork = False
        self._terminal_driver_cancelled = False
    if (self._candidate is not None and self._phase in ("signaling", "ready", "observing", "changing")
        and vehicle.lane_change_state == ObservedLaneChangeState.finishing
        and vehicle.lane_change_direction == LaneIntentDirection.none):
      # Preserve an explicit driver cancellation, while allowing an approach
      # stopped by changing boundary/safety evidence to retry once in jihui's
      # separate final-fork window with every current gate checked again.
      return self._abort(
        self._candidate[0], "laneChangeCancelled",
        driver_cancelled=vehicle.steering_pressed or vehicle.brake_pressed,
      )
    if self._phase in ("signaling", "ready", "observing") and self._candidate is not None:
      _event_key, start_ego, direction, _target_index, _relative_edge = self._candidate
      if (plan.force_fork and self._event_key(plan) == _event_key
          and vehicle.lane_change_state in (ObservedLaneChangeState.off, ObservedLaneChangeState.pre)):
        self._candidate_fork = True
        self._candidate = (*self._candidate[:4], True)
      if self.announcement_id:
        neighbor = topology.left_neighbor_exists if direction == LaneIntentDirection.left else topology.right_neighbor_exists
        if vehicle.steering_pressed or (neighbor is not True and not plan.force_fork):
          return self._abort(_event_key, "laneChangeAnnouncementFailed")
      if vehicle.lane_change_state == ObservedLaneChangeState.starting and vehicle.lane_change_direction == direction:
        if self.require_announcement and not self._announcement_confirmed:
          return self._abort(_event_key, "laneChangeAnnouncementFailed")
        # Observe the SP transition before deciding whether a new action may
        # start: reaching the turn window must not cut an action already begun.
        self._phase = "changing"
        self._phase_since_ns = now_ns
        self._expected_lane_index = self._adjacent_target_index(start_ego, direction)
        self._completion_since_ns = 0
    base_healthy = bool(
      plan.valid and vehicle.lateral_active and not vehicle.brake_pressed
      and not ((self._phase == "changing" or plan.force_fork) and vehicle.steering_pressed)
      and math.isfinite(vehicle.speed_mps)
      and (self._phase == "changing" or vehicle.speed_mps >= self.MIN_SPEED_MPS
           or (plan.force_fork and vehicle.speed_mps > 0.0))
    )
    topology_healthy = bool(plan.force_fork or (
      topology.valid_for_control and plan.lane_count == topology.visible_lane_count
      and 0 <= topology.ego_lane_index < topology.visible_lane_count
    ))
    relative_direction = self._relative_direction(plan)
    if relative_direction != LaneIntentDirection.none and (not base_healthy or not topology_healthy):
      self._relative_consistency.update(
        (plan.session_id, plan.route_revision, plan.maneuver_event_id),
        direction="left" if relative_direction == LaneIntentDirection.left else "right",
        neighbor_exists=False, observation_valid=False, lane_change_active=False,
        steering_pressed=vehicle.steering_pressed, now_ns=now_ns,
      )
    if not base_healthy:
      if self._phase not in ("idle", "cooldown") and self._candidate is not None:
        return self._abort(
          self._candidate[0], "health",
          driver_cancelled=(self._phase == "changing" and (vehicle.steering_pressed or vehicle.brake_pressed)),
        )
      self._reset()
      return self._idle("health")

    if self._phase in ("signaling", "ready", "observing") and not allow_new_lane_change:
      if self.announcement_id:
        return self._abort(self._candidate[0], "laneChangeAnnouncementFailed")
      self._reset()
      return self._idle("turnApproachHandoff")

    if self._phase == "changing" and self._candidate is not None:
      if now_ns - self._phase_since_ns > self.LANE_CHANGE_TIMEOUT_NS:
        return self._abort(self._candidate[0], "laneChangeTimeout")

    if not topology_healthy:
      if self._phase in ("signaling", "ready", "observing") and self._candidate is not None:
        if self.announcement_id:
          return self._abort(self._candidate[0], "laneChangeAnnouncementFailed")
        if self._event_key(plan) != self._candidate[0]:
          return self._abort(self._candidate[0], "routeChanged")
        return self._observe_before_start(now_ns, recovering=False)
      if self._phase == "changing" and self._candidate is not None:
        self._completion_since_ns = 0
        event_key, _start_ego, direction, target_index, _relative_edge = self._candidate
        if self._event_key(plan) != event_key:
          return self._abort(event_key, "routeChanged")
        if (vehicle.lane_change_state in (ObservedLaneChangeState.pre, ObservedLaneChangeState.starting,
                                          ObservedLaneChangeState.finishing) and
            vehicle.lane_change_direction != direction):
          return self._abort(event_key, "directionMismatch")
        physical_signal_on = ((vehicle.left_blinker and not vehicle.right_blinker)
                              if direction == LaneIntentDirection.left else
                              (vehicle.right_blinker and not vehicle.left_blinker))
        if not physical_signal_on:
          return self._abort(event_key, "physicalSignalLost")
        self._observe_model_cycle(vehicle, direction)
        if _relative_edge:
          completed = self._relative_model_completion(vehicle, direction, event_key, now_ns)
          if completed is not None:
            return completed
        return NavLaneIntent(
          signal_requested=True,
          lane_change_ready=vehicle.lane_change_state == ObservedLaneChangeState.starting,
          direction=direction,
          request_id=self._request_id, target_lane_index=self._expected_lane_index,
          reason=self._plan_reason(plan, "topologyTransition"),
        )
      self._reset()
      return self._idle("health")

    relative_status = None
    if relative_direction != LaneIntentDirection.none:
      relative_neighbor = (topology.left_neighbor_exists if relative_direction == LaneIntentDirection.left
                           else topology.right_neighbor_exists)
      relative_status = self._relative_consistency.update(
        (plan.session_id, plan.route_revision, plan.maneuver_event_id),
        direction="left" if relative_direction == LaneIntentDirection.left else "right",
        neighbor_exists=relative_neighbor,
        observation_valid=topology_healthy,
        lane_change_active=vehicle.lane_change_state in (ObservedLaneChangeState.starting, ObservedLaneChangeState.finishing),
        steering_pressed=vehicle.steering_pressed,
        now_ns=now_ns,
      )

    if self._phase in ("signaling", "ready", "observing") and self._candidate is not None:
      event_key, _ego, direction, _target, _relative = self._candidate
      if self._event_key(plan) != event_key:
        return self._abort(event_key, "routeChanged")
      neighbor = topology.left_neighbor_exists if direction == LaneIntentDirection.left else topology.right_neighbor_exists
      if neighbor is False and not plan.force_fork:
        return self._abort(event_key, "noNeighbor")
      if (neighbor is None and not plan.force_fork) or self._phase == "observing":
        waiting = self._observe_before_start(
          now_ns, recovering=(neighbor is True or plan.force_fork) and not vehicle.steering_pressed,
        )
        if waiting is not None:
          return waiting

    if self._phase == "cooldown":
      if now_ns - self._phase_since_ns < self.COOLDOWN_NS:
        return self._idle("cooldown")
      relative_edge = bool(self._candidate is not None and self._candidate[4])
      if not relative_edge and topology.ego_lane_index != self._expected_lane_index:
        return self._abort(self._candidate[0] if self._candidate is not None else None, "laneChangeNotObserved")
      self._reset()
      return self._idle("laneChangeComplete")

    if self._phase == "changing" and self._candidate is not None:
      event_key, _start_ego, direction, target_index, relative_edge = self._candidate
      if self._event_key(plan) != event_key:
        return self._abort(event_key, "routeChanged")
      if (vehicle.lane_change_state in (ObservedLaneChangeState.pre, ObservedLaneChangeState.starting,
                                        ObservedLaneChangeState.finishing) and
          vehicle.lane_change_direction != direction):
        return self._abort(event_key, "directionMismatch")
      physical_signal_on = ((vehicle.left_blinker and not vehicle.right_blinker)
                            if direction == LaneIntentDirection.left else
                            (vehicle.right_blinker and not vehicle.left_blinker))
      if not physical_signal_on:
        return self._abort(event_key, "physicalSignalLost")
      self._observe_model_cycle(vehicle, direction)
      if relative_edge:
        completed = self._relative_model_completion(vehicle, direction, event_key, now_ns)
        if completed is not None:
          return completed
      model_cycle_complete = vehicle.lane_change_state in (
        ObservedLaneChangeState.off, ObservedLaneChangeState.pre,
      )
      if topology.ego_lane_index == self._expected_lane_index and model_cycle_complete:
        if self._completion_since_ns == 0:
          self._completion_since_ns = now_ns
        elif now_ns - self._completion_since_ns >= self.LANE_INDEX_STABLE_NS:
          self._phase = "cooldown"
          self._phase_since_ns = now_ns
          return self._idle("laneChangeObserved")
      else:
        self._completion_since_ns = 0
      return NavLaneIntent(
        signal_requested=True,
        lane_change_ready=vehicle.lane_change_state == ObservedLaneChangeState.starting,
        direction=direction,
        request_id=self._request_id,
        target_lane_index=self._expected_lane_index,
        reason=self._plan_reason(plan, self._phase),
      )

    # jihui CP resets fork_now to its own one-shot allowance. Approach changes
    # keep their limit; the existing forkCompleted terminal state consumes fork.
    if (not plan.force_fork and relative_status is not None
        and relative_status.completed_changes >= self._relative_consistency.max_changes):
      self._reset()
      return self._idle(self._plan_reason(plan, "changeLimit"))

    if not allow_new_lane_change:
      self._reset()
      return self._idle("turnApproachHandoff")

    target_index = self._target(plan, topology.ego_lane_index)
    if target_index is None:
      self._reset()
      return self._idle("noRecommendedLane")
    direction = relative_direction if relative_direction != LaneIntentDirection.none else self._direction(topology.ego_lane_index, target_index)
    relative_cycle_active = vehicle.lane_change_state in (ObservedLaneChangeState.starting, ObservedLaneChangeState.finishing)
    if (relative_status is not None and not relative_status.ready and not plan.force_fork
        and self._phase != "changing" and not relative_cycle_active):
      self._blocked_key = None
      self._reset()
      return self._idle(self._plan_reason(plan, relative_status.reason))
    if direction == LaneIntentDirection.none:
      self._blocked_key = None
      self._reset()
      return self._idle("alreadyInRecommendedLane")

    event_key = self._event_key(plan)
    if self._blocked_key == event_key:
      return self._idle("blockedEvent")

    neighbor_exists = topology.left_neighbor_exists if direction == LaneIntentDirection.left else topology.right_neighbor_exists
    if neighbor_exists is None and not plan.force_fork:
      self._reset()
      return self._idle("neighborUnknown")
    if not neighbor_exists and not plan.force_fork:
      return self._abort(event_key, "noNeighbor")

    candidate = (event_key, topology.ego_lane_index, direction, target_index, plan.heuristic)
    if self.fork_active and self._candidate[0] == event_key:
      # A relative fork keeps its identity when the visible lane window recenters.
      candidate = self._candidate
    if self._phase == "idle":
      if candidate != self._candidate:
        self._candidate = candidate
        self._candidate_since_ns = now_ns
        if not plan.force_fork:
          return self._idle(self._plan_reason(plan, "stabilizingLaneAlignment"))
      if not plan.force_fork and now_ns - self._candidate_since_ns < self.MISMATCH_STABLE_NS:
        return self._idle(self._plan_reason(plan, "stabilizingLaneAlignment"))
      # Navigation owns lamp timing. OEM permission, BSM and the optional
      # visual boundary veto qualify readiness below; they do not suppress the
      # physical intent lamp while the vehicle waits for a safe opening.
      self._phase = "signaling"
      self._candidate_fork = plan.force_fork
      self._phase_since_ns = now_ns
      self._signal_since_ns = now_ns
      self._request_id += 1

    if self._candidate != candidate and self._phase != "cooldown":
      self._reset()
      self._candidate = candidate
      self._candidate_since_ns = now_ns
      return self._idle("routeChanged")

    if self._phase in ("ready", "changing") and now_ns - self._phase_since_ns > self.LANE_CHANGE_TIMEOUT_NS:
      return self._abort(event_key, "laneChangeTimeout")

    if vehicle.lane_change_state in (ObservedLaneChangeState.starting, ObservedLaneChangeState.finishing):
      if self.require_announcement and not self._announcement_confirmed:
        return self._abort(event_key, "laneChangeAnnouncementFailed")
      if vehicle.lane_change_direction != direction:
        return self._abort(event_key, "directionMismatch")
      if vehicle.lane_change_state == ObservedLaneChangeState.starting:
        self._phase = "changing"
        self._phase_since_ns = now_ns
        self._expected_lane_index = self._adjacent_target_index(topology.ego_lane_index, direction)
        self._completion_since_ns = 0
      else:
        # A first observed finishing frame cannot establish a successful start.
        # Relative plans require the complete normal lifecycle above.
        self._phase = "changing"

    crossing_allowed = topology.left_crossing_allowed if direction == LaneIntentDirection.left else topology.right_crossing_allowed
    blindspot = vehicle.left_blindspot if direction == LaneIntentDirection.left else vehicle.right_blindspot
    physical_signal_on = ((vehicle.left_blinker and not vehicle.right_blinker)
                          if direction == LaneIntentDirection.left else
                          (vehicle.right_blinker and not vehicle.left_blinker))
    if self.announcement_id and self._phase in ("signaling", "ready"):
      if (not crossing_allowed or blindspot or not physical_signal_on or vehicle.steering_pressed
          or (not self._announcement_confirmed and now_ns - self._announcement_since_ns > 8_000_000_000)):
        return self._abort(event_key, "laneChangeAnnouncementFailed")
      if spoken_announcement_id == self.announcement_id:
        self._announcement_confirmed = True
    if self._phase == "signaling":
      if now_ns - self._signal_since_ns > self.SIGNAL_WAIT_TIMEOUT_NS:
        return self._abort(event_key, "crossingWaitTimeout")
      if crossing_allowed and not blindspot and physical_signal_on:
        if self.require_announcement and not self._announcement_confirmed:
          if not self.announcement_id:
            self.announcement_id = secrets.token_hex(16)
            self._announcement_since_ns = now_ns
        else:
          self._phase = "ready"
          self._phase_since_ns = now_ns
    elif self._phase == "ready" and (not crossing_allowed or blindspot or not physical_signal_on):
      self._phase = "signaling"

    ready = self._phase in ("ready", "changing")
    return NavLaneIntent(
      signal_requested=True,
      lane_change_ready=ready,
      direction=direction,
      request_id=self._request_id,
      target_lane_index=self._adjacent_target_index(topology.ego_lane_index, direction),
      reason=self._plan_reason(plan, self._phase),
    )
