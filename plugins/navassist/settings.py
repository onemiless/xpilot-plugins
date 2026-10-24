"""Navigation policy settings, read from the host ParamsView (plugin.json declares the keys)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NavSettings:
  lane_change_enabled: bool = True
  turn_signal_enabled: bool = True
  turn_slowdown_enabled: bool = True
  turn_lane_lookahead_m: int = 1000
  exit_lane_lookahead_m: int = 2000
  signal_lead_time_s: int = 8
  turn_speed_kph: int = 18
  max_lane_changes: int = 5


def _int(params, key: str, default: int, low: int, high: int) -> int:
  try:
    value = int(params.get(key, return_default=True))
  except (TypeError, ValueError):
    return default
  return value if low <= value <= high else default


def _bool(params, key: str, default: bool) -> bool:
  try:
    return bool(params.get_bool(key))
  except Exception:
    return default


def read(params) -> NavSettings:
  return NavSettings(
    lane_change_enabled=_bool(params, "ExtNavAssistLaneChange", True),
    turn_signal_enabled=_bool(params, "ExtNavAssistTurnSignal", True),
    turn_slowdown_enabled=_bool(params, "ExtNavAssistTurnSlowdown", True),
    turn_lane_lookahead_m=_int(params, "ExtNavAssistLookahead", 1000, 100, 1500),
    exit_lane_lookahead_m=_int(params, "ExtNavAssistExitLookahead", 2000, 300, 3000),
    signal_lead_time_s=_int(params, "ExtNavAssistSignalLead", 8, 3, 12),
    turn_speed_kph=_int(params, "ExtNavAssistTurnSpeed", 18, 10, 30),
    max_lane_changes=_int(params, "ExtNavAssistMaxChanges", 5, 1, 5),
  )
