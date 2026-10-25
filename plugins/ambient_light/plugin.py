"""Ambient light plugin: blindspot warning on the cabin ambient lights (0x679, vehicle bus).
Panda permissions: plugin.json runtime rules. Params: TeslaBlindspotAmbient*."""
from opendbc.can import CANParser
from opendbc.car import Bus
from opendbc.car.tesla.values import CANBUS, DBC
from openpilot.tesla.plugin import Plugin
from .controller import AmbientLightingController

LIGHTING_STALE_NS = 2_000_000_000
LIGHTING_MESSAGE = "ID3F5VCFRONT_lighting"
DISPLAY_MESSAGE = "UI_status2"


class AmbientLight(Plugin):

  def __init__(self, CP, params):
    super().__init__(CP, params)
    self.ambient = AmbientLightingController()
    try:
      self.lighting = CANParser(DBC[CP.carFingerprint][Bus.adas], [(LIGHTING_MESSAGE, float("nan")), (DISPLAY_MESSAGE, float("nan"))],
                                CANBUS.vehicle)
    except KeyError:
      self.lighting = None
    self.ambient.service_params(params)

  def _night(self, now_ns: int) -> bool:
    if self.lighting is None:
      return True
    ts = self.lighting.ts_nanos.get(DISPLAY_MESSAGE, {}).get("UI_displayInDarkMode", 0)
    if ts > 0 and 0 <= now_ns - ts <= LIGHTING_STALE_NS:
      return bool(self.lighting.vl[DISPLAY_MESSAGE]["UI_displayInDarkMode"])
    ts = max(self.lighting.ts_nanos.get(LIGHTING_MESSAGE, {}).values(), default=0)
    if ts <= 0 or not 0 <= now_ns - ts <= LIGHTING_STALE_NS:
      return True
    v = self.lighting.vl[LIGHTING_MESSAGE]
    low_beam = int(v["VCFRONT_lowBeamLeftStatus"]) == 1 or int(v["VCFRONT_lowBeamRightStatus"]) == 1
    return low_beam and not bool(v["VCFRONT_lowBeamsOnForDRL"])

  def observe_can(self, can_list, now_ns: int) -> list:
    if self.lighting is not None:
      self.lighting.update(can_list)
    for mono_time, frames in can_list:
      for address, data, source in frames:
        self.ambient.observe_frame(mono_time, address, data, source)
    return self.ambient.take_can_sends(now_ns)

  def control_sends(self, CS, CC, now_ns: int) -> list:
    self.ambient.update_blindspot(2 if CS.leftBlindspot else 0, 2 if CS.rightBlindspot else 0, self._night(now_ns), now_ns)
    return self.ambient.take_can_sends(now_ns)

  def service_params(self, params) -> None:
    self.ambient.service_params(params)
