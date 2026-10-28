"""Failure cases listed first:
- D1 above High does not propose stock, below Low does not propose sp
- D2 between Low and High (and exactly on a threshold) proposes a switch instead of keeping the owner
- D3 Low >= High (bad settings) still proposes switches
- D4 a TLSSC stop (red/yellow or a near stop line, TLSSC active) at any speed does not propose stock,
     or an inactive/missing/green-without-stop-line tlssc.state changes the speed rule
- D5 four-finger toggle: a hold at start or a continued hold fires, 3/5 fingers fire, a press while disengaged or
     without the owner ABI fires, the choice is lost before the next press, or survives disengage
"""
from ..logic import KPH, TouchToggle, desired_owner


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


def test_touch_toggle():
  t = TouchToggle()
  assert t.update(4, 'stock', True, True) is None  # already held at start
  assert t.update(4, 'stock', True, True) is None
  assert t.update(0, 'stock', True, True) is None
  for fingers in (3, 5, 0):
    assert t.update(fingers, 'stock', True, True) is None
  assert t.update(4, 'stock', True, True) == 'sp'
  assert t.update(4, 'stock', True, True) == 'sp' and t.update(None, 'sp', True, True) == 'sp'  # held, kept after ack
  t.update(0, 'sp', True, True)
  assert t.update(4, 'sp', True, True) == 'stock'
  assert t.update(None, 'stock', False, True) is None  # disengage clears
  t.update(0, 'stock', False, True)
  assert t.update(4, 'stock', False, True) is None and t.update(0, 'stock', True, False) is None
  assert t.update(4, 'stock', True, False) is None
