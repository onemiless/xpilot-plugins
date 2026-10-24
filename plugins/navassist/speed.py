"""Bounded navigation speed ceiling before a supported maneuver.

Only lowers the common cruise target; never requests a stop or acceleration. The host's long_target merges this
with SP's own targets by taking the minimum, so lead/FCW/model limits still win."""
from __future__ import annotations

import math

from .settings import NavSettings

COMFORT_BRAKE = 1.2
ACTUATION_DELAY_S = 1.0
ADMISSION_MARGIN_M = 5.0
ACTIVATION_MARGIN_M = 25.0
MAX_MANEUVER_DISTANCE_M = 2_000.0
MAX_NAV_SPEED_MPS = 60.0 / 3.6
MIN_TARGET_SPEED_MPS = 2.0
RELEASE_ACCEL_MPS2 = 1.0
DT = 0.05

TARGET_SPEEDS = {
  "slight_left": 8.0, "slight_right": 8.0,
  "turn_left": 5.0, "turn_right": 5.0,
  "sharp_left": 3.5, "sharp_right": 3.5,
  "u_turn_left": 2.5, "u_turn_right": 2.5,
  "exit_left": 10.0, "exit_right": 10.0,
  "ramp_left": 10.0, "ramp_right": 10.0,
  "roundabout": 6.0,
}


class NavigationSpeedCeiling:
  def __init__(self, settings: NavSettings):
    self.settings = settings
    self.output = None
    self.is_active = False
    self._event_key = None
    self._admitted = False
    self._rejected = False
    self._activated = False
    self._target = 0.0
    self._required = 0.0
    self._releasing = False

  def reset(self) -> None:
    self.output = None
    self.is_active = False
    self._event_key = None
    self._admitted = self._rejected = self._activated = False
    self._releasing = False

  @staticmethod
  def _required_distance(v_ego: float, target: float) -> float:
    braking = max(0.0, v_ego * v_ego - target * target) / (2.0 * COMFORT_BRAKE)
    return braking + v_ego * ACTUATION_DELAY_S

  def _target_for(self, nav: dict) -> float | None:
    default = TARGET_SPEEDS.get(str(nav.get("maneuver", "none")))
    if default is not None and str(nav.get("maneuver")) in ("turn_left", "turn_right"):
      default = self.settings.turn_speed_kph / 3.6
    if default is None:
      return None
    advisory = float(nav.get("advisorySpeedMps") or 0.0)
    if advisory > 0.0:
      return max(MIN_TARGET_SPEED_MPS, min(default, advisory))
    return default

  def _release(self, v_cruise: float) -> None:
    self.is_active = False
    if self.output is None:
      self._releasing = False
      return
    self.output = min(v_cruise, self.output + RELEASE_ACCEL_MPS2 * DT)
    self._releasing = self.output < v_cruise - 1e-3
    if not self._releasing:
      self.output = None

  def _pause(self) -> None:
    if self._activated:
      self._rejected = True
    self._admitted = False

  def update(self, nav: dict, *, v_ego: float, v_cruise: float, enabled: bool,
             driver_override: bool, planner_ready: bool = True) -> float | None:
    if (not enabled or not self.settings.turn_slowdown_enabled or driver_override or not planner_ready
        or not math.isfinite(v_ego) or not 0.0 <= v_ego <= MAX_NAV_SPEED_MPS):
      self._pause()
      self._release(v_cruise)
      return self.output
    if not nav.get("valid") or nav.get("stale"):
      self._pause()
      self._release(v_cruise)
      return self.output
    target = self._target_for(nav)
    distance = float(nav.get("distanceM", 0.0))
    key = (str(nav.get("sessionId", "")), int(nav.get("routeRevision", 0)), int(nav.get("maneuverEventId", 0)))
    if (target is None or key[2] == 0 or not math.isfinite(distance)
        or not 0.0 <= distance <= MAX_MANEUVER_DISTANCE_M):
      self._pause()
      self._release(v_cruise)
      return self.output
    required = self._required_distance(v_ego, target)
    if key != self._event_key or (not self._admitted and not self._rejected):
      self._event_key = key
      self._target = target
      self._required = required
      self._admitted = distance >= required + ADMISSION_MARGIN_M
      self._rejected = not self._admitted
      self._activated = False
    if self._rejected or not self._admitted:
      self._release(v_cruise)
      return self.output
    if not self._activated:
      self._required = max(self._required, required)
      if distance < self._required:
        self._rejected = True
        self._release(v_cruise)
        return self.output
      if distance > self._required + ACTIVATION_MARGIN_M:
        self._release(v_cruise)
        return self.output
      self._activated = True
    self.is_active = True
    self._releasing = False
    self.output = min(v_cruise, self._target)
    return self.output
