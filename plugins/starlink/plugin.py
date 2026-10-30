"""Optional internet connectivity, independent of driving roles."""
from openpilot.tesla.plugin import Plugin
from openpilot.tesla.plugin_params import ParamsView
from . import session


def should_run(started, params, CP):
  settings = ParamsView(params)
  if started or not params.get_bool('IsOffroad') or params.get_bool('IsEngaged'):
    session.clear()
    if settings.get_bool('ExtStarlinkConnect'):
      settings.put_bool('ExtStarlinkConnect', False, block=True)
    return False
  return settings.get_bool('ExtStarlinkConnect') and session.requested()


class Starlink(Plugin):
  @classmethod
  def processes(cls):
    from .process import RestartingProcess
    return [RestartingProcess('starlinkd', f'{__package__}.service', should_run)]
