from .. import stalk
from ..logic import LEFT, RIGHT, SPACING_NS, TURN_MAX_SPEED, Decider, TurnSignal

S = 1_000_000_000


def nav(left=(0., 0.), right=(0., 0.), health=7, flags=2, road=None):
  state = [2, 15, 10, 0, 3000, 5000, 300, 10, flags, 255, 0xFFFF]  # a left turn 3 km ahead, navigation fed to the model
  return {'health': health, 'state': state, 'road': road,
          'blinker': {'left': list(left) + [0.] * 4, 'right': list(right) + [0.] * 4}}


def road(**kw):
  return {'lane_count': 3, 'roundabout': False, 'ramp': False, 'tunnel': False} | kw


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


def test_model_blinker_decides_direction_and_timing():
  d = Decider(threshold=.6)
  assert run(d, 0, .9, nav(right=(.1, .8)), True, True, False, False) == []         # must hold 1 s
  assert run(d, .9, 1.2, nav(right=(.1, .8)), True, True, False, False) == [RIGHT]  # model direction, not the left turn
  assert run(d, 1.2, 8.5, nav(right=(.1, .8)), True, True, False, False) == []     # spacing
  assert run(Decider(threshold=.6), 0, 2, nav(left=(.7, .1), right=(.65, 0)), True, True, False, False) == [LEFT]
  assert run(Decider(threshold=.6), 0, 3, nav(left=(0, 0)), True, True, False, False) == []  # turn alone never taps


def test_gates():
  for args in ((None, True, True, False, False),                                  # no navigation
               (nav(left=(.9, .9), health=15), True, True, False, False),         # rerouting
               (nav(left=(.9, .9), flags=0), True, True, False, False),           # navigation not fed to the model
               (nav(left=(.9, .9), road=road(lane_count=1)), True, True, False, False),
               (nav(left=(.9, .9), road=road(roundabout=True)), True, True, False, False),
               (nav(left=(.9, .9), road=road(ramp=True)), True, True, False, False),
               (nav(left=(.9, .9), road=road(tunnel=True)), True, True, False, False),
               (nav(left=(.9, .9)), False, True, False, False),                   # local model, not the phone
               (nav(left=(.9, .9)), True, False, False, False),                   # not ready (driver input, speed...)
               (nav(left=(.9, .9)), True, True, True, False)):                    # car in the left blind spot
    assert run(Decider(threshold=.6), 0, 3, *args) == []
  assert run(Decider(threshold=.6), 0, 2, nav(left=(.9, .9), road=road(lane_count=0)), True, True, False, False) == [LEFT]


def test_flicker_restarts_hold():
  d = Decider(threshold=.6)
  taps = [r for t in range(60) if (r := d.update(t * S // 20, nav(left=(.9 if t % 15 else .1, 0)), True, True, False, False))]
  assert taps == [] and SPACING_NS == 8 * S


def turn_nav(maneuver=15, distance=60, health=7):
  return {'health': health, 'state': [2, maneuver, 10, 0, distance, 5000, 300, 10, 2, 255, 0xFFFF], 'road': None}


def test_turn_signal():
  """T1 no tap for a non-turn maneuver, beyond the distance, without a healthy route, at or above TURN_MAX_SPEED or
  with a blinker already on; T2 more than one tap per approach, or no re-arm after the condition clears."""
  assert TURN_MAX_SPEED < 20 * 0.44704
  t = TurnSignal(80)
  for nav, v in ((turn_nav(maneuver=7), 5), (turn_nav(distance=120), 5), (turn_nav(health=1), 5), (turn_nav(), TURN_MAX_SPEED),
                 (turn_nav(distance=0xFFFFFFFF), 5), (None, 5)):
    assert t.update(nav, v, False) == 0
  assert t.update(turn_nav(), 5, True) == 0  # driver already signalling
  assert t.update(turn_nav(), 5, False) == LEFT
  assert t.update(turn_nav(), 5, False) == 0 and t.update(turn_nav(distance=20), 0, False) == 0
  assert t.update(turn_nav(maneuver=7), 5, False) == 0  # turn done
  assert t.update(turn_nav(maneuver=10), 5, False) == RIGHT
