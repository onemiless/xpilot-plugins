"""Failure cases:
- E1 the 0x24A behaviour type maps to the wrong side, or a short frame yields a request
- E2 a tap goes out before the delay, without NoA, toward a side ALC does not allow, or while a blinker is on
- E3 one request session taps more than once, or a new/opposite request does not start a new delayed session
- E4 a blocked side never taps once ALC frees it within the same session
- E5 the manifest misses role:stock.ap, role:engagement.state or the 0x249 rule, or the rule differs from nav_lane_change's
"""
import json
from pathlib import Path

from ..logic import LEFT, RIGHT, AutoBlinker, request_dir

S = 1_000_000_000
BOTH, ONLY_L, ONLY_R, BLOCKED = 8, 6, 7, 22


def frame(behaviour):
  return bytes(7) + bytes((behaviour,))


def test_request_dir():
  assert request_dir(frame(2)) == LEFT and request_dir(frame(3)) == RIGHT
  assert request_dir(frame(0)) == 0 and request_dir(frame(1)) == 0 and request_dir(frame(2)[:7]) == 0


def run(b, t0, t1, *args):
  return [r for t in range(int(t0 * 20), int(t1 * 20)) if (r := b.update(t * S // 20, *args))]


def test_delay_and_gates():
  b = AutoBlinker(2 * S)
  assert run(b, 0, 1.9, True, LEFT, BOTH, False) == []
  assert run(b, 1.9, 5, True, LEFT, BOTH, False) == [LEFT]
  for args in ((False, LEFT, BOTH, False), (True, LEFT, ONLY_R, False), (True, RIGHT, ONLY_L, False), (True, LEFT, BOTH, True)):
    b = AutoBlinker(0)
    assert run(b, 0, 3, *args) == [], args


def test_sessions():
  b = AutoBlinker(S)
  assert run(b, 0, 5, True, LEFT, BOTH, False) == [LEFT]
  assert run(b, 5, 5.5, True, RIGHT, BOTH, False) == []
  assert run(b, 5.5, 8, True, RIGHT, BOTH, False) == [RIGHT]
  assert run(b, 8, 9, True, 0, BOTH, False) == []
  assert run(b, 9, 12, True, RIGHT, BOTH, False) == [RIGHT]


def test_blocked_then_free():
  b = AutoBlinker(S)
  assert run(b, 0, 3, True, RIGHT, BLOCKED, False) == []
  assert run(b, 3, 4, True, RIGHT, ONLY_R, False) == [RIGHT]


def test_manifest():
  here = Path(__file__).parents[1]
  m = json.loads((here / "plugin.json").read_text())
  assert {"role:stock.ap", "role:engagement.state", "can.tx:0x249:1"} <= set(m["claims"])
  assert m["rules"] == json.loads((here.parent / "nav_lane_change/plugin.json").read_text())["rules"]
