from .. import stalk
from ..logic import LEFT, RIGHT, SPACING_NS, Decider

S = 1_000_000_000


def nav(maneuver=15, distance=300, health=7):
  return {'health': health, 'state': [2, maneuver, 0, 0, distance, 5000, 300, 10]}


def desire(turn_left=0., turn_right=0., lc_left=0., lc_right=0.):
  return [0., turn_left, turn_right, lc_left, lc_right, 0., 0., 0.] * 4


def test_stalk_frame_matches_dev_sp():
  # dev-sp create_sccm_left_stalk(turn, counter) on an all-zero frame; counter = template counter + 1
  for turn in (stalk.LEFT, stalk.RIGHT):
    for counter in range(16):
      template = bytes((0, (counter - 1) & 0x0F, 0, 0))
      want = bytearray((0, counter, turn, 0))
      want[0] = stalk.crc8_opensafety(bytes((0, turn, 0, 0))) ^ stalk.MAGIC[counter]
      assert stalk.stalk_frame(template, turn) == bytes(want)
  assert not stalk.is_idle_template(bytes((0, 0, 2, 0)))  # never clone an active stalk frame


def test_triggers_at_distance_and_respects_gates():
  d = Decider(trigger_distance_m=400)
  assert d.update(1 * S, nav(15, 600), desire(), True, False, False) == 0   # too far, no model intent
  assert d.update(2 * S, nav(15, 390), desire(), True, True, False) == 0    # car in the left blind spot
  assert d.update(3 * S, nav(15, 380), desire(), False, False, False) == 0  # not ready (driver input, speed...)
  assert d.update(4 * S, nav(15, 370), desire(), True, False, False) == LEFT
  assert d.update(5 * S, nav(15, 360), desire(), True, False, False) == 0   # spacing
  assert d.update(5 * S + SPACING_NS, nav(10, 200), desire(), True, False, False) == RIGHT


def test_model_intents():
  d = Decider(trigger_distance_m=200, lc_intent=.3)
  assert d.update(1 * S, nav(10, 900), desire(lc_right=.5), True, False, False) == 0  # intent must hold 0.5 s
  assert d.update(int(1.6 * S), nav(10, 880), desire(lc_right=.5), True, False, False) == RIGHT
  d = Decider(trigger_distance_m=400)
  assert d.update(1 * S, nav(15, 300), desire(turn_left=.6), True, False, False) == 0  # already in the turning lane


def test_bad_navigation_never_taps():
  d = Decider()
  for bad in (None, {}, nav(health=5), nav(health=15), nav(distance=0xFFFFFFFF), nav(maneuver=8), nav(maneuver=4)):
    assert d.update(1 * S, bad, desire(lc_left=1.), True, False, False) == 0


def test_max_changes_per_maneuver():
  d = Decider(trigger_distance_m=400)
  taps = [d.update(i * SPACING_NS, nav(15, 350 - i), desire(), True, False, False) for i in range(1, 6)]
  assert taps.count(LEFT) == 3
  assert d.update(7 * SPACING_NS, nav(15, 399), desire(), True, False, False) == LEFT  # new maneuver (distance jumped)
