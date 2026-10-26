"""External compute: plugin-owned TCP bridge using pluginMsg advisory envelopes."""
from openpilot.tesla.plugin import Plugin


def _always(started, params, CP) -> bool:
  return True


class ExternalCompute(Plugin):

  @classmethod
  def processes(cls) -> list:
    from openpilot.system.manager.process import PythonProcess
    return [PythonProcess("externald", f"{__package__}.externald", _always)]
