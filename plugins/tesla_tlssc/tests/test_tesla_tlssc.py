"""Failure cases:
- T1 a non-mux-0 or short 0x3FD frame is cloned
- T2 the clone changes anything but byte 4 bits 6/7, or sends when the car already has TLSSC on
- T3 the speed window lets a speed outside [min, max] through or blocks its edges
- T4 tlssc.state counts as stopping when inactive, missing, green without a stop line, or the stop line is 0/far
- T5 the manifest rule is not mux-0 only, forces other bits, or copies bits 38/39 from the stock frame
"""
import json
from pathlib import Path

from ..logic import GREEN, RED, YELLOW, in_window, is_template, stopping, tlssc_frame

STOCK = bytes((0x08, 0x11, 0x22, 0x33, 0x05, 0x55, 0x66, 0x77))  # mux 0, TLSSC off


def test_template_only_mux0():
  assert is_template(STOCK)
  assert not is_template(bytes((0x01,)) + STOCK[1:])
  assert not is_template(STOCK[:7])


def test_clone_sets_only_tlssc_bits():
  out = tlssc_frame(STOCK)
  assert out[4] == 0xC5 and out[:4] == STOCK[:4] and out[5:] == STOCK[5:]
  assert tlssc_frame(out) is None
  assert tlssc_frame(bytes((0x02,)) + STOCK[1:]) is None


def test_speed_window():
  assert in_window(0, 0, 90) and in_window(90 / 3.6, 0, 90)
  assert not in_window(95 / 3.6, 0, 90) and not in_window(10 / 3.6, 20, 90)


def test_stopping():
  assert stopping({"active": True, "color": RED, "stop_m": 0})
  assert stopping({"active": True, "color": YELLOW, "stop_m": 0})
  assert stopping({"active": True, "color": GREEN, "stop_m": 30})
  for state in (None, {}, {"active": False, "color": RED}, {"active": True, "color": GREEN, "stop_m": 0},
                {"active": True, "color": 0, "stop_m": 120}):
    assert not stopping(state)


def test_rule_matches_logic():
  rule = json.loads((Path(__file__).parents[1] / "plugin.json").read_text())["rules"][0]
  assert (rule["tx_address"], rule["tx_bus"], rule["predicate_mask"][0], rule["predicate_value"][0]) == (0x3FD, 1, 7, 0)
  assert rule["constant_mask"] == rule["constant_value"] == [0, 0, 0, 0, 0xC0, 0, 0, 0]
  assert rule["copy_mask"][4] == 0x3F and all(m == 0xFF for i, m in enumerate(rule["copy_mask"]) if i != 4)
