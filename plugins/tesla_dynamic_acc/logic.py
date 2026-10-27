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
