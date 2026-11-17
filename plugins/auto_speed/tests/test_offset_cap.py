"""Speed limit target with the offset cap (dev-sp-npu SpeedLimitOffsetMaxSpeed, kept in this plugin).

Failure cases:
- C1 a cap of 0 changes the target (0 means off)
- C2 the offset still applies when limit + offset reaches the cap
- C3 the offset is dropped below the cap
- C4 the cap is compared in m/s instead of the car's display units (MPH car)
- C5 a negative offset is dropped/kept by the same rule
- C6 no speed limit still gives a target
- C7 the setting is not in plugin.json or not a 0..150 stepper
"""
import json
from pathlib import Path
from types import SimpleNamespace

from ..plugin import AutoSpeed, KPH_TO_MS, MPH_TO_MS


def target(limit_display, offset, cap, units="KPH"):
  unit = MPH_TO_MS if units == "MPH" else KPH_TO_MS
  p = AutoSpeed.__new__(AutoSpeed)
  p.state = SimpleNamespace(tesla_speed_units=units, tesla_speed_limit_target=0.0, tesla_speed_limit_target_valid=False)
  p.offset, p.offset_max = offset, cap
  p.publish_state(SimpleNamespace(speedLimit=limit_display * unit))
  return round(p.state.tesla_speed_limit_target / unit, 6) if p.state.tesla_speed_limit_target_valid else None


def test_cap_off():
  assert target(100, 5, 0) == 105  # C1


def test_cap_reached_drops_offset():
  assert target(100, 5, 105) == 100  # C2: limit + offset == cap
  assert target(120, 5, 100) == 120


def test_below_cap_keeps_offset():
  assert target(60, 5, 100) == 65  # C3


def test_mph_display_units():
  assert target(65, 5, 70, "MPH") == 65  # C4: 70 mph, not 70 km/h
  assert target(50, 5, 70, "MPH") == 55


def test_negative_offset():
  assert target(100, -5, 90) == 100  # C5: 95 >= 90
  assert target(80, -5, 90) == 75


def test_no_limit():
  assert target(0, 5, 100) is None  # C6


def test_setting():
  m = json.loads((Path(__file__).parents[1] / "plugin.json").read_text())
  row = next(s for s in m["settings"] if s["key"] == "ExtAutoSpeedLimitOffsetMax")  # C7
  assert row["range"][:3] == [0, 150, 5] and m["params"]["ExtAutoSpeedLimitOffsetMax"]["type"] == "INT"
