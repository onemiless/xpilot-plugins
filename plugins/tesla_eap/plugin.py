"""Tesla EAP mode: SP never engages (role:engagement.state), the host sets the panda STOCK_AP bit (role:stock.ap) so the
car's own Autopilot runs, and Navigate-on-Autopilot lane-change requests get a stalk tap (0x249) after the delay.
Panda permissions: plugin.json runtime rule (nav_lane_change's: cloned idle frame, RIGHT/LEFT only, cruise on, budgeted)."""
import math
from types import SimpleNamespace

from opendbc.can import CANParser
from opendbc.car import Bus
from opendbc.car.can_definitions import CanData
from opendbc.car.tesla.values import CANBUS, DBC
from openpilot.tesla.plugin import Plugin

from . import stalk
from .logic import ACTIVE_NAV, LEFT, REQUEST_FRESH_NS, AutoBlinker, request_dir

REQUEST_ADDRESS = 0x24A
PULSE_NS = 350_000_000  # t2can BLINKA_PULSE_MS
TEMPLATE_MAX_AGE_NS = 300_000_000


class _NeverEngage:
  """SP StateMachine interface that stays disabled."""

  def __init__(self, State, ET):
    self.State, self.ET = State, ET
    self.state, self.soft_disable_timer, self.current_alert_types = State.disabled, 0, [ET.PERMANENT]

  def update(self, events):
    self.state, self.current_alert_types = self.State.disabled, [self.ET.PERMANENT]
    return False, False


class TeslaEap(Plugin):
  def __init__(self, CP, params):
    super().__init__(CP, params)
    self.das = CANParser(DBC[CP.carFingerprint][Bus.party], [("DAS_status", math.nan)], CANBUS.autopilot_party)
    self.blinker = AutoBlinker()
    self.request, self.request_ns = 0, 0
    self.template, self.template_ns, self.sent_ns = None, 0, 0
    self.pulse_turn, self.pulse_until = 0, 0
    self.service_params(params)

  @classmethod
  def engagement(cls, selfdrive):
    from openpilot.selfdrive.selfdrived.events import ET
    from openpilot.selfdrive.selfdrived.state import State
    return SimpleNamespace(state_machine=_NeverEngage(State, ET), mads=None)

  def observe_can(self, can_list, now_ns: int) -> list:
    self.das.update(can_list)
    for mono_time, frames in can_list:
      for address, data, source in frames:
        if source != CANBUS.vehicle:
          continue
        if address == REQUEST_ADDRESS:
          self.request, self.request_ns = request_dir(data), mono_time
        elif address == stalk.ADDRESS and stalk.is_idle_template(data):
          self.template, self.template_ns = bytes(data), mono_time
    return []

  def control_sends(self, CS, CC, now_ns: int) -> list:
    das = self.das.vl["DAS_status"]
    noa = int(das["DAS_autopilotState"]) == ACTIVE_NAV
    req = self.request if now_ns - self.request_ns <= REQUEST_FRESH_NS else 0
    if direction := self.blinker.update(now_ns, noa, req, int(das["DAS_autoLaneChangeState"]), CS.leftBlinker or CS.rightBlinker):
      self.pulse_turn, self.pulse_until = (stalk.LEFT if direction == LEFT else stalk.RIGHT), now_ns + PULSE_NS
    if self.pulse_turn and (now_ns > self.pulse_until or not noa):
      self.pulse_turn = 0
    if (not self.pulse_turn or self.template is None or self.template_ns <= self.sent_ns or
        now_ns - self.template_ns > TEMPLATE_MAX_AGE_NS):
      return []
    self.sent_ns = self.template_ns
    return [CanData(stalk.ADDRESS, stalk.stalk_frame(self.template, self.pulse_turn), CANBUS.vehicle)]

  def service_params(self, params) -> None:
    self.blinker.delay_ns = (params.get("XpTeslaEapDelay", return_default=True) or 0) * 100_000_000
