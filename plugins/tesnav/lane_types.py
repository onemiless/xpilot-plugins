from __future__ import annotations

from dataclasses import dataclass, replace
from enum import IntEnum
import math
import secrets



class LaneIntentDirection(IntEnum):
  none = 0
  left = 1
  right = 2


class ObservedLaneChangeState(IntEnum):
  off = 0
  pre = 1
  starting = 2
  finishing = 3


@dataclass(frozen=True)
class NavLanePlan:
  valid: bool
  session_id: str
  route_revision: int
  maneuver_event_id: int
  lane_count: int
  recommended_indices: tuple[int, ...]
  heuristic: bool = False
  edge_direction: LaneIntentDirection = LaneIntentDirection.none
  force_fork: bool = False
  allow_unknown_crossing: bool = False
  ignore_solid_boundary: bool = False
  # Source health is distinct from mapping AMap's full-road lane indices.
  navigation_valid: bool = False


@dataclass(frozen=True)
class LaneTopologyInput:
  valid_for_control: bool
  visible_lane_count: int
  ego_lane_index: int
  left_neighbor_exists: bool | None
  right_neighbor_exists: bool | None
  left_crossing_allowed: bool
  right_crossing_allowed: bool


@dataclass(frozen=True)
class LaneVehicleInput:
  lateral_active: bool
  speed_mps: float
  left_blindspot: bool = False
  right_blindspot: bool = False
  left_blinker: bool = False
  right_blinker: bool = False
  brake_pressed: bool = False
  gas_pressed: bool = False
  steering_pressed: bool = False
  lane_change_state: ObservedLaneChangeState = ObservedLaneChangeState.off
  lane_change_direction: LaneIntentDirection = LaneIntentDirection.none
  model_mono_time_ns: int = 0


@dataclass(frozen=True)
class NavTurnPlan:
  valid: bool
  session_id: str
  route_revision: int
  maneuver_event_id: int
  maneuver: str
  distance_m: float
  source_interrupted: bool = False
  turn_signal_hold: bool = False


@dataclass(frozen=True)
class NavLaneIntent:
  signal_requested: bool = False
  lane_change_ready: bool = False
  direction: LaneIntentDirection = LaneIntentDirection.none
  request_id: int = 0
  target_lane_index: int = -1
  reason: str = "idle"
