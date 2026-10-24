"""Lane-change policy: let Tesla's factory verdict and the phone blind spot veto a change.

Reads pluginMsg only (no new cereal). It never starts a lane change by itself: the physical lamp is requested by
the navassist process and SP's own DesireHelper/ALC remains the start authority."""
from __future__ import annotations

import time

from .phone import PhoneBlindspotAssist
from .settings import read as read_settings

POLL_S = 0.10


def _side(direction) -> str:
  raw = getattr(direction, "raw", direction)
  try:
    return "left" if int(raw) == 1 else "right"
  except (TypeError, ValueError):
    return "left" if "left" in str(raw).lower() else "right"


class NavLanePolicy:
  def __init__(self, params):
    self.params = params
    self.settings = read_settings(params)
    self._msgs = None
    self._last = {}
    self._last_poll = -1.0
    self._phone = PhoneBlindspotAssist()
    self._phone_t = 0.0
    self._phone_states = None

  def _poll(self) -> dict:
    now = time.monotonic()
    if now - self._last_poll < POLL_S:
      return self._last
    self._last_poll = now
    try:
      if self._msgs is None:
        from openpilot.tesla.plugins import PluginMsgs
        self._msgs = PluginMsgs()
      out = {}
      for kind in ("oem.lanes", "lanes.intent", "lanes.topology"):
        fresh = self._msgs.fresh(kind)
        out[kind] = next(iter(fresh.values()), None) if fresh else None
      self._last = out
    except Exception:
      self._last = {}
    if now - self._phone_t >= 0.1:
      self._phone_t = now
      self._phone_states = self._phone.read(time.monotonic_ns())
    return self._last

  def allow(self, carstate, direction, default: bool) -> bool:
    side = _side(direction)
    last = self._poll()
    oem = last.get("oem.lanes") or {}
    topology = last.get("lanes.topology") or {}
    intent = last.get("lanes.intent") or {}
    if bool(oem.get(f"{side}ExplicitBlock")):
      return False
    if bool(oem.get(f"{side}AvailabilityBlock")) and not bool(topology.get("valid")):
      return False
    if self.settings.turn_signal_enabled and self._phone_states is not None:
      want = 0 if side == "left" else 1
      if self._phone.enabled and len(self._phone_states) == 2 and self._phone_states[want].blocked:
        return False
    if intent.get("signalRequested"):
      if str(intent.get("direction")) != side:
        return False
      return bool(intent.get("spLaneChangeReady")) and bool(intent.get("laneChangeAllowed"))
    return default
