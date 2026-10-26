from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from .controller import TrafficControlPhase
from openpilot.tesla import plugins

# CP's low-speed cruise acceleration envelope. This applies only to a
# same-event CAN-authoritative green start and remains time/speed bounded.
START_MAX_ACCEL = 1.6
START_MAX_SPEED = 2.5
START_MAX_DURATION_NS = 3_000_000_000
START_JERK_LIMIT = 1.0
START_NEAR_LEAD_DISTANCE = 8.0
START_LEAD_CONFIRM_NS = 500_000_000
START_LEAD_CLEAR_NS = 400_000_000
START_LEAD_MAX_GAP_NS = 150_000_000
QUEUE_STOP_LINE_GUARD = 5.0
MOVING_GREEN_SPEED = 0.3
TERMINAL_MAX_SPEED = 1.5
TERMINAL_LOOKAHEAD_S = 0.05
PLANNER_TRAFFIC_STALE_NS = 350_000_000
MAX_TRAFFIC_STOP_BRAKE = 3.0
YELLOW_MARGIN_BASE = 1.0
YELLOW_MARGIN_TIME = 0.15
YELLOW_MARGIN_MIN = 2.0
YELLOW_MARGIN_MAX = 4.0
STOP_CONTROL_PHASES = (
  int(TrafficControlPhase.approachRed),
  int(TrafficControlPhase.braking),
  int(TrafficControlPhase.hold),
  int(TrafficControlPhase.flashingGreenStop),
  int(TrafficControlPhase.yellowStop),
)


@dataclass(frozen=True)
class _TrafficStopStyle:
  comfort_brake: float
  jerk_limit: float
  decel_margin: float


class TrafficPlanAction(IntEnum):
  none = 0
  stop = 1
  hold = 2
  start = 3
  release = 4
  rollingRelease = 5


class TrafficStartBlockReason(IntEnum):
  none = 0
  noPreviousHold = 1
  eventMismatch = 2
  unsafeBasePlan = 3
  modelStop = 4
  driverOverride = 5
  physicalLead = 6
  invalidCruise = 7
  alreadyStarted = 8


@dataclass
class TrafficPlanDiagnostics:
  mode: int = 0
  action: TrafficPlanAction = TrafficPlanAction.none
  applied: bool = False
  start_requested: bool = False
  start_applied: bool = False
  start_block_reason: TrafficStartBlockReason = TrafficStartBlockReason.none
  event_id: int = 0
  stop_session_id: int = 0
  direction_unknown: bool = False
  driver_override_active: bool = False
  can_remaining: float = 0.0
  station_innovation: float = 0.0
  stop_control_allowed: bool = False
  stop_safety_allowed: bool = False
  raw_observation_fresh: bool = False
  raw_observation_age_ms: float = 0.0
  stop_direction_unknown: bool = False
  phase: int = int(TrafficControlPhase.off)
  light_state: int = 0
  remaining_distance: float = 0.0
  raw_distance: float = 255.0
  stop_reference: float = 0.0
  source_bus: int = 0
  quality: int = 0
  base_a_target: float = 0.0
  traffic_a_target: float = 0.0
  final_a_target: float = 0.0
  should_stop: bool = False
  terminal_catch_active: bool = False


class TrafficDiagnosticsMixin:
  """Copy the traffic target into final-plan diagnostics published through pluginMsg."""

  def _set_diagnostics_from_traffic(self, traffic) -> None:
    if traffic is None:
      return
    self.diagnostics.event_id = int(traffic.eventId)
    self.diagnostics.stop_session_id = int(traffic.stopSessionId)
    self.diagnostics.direction_unknown = bool(traffic.directionUnknown)
    self.diagnostics.driver_override_active = bool(traffic.driverOverrideActive)
    self.diagnostics.can_remaining = float(traffic.canRemaining)
    self.diagnostics.station_innovation = float(traffic.stationInnovation)
    self.diagnostics.stop_control_allowed = bool(traffic.stopControlAllowed)
    self.diagnostics.stop_safety_allowed = bool(traffic.stopSafetyAllowed)
    self.diagnostics.raw_observation_fresh = bool(traffic.rawObservationFresh)
    self.diagnostics.raw_observation_age_ms = float(traffic.observationAgeMs)
    self.diagnostics.stop_direction_unknown = bool(traffic.stopDirectionUnknown)
    self.diagnostics.mode = int(traffic.mode)
    self.diagnostics.phase = int(traffic.phase)
    self.diagnostics.light_state = int(traffic.lightState)
    self.diagnostics.remaining_distance = max(0.0, float(traffic.distanceToStopPoint))
    self.diagnostics.raw_distance = float(traffic.rawDistance)
    self.diagnostics.stop_reference = max(
      0.0, float(traffic.oemTargetDistance) - float(traffic.distanceToStopPoint),
    )
    self.diagnostics.source_bus = int(traffic.sourceBus)
    self.diagnostics.quality = int(traffic.quality)


  def annotate_plan_sp(self, plan_sp) -> None:
    from .plugin import TrafficLight
    plan_sp.aTarget = self.diagnostics.final_a_target
    plugins.send_msg(TrafficLight, "traffic.plan", self.diagnostic_payload(), valid_for_ms=350)

  def diagnostic_payload(self) -> dict:
    diagnostics = self.diagnostics
    return {
      "mode": diagnostics.mode,
      "phase": diagnostics.phase,
      "active": diagnostics.action != TrafficPlanAction.none,
      "shadow": False,
      "applied": diagnostics.applied,
      "shouldStop": diagnostics.should_stop,
      "remainingDistance": diagnostics.remaining_distance,
      "rawDistance": diagnostics.raw_distance,
      "stopReference": diagnostics.stop_reference,
      "lightState": diagnostics.light_state,
      "sourceBus": diagnostics.source_bus,
      "quality": diagnostics.quality,
      "constraintAccel": diagnostics.traffic_a_target,
      "action": int(diagnostics.action),
      "baseATarget": diagnostics.base_a_target,
      "finalATarget": diagnostics.final_a_target,
      "startRequested": diagnostics.start_requested,
      "startApplied": diagnostics.start_applied,
      "startBlockReason": int(diagnostics.start_block_reason),
      "eventId": diagnostics.event_id,
      "terminalCatchActive": diagnostics.terminal_catch_active,
      "stopSessionId": diagnostics.stop_session_id,
      "directionUnknown": diagnostics.direction_unknown,
      "driverOverrideActive": diagnostics.driver_override_active,
      "canRemaining": diagnostics.can_remaining,
      "stationInnovation": diagnostics.station_innovation,
      "stopControlAllowed": diagnostics.stop_control_allowed,
      "stopSafetyAllowed": diagnostics.stop_safety_allowed,
      "rawObservationFresh": diagnostics.raw_observation_fresh,
      "rawObservationAgeMs": diagnostics.raw_observation_age_ms,
      "stopDirectionUnknown": diagnostics.stop_direction_unknown,
    }
