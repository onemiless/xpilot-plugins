"""Pure decision logic (no openpilot imports): tap the turn stalk when the phone (big) model predicts the driver's blinker.
Direction and timing come only from the model (lean_mici 'blinker': driver blinker probability at 0,2,4,6,8,10 s).
Navigation gates: route active, navigation fed to the model, and no lane change on single-lane roads, roundabouts,
ramps or in tunnels."""
LEFT, RIGHT = -1, 1
HORIZON = 2  # use the predictions for now and +2 s

INTENT_HOLD_NS = 1_000_000_000
SPACING_NS = 8_000_000_000
MIN_SPEED = 30 / 3.6
TURN_MAX_SPEED = 20 * 0.44704 - 3 / 3.6  # below SP's LANE_CHANGE_SPEED_MIN (20 mph) with margin: the blinker cannot start a lane change
TURN_RIGHT, TURN_LEFT = (10, 11, 12), (13, 14, 15)  # NAV_STATE maneuver: turn/sharp/U-turn right, U-turn/sharp/turn left
NAV_FED = 0x02  # NAV_STATE nav_input_flags bit1: navigation fed to the model


def gate(nav):
  """True when the phone navigation context allows a lane change."""
  try:
    if nav['health'] & 11 != 3 or not nav['state'] or not nav['state'][8] & NAV_FED:
      return False
    road = nav.get('road')
    return not (road and (road['lane_count'] == 1 or road['roundabout'] or road['ramp'] or road['tunnel']))
  except (KeyError, TypeError, IndexError):
    return False


def intent(nav, side):
  """Max predicted driver-blinker probability over the next HORIZON steps."""
  try:
    p = nav['blinker'][side][:HORIZON]
    return max(float(x) for x in p) if p else 0.
  except (KeyError, TypeError, ValueError):
    return 0.


class Decider:
  def __init__(self, threshold=0.6):
    self.threshold = threshold
    self.direction = 0
    self.since = 0
    self.last_tap = -SPACING_NS

  def update(self, now_ns, nav, big, ready, blindspot_left, blindspot_right):
    """Direction to tap now (LEFT/RIGHT) or 0. big: this frame came from the phone model.
    ready: engaged lateral, speed, no driver input, no blinker, no lane change running."""
    ok = big and gate(nav)
    left, right = (intent(nav, 'left'), intent(nav, 'right')) if ok else (0., 0.)
    want = LEFT if left > self.threshold and left >= right else RIGHT if right > self.threshold else 0
    if want != self.direction:
      self.direction, self.since = want, now_ns
    if (not self.direction or now_ns - self.since < INTENT_HOLD_NS or not ready or now_ns - self.last_tap < SPACING_NS or
        (blindspot_left if self.direction == LEFT else blindspot_right)):
      return 0
    self.last_tap = now_ns
    return self.direction


class TurnSignal:
  """Navigation turn signal: one stalk tap when the route's next maneuver is a turn within distance_m and the car is
  slower than TURN_MAX_SPEED, so the blinker only signals the turn. Once per approach: re-arms after the turn condition
  has been false (turn done, route changed, speed up)."""

  def __init__(self, distance_m=80):
    self.distance_m = distance_m
    self.fired = False

  def update(self, nav, v_ego, blinker_on):
    want = 0
    try:
      state = nav['state']
      if nav['health'] & 11 == 3 and state and 0 < state[4] <= self.distance_m and 0 <= v_ego < TURN_MAX_SPEED:
        want = LEFT if state[1] in TURN_LEFT else RIGHT if state[1] in TURN_RIGHT else 0
    except (KeyError, TypeError, IndexError):
      want = 0
    if not want:
      self.fired = False
      return 0
    if self.fired or blinker_on:
      return 0
    self.fired = True
    return want
