"""TesNav glue. The Android/iOS App supplies observations, never actuator commands."""
from openpilot.tesla.plugin import Plugin


def always(started, params, CP):
  return True


class TesNav(Plugin):
  SERVICES = ('navInstruction', 'carControl', 'carState', 'deviceState')

  @classmethod
  def processes(cls):
    from openpilot.system.manager.process import PythonProcess
    return [PythonProcess('tesnavd', f'{__package__}.service', always)]

  @classmethod
  def long_target(cls, CP, params):
    if CP.brand != 'tesla' or not CP.openpilotLongitudinalControl:
      return None
    from .speed import NavigationSpeed
    return NavigationSpeed(params)
