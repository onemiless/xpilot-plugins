"""Auto speed plugin: follows the car's own speed limit (DAS_fusedSpeedLimit, upstream carStateSP.speedLimit)
plus an offset by sending single right scroll-wheel ticks (0x3C2) cloned from the car's idle frame.
Panda permissions: plugin.json runtime rules. Params: TeslaAutoSpeedLimit(Offset)."""
from types import SimpleNamespace

from opendbc.can import CANParser
from opendbc.car import Bus
from opendbc.car.tesla.values import CANBUS, DBC
from opendbc.sunnypilot.car.tesla.values import TeslaFlagsSP
from openpilot.tesla.plugin import Plugin
from .controller import AutoSpeedController

SWITCH_ADDRESS = 0x3C2
RESUME_GESTURE_NS = 1_500_000_000  # opposite wheel ticks within this window resume auto speed
KPH_TO_MS, MPH_TO_MS = 1 / 3.6, 0.44704


class AutoSpeed(Plugin):

  def __init__(self, CP, params):
    super().__init__(CP, params)
    self.controller = AutoSpeedController()
    # State the controller reads; filled from CAN and carStateSP below.
    self.state = SimpleNamespace(out=None, tesla_speed_limit_assist_enabled=False, tesla_speed_limit_target=0.0,
                                 tesla_speed_limit_target_valid=False, tesla_speed_units="KPH", tesla_autopilot_active=False,
                                 tesla_speed_button_template=None, tesla_speed_button_template_nanos=0,
                                 tesla_manual_speed_adjustment_counter=0, tesla_speed_auto_resume_gesture_counter=0)
    self.offset = 0
    self._up_ns = self._down_ns = 0
    self._wait_idle = False
    dbc = DBC[CP.carFingerprint][Bus.party]
    self.party = CANParser(dbc, [("DI_state", float("nan"))], CANBUS.party)
    self.ap_party = CANParser(dbc, [("DAS_status", float("nan"))], CANBUS.autopilot_party)
    self.service_params(params)

  def setup(self, CP_SP) -> None:
    # openpilot longitudinal and the vehicle-bus harness are required to see and send the wheel frame
    if self.CP.openpilotLongitudinalControl and CP_SP.flags & TeslaFlagsSP.HAS_VEHICLE_BUS:
      self.controller.configured = True

  def _wheel_frame(self, data: bytes, now_ns: int) -> None:
    s = self.state
    if len(data) != 8 or (data[0] & 0x03) != 1:
      return
    raw = data[3] & 0x3F
    if raw == 0:
      s.tesla_speed_button_template, s.tesla_speed_button_template_nanos, self._wait_idle = bytes(data), now_ns, False
      return
    if self._wait_idle:  # trailing frames of a finished resume gesture
      return
    direction = 1 if (raw - 0x40 if raw & 0x20 else raw) > 0 else -1
    s.tesla_manual_speed_adjustment_counter += 1
    opposite = self._down_ns if direction > 0 else self._up_ns
    if opposite and now_ns - opposite <= RESUME_GESTURE_NS:
      s.tesla_speed_auto_resume_gesture_counter += 1
      self._up_ns = self._down_ns = 0
      self._wait_idle = True
    elif direction > 0:
      self._up_ns, self._down_ns = now_ns, 0
    else:
      self._down_ns, self._up_ns = now_ns, 0

  def observe_can(self, can_list, now_ns: int) -> list:
    self.party.update(can_list)
    self.ap_party.update(can_list)
    for mono_time, frames in can_list:
      for address, data, source in frames:
        if source == CANBUS.vehicle and address == SWITCH_ADDRESS:
          self._wheel_frame(data, mono_time)
    if self.party.ts_nanos["DI_state"]["DI_speedUnits"] > 0:  # keep the last known units until DI_state arrives
      self.state.tesla_speed_units = "KPH" if int(self.party.vl["DI_state"]["DI_speedUnits"]) == 1 else "MPH"  # DBC: 0 MPH, 1 KPH
    # Only idle/available AP states 0..2 allow wheel injection
    self.state.tesla_autopilot_active = int(self.ap_party.vl["DAS_status"]["DAS_autopilotState"]) not in (0, 1, 2)
    return []

  def publish_state(self, CS_SP) -> None:
    unit = MPH_TO_MS if self.state.tesla_speed_units == "MPH" else KPH_TO_MS
    target = CS_SP.speedLimit + self.offset * unit
    self.state.tesla_speed_limit_target_valid = CS_SP.speedLimit > 0 and target > 0
    self.state.tesla_speed_limit_target = target if self.state.tesla_speed_limit_target_valid else 0.0

  def control_sends(self, CS, CC, now_ns: int) -> list:
    self.state.out = CS
    return self.controller.update(CC, self.state, now_ns)

  def service_params(self, params) -> None:
    self.state.tesla_speed_limit_assist_enabled = params.get_bool("TeslaAutoSpeedLimit")
    self.offset = params.get("TeslaAutoSpeedLimitOffset", return_default=True)
