"""NavAssist: phone navigation as an xpilot plugin.

- navassistd receives canonical v3 phone navigation over UDP, reads modelV2 lane topology, and publishes
  pluginMsg state for the HUD, the lane-change policy and the speed ceiling.
- The card-side hook clones Tesla's own 0x3E9 DAS_bodyControls frame to request one physical turn signal; the
  panda plugin 'turn_signal' validates every frame. SP's DesireHelper/ALC remains the only lane-change start
  authority and still checks the blind spot, road edge and configured automatic mode.
- No new cereal messages, no steering/acceleration command, no phone curvature."""
from openpilot.tesla.plugin import Plugin

from .settings import read as read_settings
from .turnsignal import TurnSignalController


def _always(started, params, CP) -> bool:
  return True


class NavAssist(Plugin):
  def __init__(self, CP, params):
    super().__init__(CP, params)
    self.params = params
    self.settings = read_settings(params)
    self.turns = TurnSignalController()
    self._msgs = None

  def setup(self, CP_SP) -> None:
    # The panda plugin only activates when the car initialized with the vehicle-bus harness and this bit is set.
    from opendbc.sunnypilot.car.tesla.values import TeslaFlagsSP
    if CP_SP.flags & TeslaFlagsSP.HAS_VEHICLE_BUS:
      CP_SP.safetyParam |= int(self.MANIFEST.safety["param_bit"], 0)

  def service_params(self, params) -> None:
    self.params = params
    self.settings = read_settings(params)

  def observe_can(self, can_list, now_ns: int) -> list:
    self.turns.observe(can_list, now_ns)
    return []

  def _fresh_intent(self) -> dict:
    try:
      if self._msgs is None:
        from openpilot.tesla.plugins import PluginMsgs
        self._msgs = PluginMsgs()
      values = self._msgs.fresh("lanes.intent")
      return next(iter(values.values())) if values else {}
    except Exception:
      return {}

  def control_sends(self, CS, CC, now_ns: int) -> list:
    # enabled=False still drives an active request through its cancel path.
    return self.turns.update(self._fresh_intent(), CS, CC, now_ns, enabled=self.settings.turn_signal_enabled)

  @classmethod
  def processes(cls) -> list:
    from openpilot.system.manager.process import PythonProcess
    return [PythonProcess("navassistd", f"{__package__}.process", _always)]

  @classmethod
  def lane_change_policy(cls, params):
    from .policy import NavLanePolicy
    return NavLanePolicy(params)

  @classmethod
  def long_target(cls, CP, params):
    from .target import NavSpeedTarget
    return NavSpeedTarget(params)
