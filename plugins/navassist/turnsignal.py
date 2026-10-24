"""Bounded Tesla DAS_bodyControls (0x3E9, vehicle bus 1) turn-signal requests.

The lamp is cloned from a fresh car frame with turn_request 0; only the turn request, reason, counter and checksum
change. The panda safety plugin 'navassist' re-validates every frame, including the checksum and counter. The
stock DesireHelper sees the resulting CarState blinker and still applies its blind-spot, road-edge and ALC-mode
gates; this module never sends steering, acceleration or a lane-change command."""
from __future__ import annotations

ADDRESS = 0x3E9
BUS = 1
TEMPLATE_MAX_AGE_NS = 1_500_000_000
SEND_INTERVAL_NS = 100_000_000
FEEDBACK_TIMEOUT_NS = 2_500_000_000
CANCEL_TOTAL_TIMEOUT_NS = 5_000_000_000
SESSION_TIMEOUT_NS = 60_000_000_000
MAX_CANCEL_ATTEMPTS = 3
TURN_REQUESTS = {"left": 1, "right": 2, "cancel": 3}
ACTIVE_REASON = 8
CANCEL_REASON = 4


def checksum(data: bytes | bytearray) -> int:
  if len(data) != 8:
    raise ValueError("0x3E9 must contain 8 bytes")
  return (0xE9 + 0x03 + sum(data[:7])) & 0xFF


def decode(data: bytes | bytearray) -> dict:
  if len(data) != 8:
    raise ValueError("0x3E9 must contain 8 bytes")
  return {"turn": data[1] & 0x03, "reason": (data[2] >> 1) & 0x0F,
          "counter": (data[6] >> 4) & 0x0F, "checksum": data[7]}


def is_idle_template(address: int, bus: int, data: bytes) -> bool:
  return bool(address == ADDRESS and bus == BUS and len(data) == 8 and checksum(data) == data[7]
              and decode(data)["turn"] == 0)


def build(template: bytes, direction: str, counter: int) -> bytes:
  if direction not in TURN_REQUESTS:
    raise ValueError(f"unsupported turn request: {direction}")
  if not 0 <= counter <= 15:
    raise ValueError("invalid counter")
  if not is_idle_template(ADDRESS, BUS, template):
    raise ValueError("not an idle 0x3E9 template")
  data = bytearray(template)
  data[1] = (data[1] & 0xFC) | TURN_REQUESTS[direction]
  reason = CANCEL_REASON if direction == "cancel" else ACTIVE_REASON
  data[2] = (data[2] & 0xE1) | ((reason & 0x0F) << 1)
  data[6] = (data[6] & 0x0F) | ((counter & 0x0F) << 4)
  data[7] = checksum(data)
  return bytes(data)


class TurnSignalController:
  def __init__(self) -> None:
    self.template: bytes | None = None
    self.template_ns = 0
    self.active: dict | None = None

  def reset(self) -> None:
    self.active = None

  def observe(self, can_list, now_ns: int) -> None:
    for _mono, frames in can_list:
      for address, data, bus in frames:
        if is_idle_template(int(address), int(bus), bytes(data)):
          self.template = bytes(data)
          self.template_ns = int(now_ns)

  def _cancel(self, now_ns: int) -> list:
    if self.active is None:
      return []
    self.active["phase"] = "cancelling"
    self.active.setdefault("cancel_started_ns", int(now_ns))
    if self.active.get("cancel_attempts", 0) >= MAX_CANCEL_ATTEMPTS:
      self.active = None
      return []
    if now_ns - int(self.active.get("cancel_sent_ns", 0)) < SEND_INTERVAL_NS:
      return []
    if self.template is None or now_ns - self.template_ns > TEMPLATE_MAX_AGE_NS:
      self.active = None
      return []
    # The safety plugin requires template_counter + 1 for every frame; repeats are bounded by the 100 ms rate.
    counter = (decode(self.template)["counter"] + 1) % 16
    self.active["cancel_attempts"] = self.active.get("cancel_attempts", 0) + 1
    self.active["cancel_sent_ns"] = int(now_ns)
    return [(ADDRESS, build(self.template, "cancel", counter), BUS)]

  def update(self, intent: dict, cs, cc, now_ns: int, *, enabled: bool) -> list:
    """Return CAN frames to append; the host checks the can.tx claim and panda validates each frame."""
    direction = str(intent.get("direction", "none"))
    requested = bool(enabled and intent.get("valid") and intent.get("signalRequested")
                     and direction in ("left", "right") and bool(getattr(cc, "latActive", False)))
    physical_left = bool(getattr(cs, "leftBlinker", False))
    physical_right = bool(getattr(cs, "rightBlinker", False))
    physical_on = physical_left if direction == "left" else physical_right

    if self.active is not None:
      age = now_ns - int(self.active["started_ns"])
      same = (direction == self.active["direction"] and intent.get("sessionId") == self.active["sessionId"]
              and int(intent.get("routeRevision", 0)) == self.active["routeRevision"]
              and int(intent.get("requestId", 0)) == self.active["requestId"])
      if self.active["phase"] == "cancelling":
        # Cancellation owns the lamp until CarState confirms it is off, or the bound expires.
        if not physical_left and not physical_right:
          self.active = None
        elif now_ns - int(self.active.get("cancel_started_ns", now_ns)) > CANCEL_TOTAL_TIMEOUT_NS:
          self.active = None
        else:
          return self._cancel(now_ns)
      if self.active is None:
        pass
      elif not requested or not same or age > SESSION_TIMEOUT_NS:
        return self._cancel(now_ns)
      elif int(getattr(cs, "brakePressed", False)):
        return self._cancel(now_ns)
      else:
        self.active["held"] = physical_on
        if physical_on:
          return []
        elapsed = now_ns - int(self.active.get("action_sent_ns", 0))
        if elapsed > FEEDBACK_TIMEOUT_NS:
          return self._cancel(now_ns)
        if now_ns - int(self.active.get("action_sent_ns", 0)) < SEND_INTERVAL_NS:
          return []
        # One retry if the first frame produced no feedback.
        if self.active.get("action_attempts", 0) >= 2:
          return self._cancel(now_ns)
        if self.template is None or now_ns - self.template_ns > TEMPLATE_MAX_AGE_NS:
          return []
        counter = (decode(self.template)["counter"] + 1) % 16
        self.active["action_attempts"] = self.active.get("action_attempts", 0) + 1
        self.active["action_sent_ns"] = int(now_ns)
        return [(ADDRESS, build(self.template, direction, counter), BUS)]

    if not requested:
      return []
    if physical_on:
      # The driver already signals this side: do not inject a duplicate frame.
      return []
    if self.template is None or now_ns - self.template_ns > TEMPLATE_MAX_AGE_NS:
      return []
    self.active = {
      "direction": direction, "sessionId": intent.get("sessionId"),
      "routeRevision": int(intent.get("routeRevision", 0)), "requestId": int(intent.get("requestId", 0)),
      "started_ns": int(now_ns), "action_sent_ns": int(now_ns), "action_attempts": 1, "phase": "active",
    }
    counter = (decode(self.template)["counter"] + 1) % 16
    return [(ADDRESS, build(self.template, direction, counter), BUS)]
