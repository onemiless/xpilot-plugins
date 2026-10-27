"""Failure cases listed first:
- D1 above High does not propose stock, below Low does not propose sp
- D2 between Low and High (and exactly on a threshold) proposes a switch instead of keeping the owner
- D3 Low >= High (bad settings) still proposes switches
- D4 a TLSSC stop (red/yellow or a near stop line, TLSSC active) at any speed does not propose stock,
     or an inactive/missing/green-without-stop-line tlssc.state changes the speed rule
"""
from ..logic import KPH, desired_owner


def test_thresholds():
  assert desired_owner(81 / KPH, 80, 70) == "stock"
  assert desired_owner(69 / KPH, 80, 70) == "sp"
  assert desired_owner(0, 80, 70) == "sp"


def test_hysteresis_band_keeps_owner():
  for kph in (70, 75, 80):
    assert desired_owner(kph / KPH, 80, 70) is None


def test_bad_settings_never_switch():
  for v in (0, 50 / KPH, 120 / KPH):
    assert desired_owner(v, 70, 70) is None
    assert desired_owner(v, 60, 70) is None


def test_tlssc_stop_hands_to_stock():
  for state in ({"active": True, "color": 1, "stop_m": 0}, {"active": True, "color": 3}, {"active": True, "color": 2, "stop_m": 40}):
    for kph in (0, 30, 75, 120):
      assert desired_owner(kph / KPH, 80, 70, state) == "stock"
  for state in (None, {}, {"active": False, "color": 1}, {"active": True, "color": 2, "stop_m": 0}, {"active": True, "stop_m": 150}):
    assert desired_owner(30 / KPH, 80, 70, state) == "sp"
    assert desired_owner(75 / KPH, 80, 70, state) is None
