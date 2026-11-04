"""Predictive accel boost (sunnypilot #2089) on the host's role:long.accel_boost hook. The host lets it raise only the
model's acceleration candidate (capped); the planner keeps the minimum of lead following, cruise and model."""
from openpilot.tesla.plugin import Plugin

from .boost import AccelBoost


class AccelBoostPlugin(Plugin):
  @classmethod
  def accel_boost(cls, CP, CP_SP, params):
    return AccelBoost() if CP.openpilotLongitudinalControl else None  # as upstream: openpilot longitudinal only
