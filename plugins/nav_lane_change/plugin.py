"""Navigation lane change: taps the turn stalk (0x249) when the phone model (lean_mici, route active) predicts the driver's
blinker for 1 s. SP's own lane change (blinker -> auto lane change) does the rest; blind spot, road edge and speed checks stay SP's.
Panda permissions: plugin.json runtime rule (cloned idle frame, turn value RIGHT/LEFT only, engaged only, budgeted)."""
from opendbc.car.can_definitions import CanData
from opendbc.car.tesla.values import CANBUS
from openpilot.tesla.plugin import Plugin

from .logic import LEFT, MIN_SPEED, Decider, TurnSignal
from . import stalk

PULSE_NS = 600_000_000      # hold the stalk this long (one cloned frame per fresh car frame)
TEMPLATE_MAX_AGE_NS = 300_000_000


class NavLaneChange(Plugin):
  def __init__(self, CP, params):
    super().__init__(CP, params)
    from openpilot.cereal import messaging
    from openpilot.tesla import plugins
    self.sm = messaging.SubMaster(['modelV2'])
    self.msgs = plugins.PluginMsgs()
    self.decider = Decider()
    self.turn = TurnSignal()
    self.template, self.template_ns, self.sent_ns = None, 0, 0
    self.pulse_turn, self.pulse_until = 0, 0
    self.service_params(params)

  def observe_can(self, can_list, now_ns: int) -> list:
    for mono_time, frames in can_list:
      for address, data, source in frames:
        if source == CANBUS.vehicle and address == stalk.ADDRESS and stalk.is_idle_template(data):
          self.template, self.template_ns = bytes(data), mono_time
    return []

  def control_sends(self, CS, CC, now_ns: int) -> list:
    self.sm.update(0)
    model = self.sm['modelV2']
    meta = model.meta
    ready = (CC.latActive and CS.vEgo >= MIN_SPEED and not (CS.steeringPressed or CS.gasPressed or CS.brakePressed) and
             not (CS.leftBlinker or CS.rightBlinker) and str(meta.laneChangeState) == 'off' and not self.pulse_turn)
    nav = self.msgs.latest('lean_mici.navigation', 'lean_mici')
    direction = self.decider.update(now_ns, nav, model.big, ready, CS.leftBlindspot, CS.rightBlindspot)
    if self.turn_enabled and not direction and not self.pulse_turn:
      direction = self.turn.update(nav, CS.vEgo, CS.leftBlinker or CS.rightBlinker)
    if direction:
      self.pulse_turn, self.pulse_until = (stalk.LEFT if direction == LEFT else stalk.RIGHT), now_ns + PULSE_NS
    if self.pulse_turn and (now_ns > self.pulse_until or (CS.vEgo >= MIN_SPEED and (not CC.latActive or CS.steeringPressed))):
      self.pulse_turn = 0
    if (not self.pulse_turn or self.template is None or self.template_ns <= self.sent_ns or
        now_ns - self.template_ns > TEMPLATE_MAX_AGE_NS):
      return []
    self.sent_ns = self.template_ns
    return [CanData(stalk.ADDRESS, stalk.stalk_frame(self.template, self.pulse_turn), CANBUS.vehicle)]

  def service_params(self, params) -> None:
    self.decider.threshold = (params.get("XpNavLaneChangeIntent", return_default=True) or 60) / 100
    self.turn_enabled = params.get_bool("XpNavTurnSignal")
    self.turn.distance_m = params.get("XpNavTurnSignalDistance", return_default=True) or 80
