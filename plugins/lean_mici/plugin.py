from openpilot.tesla.plugin import Plugin
from .prepare import ready


def onroad(started, params, CP):
  return started


class LeanMici(Plugin):
  @classmethod
  def prepare_install(cls):
    return ready()

  @classmethod
  def processes(cls):
    from openpilot.system.manager.process import PythonProcess
    return [PythonProcess('lean_modeld', f'{__package__}.runner', onroad)]
