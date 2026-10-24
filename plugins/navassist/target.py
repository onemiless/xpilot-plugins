"""long_target hook: the bounded navigation speed ceiling before a maneuver."""
from __future__ import annotations

import time

from .settings import read as read_settings
from .speed import NavigationSpeedCeiling


class NavSpeedTarget:
  def __init__(self, params):
    self.params = params
    self.settings = read_settings(params)
    self.ceiling = NavigationSpeedCeiling(self.settings)
    self._msgs = None
    self._next_settings = 0.0

  def _fresh(self) -> dict:
    try:
      if self._msgs is None:
        from openpilot.tesla.plugins import PluginMsgs
        self._msgs = PluginMsgs()
      values = self._msgs.fresh("nav.state")
      return next(iter(values.values())) if values else {}
    except Exception:
      return {}

  def update(self, sm, v_ego: float, v_cruise: float):
    now = time.monotonic()
    if now >= self._next_settings:
      self._next_settings = now + 1.0
      try:
        self.settings = read_settings(self.params)
        self.ceiling.settings = self.settings
      except Exception:
        pass
    try:
      cs = sm["carState"]
      cc = sm["carControl"]
      override = bool(cs.gasPressed or cs.brakePressed or cc.cruiseControl.override)
      planner_ready = bool(cc.enabled)
    except (KeyError, AttributeError):
      override, planner_ready = False, True
    return self.ceiling.update(self._fresh(), v_ego=v_ego, v_cruise=v_cruise, enabled=True,
                               driver_override=override, planner_ready=planner_ready)
