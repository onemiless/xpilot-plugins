from opendbc.sunnypilot.car.tesla.values import TeslaFlagsSP
from openpilot.cereal import log
from openpilot.tesla.plugin import Plugin

from .controller import MixedControl


class TeslaMixedControl(Plugin):
  def __init__(self, CP, params):
    super().__init__(CP, params)
    self.enabled = params.get_bool(self.ENABLE_PARAM)

  def setup(self, CP_SP):
    if self.enabled:
      CP_SP.flags &= ~TeslaFlagsSP.COOP_STEERING.value

  @classmethod
  def lat_controller(cls, CP, CP_SP, CI, params):
    if params.get_bool(cls.ENABLE_PARAM):
      return MixedControl(CP, CP_SP, CI)

  @classmethod
  def event_filter(cls, ctx, CS, names, names_sp):
    return [log.OnroadEvent.EventName.invalidLkasSetting], []
