"""Pure decision logic (no openpilot imports), so it can be checked anywhere."""
KPH = 3.6


def desired_owner(v_ego: float, high_kph: float, low_kph: float) -> str | None:
  """"stock" above high, "sp" below low, None in between (keep the current owner) or when low >= high."""
  if low_kph >= high_kph:
    return None
  v = v_ego * KPH
  if v > high_kph:
    return "stock"
  if v < low_kph:
    return "sp"
  return None
