"""Tesla Dynamic ACC: speed-based longitudinal owner proposals (G8 role:long.owner)."""
from openpilot.tesla.plugin import Plugin

import time

from .logic import TouchToggle, desired_owner

TOUCH_ADDRESS, VEHICLE_BUS, FRESH_NS = 0x3DF, 1, 100_000_000  # UI_status2 on the vehicle bus


class _Policy:
  def __init__(self, params):
    # read once per drive, like every plugin setting
    self.high = float(params.get("XpTeslaDynamicAccHigh", return_default=True) or 80)
    self.low = float(params.get("XpTeslaDynamicAccLow", return_default=True) or 70)
    from openpilot.cereal import messaging
    from openpilot.tesla import plugins
    self.messaging, self.msgs = messaging, plugins.PluginMsgs()
    self.touch = TouchToggle() if params.get_bool("XpTeslaDynamicAccTouch") else None
    self.can = messaging.sub_sock("can") if self.touch else None

  def desired(self, ctx, CS):
    if self.touch is not None:
      now = time.monotonic_ns()
      manual = None
      for event in self.messaging.drain_sock(self.can):
        if 0 <= now - event.logMonoTime <= FRESH_NS:
          for frame in event.can:
            if frame.address == TOUCH_ADDRESS and frame.src == VEHICLE_BUS and len(frame.dat) == 8:
              manual = self.touch.update(frame.dat[3], ctx.owner, ctx.enabled, ctx.available)
      manual = self.touch.update(None, ctx.owner, ctx.enabled, ctx.available) if manual is None else manual
      if manual is not None:
        return manual
    return desired_owner(CS.vEgo, self.high, self.low, self.msgs.latest("tlssc.state", "tesla_tlssc"))


class TeslaDynamicAcc(Plugin):
  @classmethod
  def long_owner(cls, CP, CP_SP, params):
    return _Policy(params) if CP.brand == "tesla" else None
