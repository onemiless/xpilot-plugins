from openpilot.selfdrive.modeld.modeld import run_with_model_source
from openpilot.tesla.model_source import ModelSource
from openpilot.tesla.store import plugin_state
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
