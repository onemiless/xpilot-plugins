from .. import stalk
from ..logic import LEFT, RIGHT, SPACING_NS, Decider

S = 1_000_000_000
ROUTE = {'health': 7, 'state': [2, 15, 0, 0, 3000, 5000, 300, 10]}  # a left turn 3 km ahead


def desire(lc_left=0., lc_right=0., turn_left=0., turn_right=0.):
  return [0., turn_left, turn_right, lc_left, lc_right, 0., 0., 0.] * 4


def run(d, t0, t1, *args):
  """Call update every 50 ms from t0 to t1 (seconds); return the taps."""
  return [r for t in range(int(t0 * 20), int(t1 * 20)) if (r := d.update(t * S // 20, *args))]


def test_stalk_frame_matches_car():
  # right-stalk frames the owner's car sent (route 00000015--2981e48722); counter = template counter + 1
  for real in ('87000200', 'f4010200', '36020200', 'cf030200', 'cf040200', '0f0d0200', 'b30e0200', 'd20f0200'):
    frame = bytes.fromhex(real)
    template = bytes((0, (frame[1] - 1) & 0x0F, 0, 0))
    assert stalk.stalk_frame(template, stalk.RIGHT) == frame
  assert not stalk.is_idle_template(bytes.fromhex('87000200'))  # never clone an active stalk frame


def test_model_intent_decides_direction_and_timing():
  d = Decider(threshold=.6)
  assert run(d, 0, .9, ROUTE, desire(lc_right=.8), True, True, False, False) == []      # must hold 1 s
  assert run(d, .9, 1.2, ROUTE, desire(lc_right=.8), True, True, False, False) == [RIGHT]  # direction from the model, not the left turn
  assert run(d, 1.2, 8.5, ROUTE, desire(lc_right=.8), True, True, False, False) == []    # spacing
  d = Decider(threshold=.6)
  assert run(d, 0, 2, ROUTE, desire(lc_left=.7, lc_right=.65), True, True, False, False) == [LEFT]


def test_navigation_turn_alone_never_taps():
  d = Decider(threshold=.6)
  assert run(d, 0, 5, ROUTE, desire(turn_left=.9, lc_left=.5), True, True, False, False) == []


def test_gates():
  for args in ((None, desire(lc_left=.9), True, True, False, False),            # no navigation
               ({'health': 15}, desire(lc_left=.9), True, True, False, False),  # rerouting
               (ROUTE, desire(lc_left=.9), False, True, False, False),          # local model, not the phone
               (ROUTE, desire(lc_left=.9), True, False, False, False),          # not ready (driver input, speed...)
               (ROUTE, desire(lc_left=.9), True, True, True, False)):           # car in the left blind spot
    assert run(Decider(threshold=.6), 0, 3, *args) == []


def test_flicker_restarts_hold():
  d = Decider(threshold=.6)
  taps = []
  for t in range(60):  # 3 s, intent drops every 0.75 s
    taps += [r for r in [d.update(t * S // 20, ROUTE, desire(lc_left=.9 if t % 15 else .1), True, True, False, False)] if r]
  assert taps == []
  assert SPACING_NS == 8 * S
