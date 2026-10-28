"""Pure decision logic (no openpilot imports), so it can be checked anywhere."""
KPH = 3.6


RED, YELLOW = 1, 3  # DAS_road.DAS_trafficLightColor, as published by tesla_tlssc in tlssc.state


def tlssc_stopping(state) -> bool:
  """tesla_tlssc's tlssc.state: TLSSC on and the car shows a red/yellow light or a stop line closer than 100 m."""
  return bool(state) and bool(state.get("active")) and (state.get("color") in (RED, YELLOW) or 0 < (state.get("stop_m") or 0) < 100)


def desired_owner(v_ego: float, high_kph: float, low_kph: float, tlssc=None) -> str | None:
  """"stock" above high or while the stock TLSSC wants to stop, "sp" below low, None in between (keep the current owner)
  or when low >= high."""
  if tlssc_stopping(tlssc):
    return "stock"
  if low_kph >= high_kph:
    return None
  v = v_ego * KPH
  if v > high_kph:
    return "stock"
  if v < low_kph:
    return "sp"
  return None


class TouchToggle:
  """Four-finger touch on the car screen (UI_status2.UI_activeTouchPoints == 4, rising edge) asks for the other owner.
  The choice is kept (the speed rule is ignored) until the next press or until SP disengages. A hold that is already
  down when the drive starts, or that continues, never fires; other finger counts are ignored."""

  def __init__(self):
    self.down, self.target = True, None

  def update(self, fingers, owner, enabled, available):
    if not (enabled and available):
      self.target = None
    if fingers is not None:
      down = fingers == 4
      if down and not self.down and enabled and available:
        self.target = 'sp' if owner == 'stock' else 'stock'
      self.down = down
    return self.target
