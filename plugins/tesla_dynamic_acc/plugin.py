"""Tesla Dynamic ACC: speed-based longitudinal owner proposals (G8 role:long.owner)."""
from openpilot.tesla.plugin import Plugin

from .logic import desired_owner


class _Policy:
  def __init__(self, params):
    # read once per drive, like every plugin setting
    self.high = float(params.get("XpTeslaDynamicAccHigh", return_default=True) or 80)
    self.low = float(params.get("XpTeslaDynamicAccLow", return_default=True) or 70)
    from openpilot.tesla import plugins
    self.msgs = plugins.PluginMsgs()

  def desired(self, ctx, CS):
    return desired_owner(CS.vEgo, self.high, self.low, self.msgs.latest("tlssc.state", "tesla_tlssc"))


class TeslaDynamicAcc(Plugin):
  @classmethod
  def long_owner(cls, CP, CP_SP, params):
    return _Policy(params) if CP.brand == "tesla" else None
