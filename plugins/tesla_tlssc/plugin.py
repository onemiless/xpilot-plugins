"""Tesla TLSSC: re-sends the car's UI_autopilotControl (0x3FD mux 0, vehicle bus) with the TLSSC bits set while
sunnypilot is engaged (CC.enabled) and the car is at or below XpTeslaTlsscMax. Standalone; also publishes tlssc.state (traffic light colour, stop line).
Panda permissions: plugin.json runtime rule (cloned stock frame, only bits 38/39 forced, controls allowed, budgeted)."""
import math
import time

from opendbc.can import CANParser
from opendbc.car import Bus
from opendbc.car.can_definitions import CanData
from opendbc.car.tesla.values import CANBUS, DBC
from openpilot.tesla import plugins
from openpilot.tesla.plugin import Plugin

from .logic import ADDRESS, KPH, is_template, tlssc_frame

TEMPLATE_MAX_AGE_NS = 300_000_000
PUBLISH_NS = 100_000_000


class TeslaTlssc(Plugin):
  def __init__(self, CP, params):
    super().__init__(CP, params)
    self.road = CANParser(DBC[CP.carFingerprint][Bus.party], [("DAS_road", math.nan)], CANBUS.autopilot_party)
    self.template, self.template_ns, self.sent_ns, self.published_ns = None, 0, 0, 0
    self.active = False
    self.service_params(params)

  def observe_can(self, can_list, now_ns: int) -> list:
    self.road.update(can_list)
    for mono_time, frames in can_list:
      for address, data, source in frames:
        if source == CANBUS.vehicle and address == ADDRESS and is_template(data):
          self.template, self.template_ns = bytes(data), mono_time
    return []

  def publish_state(self, CS_SP) -> None:
    now_ns = time.monotonic_ns()
    if now_ns - self.published_ns < PUBLISH_NS:
      return
    self.published_ns = now_ns
    fresh = self.road.ts_nanos["DAS_road"]["DAS_trafficLightColor"] > 0
    road = self.road.vl["DAS_road"]
    payload = {"active": self.active, "color": int(road["DAS_trafficLightColor"]) if fresh else 0,
               "stop_m": float(road["DAS_stopLineDist"]) if fresh else 0.0}
    plugins.send_msg(self, "tlssc.state", payload, valid_for_ms=500)

  def control_sends(self, CS, CC, now_ns: int) -> list:
    self.active = CC.enabled and CS.vEgo * KPH <= self.max_kph
    if (not self.active or self.template is None or self.template_ns <= self.sent_ns or
        now_ns - self.template_ns > TEMPLATE_MAX_AGE_NS):
      return []
    self.sent_ns = self.template_ns
    frame = tlssc_frame(self.template)
    return [] if frame is None else [CanData(ADDRESS, frame, CANBUS.vehicle)]

  def service_params(self, params) -> None:
    self.max_kph = params.get("XpTeslaTlsscMax", return_default=True) or 0
