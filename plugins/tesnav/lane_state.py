from __future__ import annotations
import math
import secrets
from dataclasses import replace
from .lane_types import *
from .relative_lane_consistency import RelativeLaneConsistencyFilter

class LaneCoordinatorState:
  MISMATCH_STABLE_NS = 500_000_000
  LANE_INDEX_STABLE_NS = 500_000_000
  TOPOLOGY_TRANSITION_GRACE_NS = 1_000_000_000
  SIGNAL_WAIT_TIMEOUT_NS = 60_000_000_000
  LANE_CHANGE_TIMEOUT_NS = 10_000_000_000
  COOLDOWN_NS = 750_000_000
  MIN_SPEED_MPS = 20 * 0.44704

  def __init__(self, *, max_changes: int = 5, require_announcement: bool = False) -> None:
    self.require_announcement = require_announcement
    self.announcement_id = ""
    self._announcement_since_ns = 0
    self._announcement_confirmed = False
    self._phase = "idle"
    self._candidate = None
    self._candidate_fork = False
    self._candidate_since_ns = 0
    self._phase_since_ns = 0
    self._signal_since_ns = 0
    self._blocked_key = None
    self._terminal_event_key = None
    self._terminal_reason = ""
    self._terminal_fork = False
    self._terminal_driver_cancelled = False
    self._request_id = 0
    self._expected_lane_index = -1
    self._completion_since_ns = 0
    self._normal_finish_seen = False
    self._starting_model_seen = False
    self._observation_since_ns: int | None = None
    self._observation_recovery_ns: int | None = None
    self._observation_lamp_released = False
    self._relative_consistency = RelativeLaneConsistencyFilter(max_changes=max_changes)

  def _idle(self, reason: str = "idle") -> NavLaneIntent:
    return NavLaneIntent(reason=reason)

  @property
  def fork_active(self) -> bool:
    return self._candidate_fork and self._phase in ("signaling", "ready", "observing", "changing")

  @property
  def fork_terminal(self) -> bool:
    return self._terminal_fork

  @staticmethod
  def _target(plan: NavLanePlan, ego_index: int) -> int | None:
    valid = tuple(index for index in plan.recommended_indices if 0 <= index < plan.lane_count)
    return min(valid, key=lambda index: (abs(index - ego_index), index)) if valid else None

  @staticmethod
  def _direction(ego_index: int, target_index: int) -> LaneIntentDirection:
    if target_index < ego_index:
      return LaneIntentDirection.left
    if target_index > ego_index:
      return LaneIntentDirection.right
    return LaneIntentDirection.none

  @staticmethod
  def _adjacent_target_index(ego_index: int, direction: LaneIntentDirection) -> int:
    return max(0, ego_index - 1) if direction == LaneIntentDirection.left else ego_index + 1

  @staticmethod
  def _event_key(plan: NavLanePlan) -> tuple[str, int, int]:
    # Visible lane indices recenter while crossing a boundary. They describe an
    # observation, not a new navigation event or a reason to cancel its lamp.
    return (plan.session_id, plan.route_revision, plan.maneuver_event_id)

  @staticmethod
  def _plan_reason(plan: NavLanePlan, reason: str) -> str:
    return f"heuristic{reason[0].upper()}{reason[1:]}" if plan.heuristic and reason else reason

  def _observe_model_cycle(self, vehicle: LaneVehicleInput, direction: LaneIntentDirection) -> None:
    if vehicle.lane_change_state == ObservedLaneChangeState.starting:
      self._starting_model_seen = True
    if (vehicle.lane_change_state == ObservedLaneChangeState.finishing
        and vehicle.lane_change_direction == direction and self._starting_model_seen):
      self._normal_finish_seen = True

  def _relative_model_completion(self, vehicle: LaneVehicleInput, direction: LaneIntentDirection,
                                 event_key, now_ns: int) -> NavLaneIntent | None:
    if vehicle.lane_change_state not in (ObservedLaneChangeState.pre, ObservedLaneChangeState.off):
      return None
    if (self._normal_finish_seen and vehicle.lane_change_state == ObservedLaneChangeState.pre
        and vehicle.lane_change_direction == direction):
      self._relative_consistency.note_lane_change_completed(now_ns)
      if self._candidate_fork:
        # jihui CP desire_helper: a completed fork_now consumes its one request.
        self._terminal_event_key = event_key
        self._terminal_reason = "forkCompleted"
        self._terminal_fork = True
        self._terminal_driver_cancelled = False
        self._reset()
        return self._idle("forkCompleted")
      self._phase = "cooldown"
      self._phase_since_ns = now_ns
      return self._idle("laneChangeObserved")
    return self._abort(event_key, "laneChangeCompletionUnconfirmed")

  @staticmethod
  def _relative_direction(plan: NavLanePlan) -> LaneIntentDirection:
    if not plan.heuristic:
      return LaneIntentDirection.none
    if plan.edge_direction != LaneIntentDirection.none:
      return plan.edge_direction
    if plan.lane_count > 1 and plan.recommended_indices == (0,):
      return LaneIntentDirection.left
    if plan.lane_count > 1 and plan.recommended_indices == (plan.lane_count - 1,):
      return LaneIntentDirection.right
    return LaneIntentDirection.none

  def _reset(self) -> None:
    self.announcement_id = ""
    self._announcement_since_ns = 0
    self._announcement_confirmed = False
    self._phase = "idle"
    self._candidate = None
    self._candidate_fork = False
    self._candidate_since_ns = 0
    self._phase_since_ns = 0
    self._signal_since_ns = 0
    self._expected_lane_index = -1
    self._completion_since_ns = 0
    self._normal_finish_seen = False
    self._starting_model_seen = False
    self._observation_since_ns = None
    self._observation_recovery_ns = None
    self._observation_lamp_released = False

  def _observe_before_start(self, now_ns: int, *, recovering: bool) -> NavLaneIntent | None:
    """Keep one request through a bounded gap, never its permission to start."""
    assert self._candidate is not None
    self._phase = "observing"
    if self._observation_since_ns is None:
      self._observation_since_ns = now_ns
    if not recovering:
      self._observation_recovery_ns = None
    elif self._observation_recovery_ns is None:
      self._observation_recovery_ns = now_ns
    if recovering and now_ns - self._observation_recovery_ns >= self.MISMATCH_STABLE_NS:
      self._phase = "signaling"
      self._phase_since_ns = now_ns
      if self._observation_lamp_released:
        self._signal_since_ns = now_ns
      self._observation_since_ns = self._observation_recovery_ns = None
      self._observation_lamp_released = False
      return None
    if not self._observation_lamp_released and now_ns - self._observation_since_ns > self.TOPOLOGY_TRANSITION_GRACE_NS:
      self._observation_lamp_released = True
      self._observation_recovery_ns = now_ns if recovering else None
    _event, _ego, direction, target, _relative = self._candidate
    return NavLaneIntent(
      signal_requested=not self._observation_lamp_released, direction=direction,
      request_id=self._request_id, target_lane_index=target,
      reason="neighborObservationPaused" if self._observation_lamp_released else "neighborObservationHold",
    )

  def _abort(self, event_key, reason: str, *, driver_cancelled: bool = False) -> NavLaneIntent:
    if self.announcement_id and self._phase in ("signaling", "ready", "observing"):
      reason = "laneChangeAnnouncementFailed"
    if self._phase == "changing" or reason in ("laneChangeCancelled", "laneChangeCompletionUnconfirmed", "laneChangeAnnouncementFailed"):
      self._terminal_event_key = event_key
      self._terminal_reason = ("laneChangeCompletionUnconfirmed" if self._phase == "changing"
                               and reason != "laneChangeCancelled" else reason)
      self._terminal_fork = self._candidate_fork
      self._terminal_driver_cancelled = driver_cancelled
    self._blocked_key = event_key
    self._reset()
    return self._idle(reason)

  def hold_active_fork(self, plan: NavLanePlan, topology: LaneTopologyInput) -> NavLanePlan:
    """Share the selected fork purpose with arbitration until model completion."""
    if self.fork_active and self._candidate[0] == self._event_key(plan):
      # Keep the selected fork purpose through distance jitter/zero while the
      # same valid navigation action completes, even if lane mapping now fails.
      # Source loss and all existing driver/control vetoes still apply below.
      fork_lane_count = max(1, topology.visible_lane_count)
      return replace(plan, valid=plan.valid or plan.navigation_valid, force_fork=True,
                     heuristic=self._candidate[4], edge_direction=self._candidate[2],
                     ignore_solid_boundary=True,
                     lane_count=fork_lane_count,
                     recommended_indices=(0 if self._candidate[2] == LaneIntentDirection.left
                                          else fork_lane_count - 1,))
    return plan
