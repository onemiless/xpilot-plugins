"""Pure decision logic (no openpilot imports): tap the turn stalk when the phone (big) model wants a lane change.
Direction and timing come only from the model's lane-change intent; navigation only tells that a route is active."""
LEFT, RIGHT = -1, 1
# modelV2.meta.desirePrediction: 4 steps x 8 desires (log.Desire order)
LC_LEFT, LC_RIGHT = 3, 4
DESIRES = 8

INTENT_HOLD_NS = 1_000_000_000  # the intent must hold this long (normal driving reaches ~0.5 for single frames)
SPACING_NS = 8_000_000_000
MIN_SPEED = 30 / 3.6


def route_active(nav):
  """lean_mici.navigation payload: phone location ok, route ok, not rerouting."""
  try:
    return nav['health'] & 11 == 3
  except (KeyError, TypeError):
    return False


def intent(desire_prediction, index):
  """Max probability of one desire over the prediction steps."""
  p = list(desire_prediction)
  return max(p[index::DESIRES], default=0.) if len(p) % DESIRES == 0 else 0.


class Decider:
  def __init__(self, threshold=0.6):
    self.threshold = threshold
    self.direction = 0
    self.since = 0
    self.last_tap = -SPACING_NS

  def update(self, now_ns, nav, desire_prediction, big, ready, blindspot_left, blindspot_right):
    """Direction to tap now (LEFT/RIGHT) or 0. big: this frame came from the phone model.
    ready: engaged lateral, speed, no driver input, no blinker, no lane change running."""
    left, right = intent(desire_prediction, LC_LEFT), intent(desire_prediction, LC_RIGHT)
    want = LEFT if left > self.threshold and left >= right else RIGHT if right > self.threshold else 0
    if not (big and route_active(nav)) or want != self.direction:
      self.direction, self.since = (want if big and route_active(nav) else 0), now_ns
    if (not self.direction or now_ns - self.since < INTENT_HOLD_NS or not ready or now_ns - self.last_tap < SPACING_NS or
        (blindspot_left if self.direction == LEFT else blindspot_right)):
      return 0
    self.last_tap = now_ns
    return self.direction
