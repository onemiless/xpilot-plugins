from __future__ import annotations

import math
import time
from types import SimpleNamespace

from openpilot.tesla import plugins

from . import TRAFFIC_SIGNAL_CONTROL_PARAM
from .arbitrator_start import StartPlanMixin
from .arbitrator_stop import StopPlanMixin
from .arbitrator_types import (  # noqa: F401  re-exported public names
  MOVING_GREEN_SPEED,
  PLANNER_TRAFFIC_STALE_NS,
  START_JERK_LIMIT,
  START_MAX_ACCEL,
  START_MAX_DURATION_NS,
  START_MAX_SPEED,
  START_NEAR_LEAD_DISTANCE,
  STOP_CONTROL_PHASES,
  TERMINAL_MAX_SPEED,
  TrafficDiagnosticsMixin,
  TrafficPlanAction,
  TrafficPlanDiagnostics,
  TrafficStartBlockReason,
)
from .controller import (
  STOP_EVIDENCE_LOSS_GRACE_S,
  TrafficControlMode,
  TrafficControlPhase,
)
from .stop_profile import StopProfileGenerator


class _TrafficPlanPublishSink:
  def __init__(self, pm, arbitrator: FinalPlanArbitrator, sm, now_ns: int) -> None:
    self._pm = pm
    self._arbitrator = arbitrator
    self._sm = sm
    self._now_ns = now_ns

  def __getattr__(self, name):
    return getattr(self._pm, name)

  def send(self, service: str, message) -> None:
    if service == "longitudinalPlan":
      self._arbitrator.apply(message.longitudinalPlan, self._sm, self._now_ns)
    elif service == "longitudinalPlanSP":
      self._arbitrator.annotate_plan_sp(message.longitudinalPlanSP)
    self._pm.send(service, message)


class FinalPlanArbitrator(TrafficDiagnosticsMixin, StopPlanMixin, StartPlanMixin):
  """Explicit post-planner Traffic constraint; never wraps or mutates a planner backend."""

  def __init__(self, CP) -> None:
    self.msgs = plugins.PluginMsgs()
    self._actuator_delay = float(CP.longitudinalActuatorDelay)
    self._profile = StopProfileGenerator(
      actuator_delay=self._actuator_delay,
      release_jerk_limit=START_JERK_LIMIT,
    )
    self._held_event_id = 0
    self._held_session_id = 0
    self._seen_stop_session_id = 0
    self._owned_stop_session_id = 0
    self._active_start_session_id = 0
    self._completed_start_session_id = 0
    self._start_started_ns = 0
    self._reset_lead_gate()
    self._was_stopping = False
    self._hold_latched = False
    self._hold_latched_should_stop = False
    self._armed_stop_session_id = 0
    self._rejected_stop_session_id = 0
    self._traffic_service_gap = False
    self.diagnostics = TrafficPlanDiagnostics()

  def _reset_lead_gate(self) -> None:
    self._lead_delegated_session_id = 0
    self._lead_candidate_session_id = 0
    self._lead_candidate_since_ns = 0
    self._lead_candidate_last_ns = 0
    self._near_lead_blocked_session_id = 0
    self._lead_clear_since_ns = 0
    self._lead_clear_last_ns = 0

  def _reset_control_state(self) -> None:
    self._held_event_id = 0
    self._held_session_id = 0
    self._seen_stop_session_id = 0
    self._owned_stop_session_id = 0
    self._active_start_session_id = 0
    self._completed_start_session_id = 0
    self._start_started_ns = 0
    self._reset_lead_gate()
    self._was_stopping = False
    self._hold_latched = False
    self._hold_latched_should_stop = False
    self._armed_stop_session_id = 0
    self._rejected_stop_session_id = 0
    self._profile.reset()

  @staticmethod
  def _plan_snapshot(plan):
    return (
      tuple(float(value) for value in plan.speeds),
      tuple(float(value) for value in plan.accels),
      tuple(float(value) for value in plan.jerks),
      float(plan.aTarget), bool(plan.shouldStop), bool(plan.allowThrottle),
    )

  @staticmethod
  def _plan_changed(before, plan) -> bool:
    after = FinalPlanArbitrator._plan_snapshot(plan)
    for previous, current in zip(before[:3], after[:3], strict=True):
      if len(previous) != len(current) or any(abs(a - b) > 1e-6 for a, b in zip(previous, current, strict=True)):
        return True
    return bool(
      abs(before[3] - after[3]) > 1e-6
      or before[4] != after[4]
      or before[5] != after[5]
    )

  @staticmethod
  def _actuation_changed(before, plan) -> bool:
    # controlsd feeds only aTarget and shouldStop into LongControl. Future
    # trajectory arrays express a planned constraint, not current actuator
    # ownership, and allowThrottle is only consumed by onroad rendering.
    return bool(
      abs(before[3] - float(plan.aTarget)) > 1e-3
      or before[4] != bool(plan.shouldStop)
    )

  def _finalize_diagnostics(self, before, plan) -> None:
    self.diagnostics.final_a_target = float(plan.aTarget)
    self.diagnostics.should_stop = bool(plan.shouldStop)
    if self.diagnostics.action == TrafficPlanAction.none:
      return
    planned = self._plan_changed(before, plan)
    if not planned:
      self.diagnostics.action = TrafficPlanAction.none
    self.diagnostics.applied = bool(planned and self._actuation_changed(before, plan))
    if not self.diagnostics.applied:
      self.diagnostics.start_applied = False

  def publisher(self, pm, sm, now_ns: int | None = None):
    return _TrafficPlanPublishSink(pm, self, sm, time.monotonic_ns() if now_ns is None else now_ns)

  @staticmethod
  def _healthy(sm, service: str) -> bool:
    return bool(sm.seen[service] and sm.alive[service] and sm.valid[service])

  def _traffic(self, sm, now_ns: int):
    payload = self.msgs.latest("traffic.target", "traffic_light")
    if payload is None or not payload["valid"]:
      return None
    traffic = SimpleNamespace(**payload)
    age_ns = now_ns - int(traffic.publishMonoTime)
    return traffic if 0 <= age_ns <= PLANNER_TRAFFIC_STALE_NS else None

  @staticmethod
  def _driver_allows_stop(sm) -> bool:
    car_state = sm["carState"]
    car_control = sm["carControl"]
    return bool(
      car_control.enabled and car_control.longActive
      and not car_state.gasPressed and not car_state.brakePressed
    )

  @classmethod
  def _driver_allows_start(cls, sm) -> bool:
    return cls._driver_allows_stop(sm)


  def apply(self, plan, sm, now_ns: int | None = None) -> None:
    now_ns = time.monotonic_ns() if now_ns is None else now_ns
    base_snapshot = self._plan_snapshot(plan)
    base_a_target = float(plan.aTarget)
    self.diagnostics = TrafficPlanDiagnostics(base_a_target=base_a_target, final_a_target=base_a_target)
    traffic = self._traffic(sm, now_ns)
    self._set_diagnostics_from_traffic(traffic)

    if traffic is None:
      self._traffic_service_gap = True
    elif self._traffic_service_gap:
      # Preserve a latched terminal STOP while the publisher is absent, but
      # never let its executor/session identity leak through recovery. The
      # producer may have restarted and reused numeric event/session IDs.
      recovery_v_ego = float(sm["carState"].vEgo)
      preserve_terminal_latch = bool(
        self._hold_latched
        and math.isfinite(recovery_v_ego)
        and recovery_v_ego <= TERMINAL_MAX_SPEED
        and int(traffic.phase) in STOP_CONTROL_PHASES
      )
      preserve_should_stop = self._hold_latched_should_stop
      self._reset_control_state()
      if preserve_terminal_latch:
        # This is deliberately identity-free. It preserves only the minimum
        # low-speed braking contract until a valid STOP can be re-established;
        # it cannot make a restarted producer's reused session look owned.
        self._hold_latched = True
        self._hold_latched_should_stop = preserve_should_stop
      self._traffic_service_gap = False

    if traffic is not None and int(traffic.mode) not in (
      int(TrafficControlMode.stopOnly), int(TrafficControlMode.stopGo),
    ):
      self._reset_control_state()
      self.diagnostics.final_a_target = float(plan.aTarget)
      self.diagnostics.should_stop = bool(plan.shouldStop)
      return

    event_passed = bool(
      traffic is not None and int(traffic.phase) == int(TrafficControlPhase.passed)
    )
    if event_passed:
      self._reset_control_state()
      self.diagnostics.final_a_target = float(plan.aTarget)
      self.diagnostics.should_stop = bool(plan.shouldStop)
      return

    driver_allows_stop = self._driver_allows_stop(sm)
    signal_release = bool(
      traffic is not None and int(traffic.stopSessionId) > 0
      and int(traffic.phase) == int(TrafficControlPhase.release) and int(traffic.lightState) == 2
    )
    confirmed_release = bool(signal_release and int(traffic.stopSessionId) == self._held_session_id)
    if confirmed_release:
      self._hold_latched = False
      self._hold_latched_should_stop = False
    elif not driver_allows_stop:
      self._hold_latched = False
      self._hold_latched_should_stop = False
      if self._active_start_session_id != 0:
        self._finish_start(self._active_start_session_id)
      self._was_stopping = False
      self._armed_stop_session_id = 0
      self._profile.reset()
      self.diagnostics.final_a_target = float(plan.aTarget)
      self.diagnostics.should_stop = bool(plan.shouldStop)
      return

    motion_values = [float(sm["carState"].vEgo), float(sm["carState"].aEgo)]
    if traffic is not None:
      motion_values.append(float(traffic.distanceToStopPoint))
    if not all(math.isfinite(value) for value in motion_values):
      if signal_release:
        self._was_stopping = False
        self._armed_stop_session_id = 0
        self._profile.reset()
      else:
        self._apply_invalid_motion_fallback(plan)
      self._finalize_diagnostics(base_snapshot, plan)
      return
    trackable_stop = bool(
      traffic is not None and traffic.targetPresent and float(traffic.confidence) >= 0.9
      and int(traffic.phase) in STOP_CONTROL_PHASES
    )
    if trackable_stop:
      session_id = int(traffic.stopSessionId)
      self._seen_stop_session_id = int(traffic.stopSessionId)
      if self._armed_stop_session_id not in (0, session_id):
        self._armed_stop_session_id = 0
      if self._rejected_stop_session_id not in (0, session_id):
        self._rejected_stop_session_id = 0
      decision_pending = session_id not in (self._armed_stop_session_id, self._rejected_stop_session_id)
      if decision_pending:
        phase = TrafficControlPhase(int(traffic.phase))
        remaining_distance = float(traffic.distanceToStopPoint)
        inside_horizon = bool(
          phase == TrafficControlPhase.hold
          or remaining_distance <= self._traffic_activation_distance(
            sm, yellow_admission=phase in (TrafficControlPhase.yellowStop, TrafficControlPhase.flashingGreenStop),
          )
        )
        if inside_horizon:
          feasible = self._traffic_stop_feasible(sm, remaining_distance, phase)
          if feasible and traffic.stopControlAllowed:
            self._armed_stop_session_id = session_id
          elif not feasible:
            # Make the ownership decision once. An initially impossible or
            # uncomfortable yellow event cannot become a surprise terminal
            # catch only because the base planner slowed the vehicle later.
            self._rejected_stop_session_id = session_id
    elif traffic is None or int(traffic.stopSessionId) != self._armed_stop_session_id:
      # Preserve an ownership decision across a transient target/phase/
      # confidence gap only while the publisher still proves the same stop
      # session. Service loss, a new session, or a cleared session cannot
      # inherit the old decision (session counters may restart after a crash).
      self._armed_stop_session_id = 0
    stale_armed_grace = bool(
      trackable_stop and int(traffic.stopSessionId) == self._armed_stop_session_id
      and traffic.stopSafetyAllowed and not traffic.rawObservationFresh
      and 0.0 <= float(traffic.observationAgeMs) <= STOP_EVIDENCE_LOSS_GRACE_S * 1000.0
      and float(traffic.rawDistance) < 255.0
    )
    active_stop = bool(
      trackable_stop and int(traffic.stopSessionId) == self._armed_stop_session_id
      and (traffic.stopControlAllowed or stale_armed_grace) and driver_allows_stop
    )
    if trackable_stop:
      self._update_go_lead_gate(plan, sm, int(traffic.stopSessionId), now_ns)
    queue_follow = bool(active_stop and self._queue_follow_owns_motion(plan, sm, traffic, now_ns))
    if active_stop and not queue_follow:
      self._apply_stop(plan, sm, traffic, now_ns)
    elif queue_follow:
      # A currently confirmed close queue lead owns motion while the vehicle
      # remains outside the personality-aware stop-line braking guard. Keep the
      # Traffic session armed so its normal STOP resumes before the guard is
      # consumed, without a hard ownership switch at 0.3 m/s.
      self._was_stopping = False
      self._profile.reset()
    elif traffic is not None and bool(traffic.plannerStartRequested) and int(traffic.lightState) == 2:
      start_session_id = int(traffic.stopSessionId)
      continuing_start = bool(
        start_session_id > 0 and self._active_start_session_id == start_session_id
      )
      # The moving threshold separates a newly observed rolling green from a
      # standstill GO; an active same-session GO retains its 2.5 m/s / 3 s bounds.
      if float(sm["carState"].vEgo) > MOVING_GREEN_SPEED and not continuing_start:
        self.diagnostics.start_requested = True
        if self._go_lead_blocked(plan, sm, start_session_id, now_ns):
          self.diagnostics.start_block_reason = TrafficStartBlockReason.physicalLead
        self._finish_start(start_session_id)
        self._hold_latched = False
        self._hold_latched_should_stop = False
        self._was_stopping = False
        self._armed_stop_session_id = 0
        self._profile.reset()
      elif not self._apply_start(plan, sm, traffic, now_ns):
        self._profile.reset()
        self._was_stopping = False
    elif signal_release:
      self._hold_latched = False
      self._hold_latched_should_stop = False
      self._was_stopping = False
      self._armed_stop_session_id = 0
      self._profile.reset()
    elif (self._hold_latched and float(sm["carState"].vEgo) <= TERMINAL_MAX_SPEED
          and driver_allows_stop):
      self._apply_latched_hold(plan, sm)
    else:
      if self._active_start_session_id != 0:
        self._finish_start(self._active_start_session_id)
      self._apply_release(plan, sm)

    self._finalize_diagnostics(base_snapshot, plan)



def create_final_plan_arbitrator(CP, params) -> FinalPlanArbitrator | None:
  if CP.brand != "tesla" or not params.get_bool(TRAFFIC_SIGNAL_CONTROL_PARAM):
    return None
  return FinalPlanArbitrator(CP)
