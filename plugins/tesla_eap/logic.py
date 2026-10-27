"""Pure Advanced-EAP logic (no openpilot imports), after t2can v3.29.1 evaluateAutoBlinker / auto_blinker_pure.h:
a Navigate-on-Autopilot lane-change request (0x24A behaviour type, bits 56..57: 2 left, 3 right) opens a session; after the
delay one stalk tap goes out, only when DAS_autoLaneChangeState allows that side. One tap per request session."""
LEFT, RIGHT = 1, 2
ACTIVE_NAV = 5  # DAS_autopilotState
REQUEST_FRESH_NS = 2_000_000_000
ALC_ALLOWS = {LEFT: (6, 8), RIGHT: (7, 8)}  # ALC_AVAILABLE_ONLY_L / ONLY_R / BOTH


def request_dir(data: bytes) -> int:
  """0x24A behaviour type -> LEFT/RIGHT/0."""
  if len(data) < 8:
    return 0
  return {2: LEFT, 3: RIGHT}.get(data[7] & 0x03, 0)


class AutoBlinker:
  def __init__(self, delay_ns: int = 2_000_000_000):
    self.delay_ns = delay_ns
    self.last_dir, self.fire_at, self.fired = 0, 0, False

  def update(self, now_ns: int, noa: bool, req_dir: int, alc: int, blinker_on: bool) -> int:
    """Direction to tap now, else 0. req_dir must already be 0 when the request frame is older than REQUEST_FRESH_NS."""
    # ponytail: no off-ramp case (t2can allows ALC state 4 toward a nav off-ramp); add it when ramps need taps
    if not noa or not req_dir:
      self.last_dir, self.fired = 0, False
      return 0
    if req_dir != self.last_dir:
      self.last_dir, self.fire_at, self.fired = req_dir, now_ns + self.delay_ns, False
    if self.fired or now_ns < self.fire_at or blinker_on or alc not in ALC_ALLOWS[req_dir]:
      return 0
    self.fired = True
    return req_dir
