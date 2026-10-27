"""Pure decision logic (no openpilot imports): when to tap the turn stalk toward a navigation maneuver."""
LEFT, RIGHT = -1, 1
# Valhalla maneuver types that need the car on the left / right side of the road (lean_mici NavExt state[1]).
LEFT_TYPES = {14, 15, 16, 19, 21, 24}
RIGHT_TYPES = {9, 10, 11, 18, 20, 23}
# modelV2.meta.desirePrediction: 4 steps x 8 desires (log.Desire order)
TURN_LEFT, TURN_RIGHT, LC_LEFT, LC_RIGHT = 1, 2, 3, 4
DESIRES = 8

EARLY_DISTANCE_M = 1000  # model lane-change intent may start the change this far before the maneuver
TURN_INTENT = 0.3        # model already predicts the turn: the car is in a turning lane, stop changing lanes
INTENT_HOLD_NS = 500_000_000
SPACING_NS = 8_000_000_000
MAX_CHANGES = 3
MIN_SPEED = 30 / 3.6


def maneuver(nav):
  """(direction, distance m) of the next maneuver from a lean_mici.navigation payload, or None."""
  try:
    state = nav['state']
    if nav['health'] & 11 != 3 or state is None or state[4] == 0xFFFFFFFF:
      return None
    direction = LEFT if state[1] in LEFT_TYPES else RIGHT if state[1] in RIGHT_TYPES else 0
    return (direction, state[4]) if direction else None
  except (KeyError, TypeError, IndexError):
    return None


def intent(desire_prediction, index):
  """Max probability of one desire over the prediction steps."""
  p = list(desire_prediction)
  return max(p[index::DESIRES], default=0.) if len(p) % DESIRES == 0 else 0.


class Decider:
  def __init__(self, trigger_distance_m=400, lc_intent=0.3):
    self.trigger_distance = trigger_distance_m
    self.lc_intent = lc_intent
    self.intent_since = 0
    self.last_tap = -SPACING_NS
    self.changes = 0
    self.last_distance = None

  def update(self, now_ns, nav, desire_prediction, ready, blindspot_left, blindspot_right):
    """Direction to tap now (LEFT/RIGHT) or 0. ready: engaged lateral, speed, no driver input, no lane change running."""
    m = maneuver(nav)
    if m is None:
      self.intent_since, self.last_distance = 0, None
      return 0
    direction, distance = m
    if self.last_distance is not None and distance > self.last_distance + 50:  # a new maneuver
      self.changes = 0
    self.last_distance = distance

    turn = intent(desire_prediction, TURN_LEFT if direction == LEFT else TURN_RIGHT)
    lc = intent(desire_prediction, LC_LEFT if direction == LEFT else LC_RIGHT)
    self.intent_since = (self.intent_since or now_ns) if lc > self.lc_intent else 0
    model_wants = self.intent_since and now_ns - self.intent_since >= INTENT_HOLD_NS

    if (not ready or turn > TURN_INTENT or self.changes >= MAX_CHANGES or now_ns - self.last_tap < SPACING_NS or
        (blindspot_left if direction == LEFT else blindspot_right)):
      return 0
    if distance <= self.trigger_distance or (model_wants and distance <= EARLY_DISTANCE_M):
      self.last_tap, self.changes, self.intent_since = now_ns, self.changes + 1, 0
      return direction
    return 0
