"""Inherited navigation policy values; persistence belongs to plugin ParamsView."""
from dataclasses import dataclass

@dataclass(frozen=True)
class NavAssistSettings:
  enabled: bool = True
  lane_change_enabled: bool = True
  lane_change_buzzer_enabled: bool = True
  efficiency_lane_change_enabled: bool = True
  turn_signal_enabled: bool = True
  turn_slowdown_enabled: bool = True
  turn_lane_lookahead_m: int = 1000
  exit_lane_lookahead_m: int = 2000
  signal_lead_time_s: int = 8
  turn_speed_kph: int = 18
  max_lane_changes: int = 5
  logging_enabled: bool = True
  overtake_mode: int = 2
  overtake_min_distance_m: int = 12
  overtake_max_distance_m: int = 80
  overtake_closing_kph: int = 3
  overtake_time_gap_tenths: int = 30
  overtake_cruise_percent: int = 90
  overtake_lead_min_kph: int = 35
  overtake_stable_ms: int = 800
  overtake_success_cooldown_s: int = 10
  overtake_failed_cooldown_s: int = 4
  overtake_allow_right: bool = True
  overtake_prefer_left: bool = True


OVERTAKE_PRESETS = {
  1: dict(overtake_min_distance_m=8, overtake_max_distance_m=100, overtake_closing_kph=1,
          overtake_time_gap_tenths=40, overtake_cruise_percent=95, overtake_lead_min_kph=25,
          overtake_stable_ms=400, overtake_success_cooldown_s=6,
          overtake_failed_cooldown_s=3, overtake_allow_right=True, overtake_prefer_left=False),
  2: dict(overtake_min_distance_m=12, overtake_max_distance_m=80, overtake_closing_kph=3,
          overtake_time_gap_tenths=30, overtake_cruise_percent=90, overtake_lead_min_kph=35,
          overtake_stable_ms=800, overtake_success_cooldown_s=10,
          overtake_failed_cooldown_s=4, overtake_allow_right=True, overtake_prefer_left=True),
  3: dict(overtake_min_distance_m=20, overtake_max_distance_m=60, overtake_closing_kph=8,
          overtake_time_gap_tenths=20, overtake_cruise_percent=80, overtake_lead_min_kph=45,
          overtake_stable_ms=1200, overtake_success_cooldown_s=15,
          overtake_failed_cooldown_s=6, overtake_allow_right=False, overtake_prefer_left=True),
}
