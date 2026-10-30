"""TN longitudinal (TN-NoDEC) as an xpilot long_planner plugin (role:long.planner).

Runs the TN planner (legacy cruise-obstacle MPC + TN acceleration controller, with its own lead following) on the
same sm as SP's planner, and hands its trajectories to the host, which replaces SP's plan (aTarget clipped to
ACCEL_MIN/MAX; SP's MPC still decides FCW). The acados solvers are prebuilt binaries (see build_native.py), so the
manifest's platform must match the device. Not ported: TN's controlsd stopping policy (no plugin hook for it).

Right-scroll follow distance:
- The card plugin reads Tesla STW_ACTN_RQ.DTR_Dist_Rq (vehicle bus) and stores a 0..6 level in
  ExtTnScrollLevel (two-frame confirmation; 255/SNA ignored).
- The planner reads that level and applies the seven t_follow values below to the TN runtime tuning. The
  other TN tuning values (comfort brake, stop distance, costs, ...) are unchanged.
- Missing/unknown level keeps the normal TN personality tuning.
"""
from __future__ import annotations

import time
from dataclasses import replace
from types import SimpleNamespace

from opendbc.car.tesla.values import CANBUS
from openpilot.tesla.plugin import Plugin


STW_ACTN_RQ_ADDR = 568
SCROLL_LEVEL_PARAM = "ExtTnScrollLevel"
DTR_LEVEL = {0: 0, 33: 1, 66: 2, 100: 3, 133: 4, 166: 5, 200: 6}

# index: Tesla ACC_DIST_1..7. Values are t_follow seconds.
FOLLOW_TIME = (1.05, 1.25, 1.45, 1.60, 1.75, 1.90, 2.10)
CONFIRM_FRAMES = 2
MAX_T_FOLLOW_SLEW = 0.20  # seconds of gap per second


class _TnPlanner:
  def __init__(self, CP, CP_SP, params):
    from .registry import TN_NO_DEC_BACKEND
    from .tn_no_dec.planner import LongitudinalPlanner
    self.params = params
    self.planner = LongitudinalPlanner(CP, CP_SP)
    from openpilot.tesla import plugins
    self.planner.plugin_long_targets = plugins.long_targets(CP, params)
    self.planner.mpc.configure_runtime_tuning(params, TN_NO_DEC_BACKEND)
    self._level = -1
    self._slewed_t_follow: float | None = None
    self._last_slew_time: float | None = None
    self._last_applied_ms = -1
    self._last_applied_write = 0.0
    self._next_level_read = 0.0
    # TN's update refresh replaces runtime_tuning from the tuning controller.
    # Wrap that one method so every refresh applies the optional scroll level.
    self._orig_refresh = self.planner.mpc._refresh_runtime_tuning
    self.planner.mpc._refresh_runtime_tuning = self._refresh

  def _read_level(self) -> None:
    now = time.monotonic()
    if now < self._next_level_read:
      return
    self._next_level_read = now + 0.10
    try:
      value = int(self.params.get(SCROLL_LEVEL_PARAM, return_default=True))
    except (TypeError, ValueError):
      value = -1
    self._level = value if 0 <= value < len(FOLLOW_TIME) else -1

  def _publish_applied(self, now: float, applied_s: float | None) -> None:
    applied_ms = -1 if applied_s is None else int(round(applied_s * 1000.0))
    if applied_ms != self._last_applied_ms and now - self._last_applied_write >= 1.0:
      try:
        self.params.put("ExtTnFollowApplied", applied_ms)
        self._last_applied_ms = applied_ms
        self._last_applied_write = now
      except Exception:
        pass

  def _refresh(self) -> None:
    self._orig_refresh()
    self._read_level()
    if self._level < 0:
      self._slewed_t_follow = None
      self._publish_applied(time.monotonic(), None)
      return

    target = FOLLOW_TIME[self._level]
    if self._slewed_t_follow is None:
      # Start from the currently active TN follow time, not the new target,
      # so the first wheel detent also ramps instead of stepping.
      self._slewed_t_follow = float(getattr(self.planner.mpc.runtime_tuning, "t_follow_standard", target))
    now = time.monotonic()
    dt = 0.0 if self._last_slew_time is None else min(0.10, max(0.0, now - self._last_slew_time))
    self._last_slew_time = now
    step = MAX_T_FOLLOW_SLEW * dt
    self._slewed_t_follow += max(-step, min(step, target - self._slewed_t_follow))

    t = float(self._slewed_t_follow)
    self.planner.mpc.runtime_tuning = replace(
      self.planner.mpc.runtime_tuning,
      t_follow_aggressive=t,
      t_follow_standard=t,
      t_follow_relaxed=t,
    )
    self._publish_applied(now, t)

  def update(self, sm, sp):
    p = self.planner
    p.update(sm)
    return SimpleNamespace(speeds=p.v_desired_trajectory, accels=p.a_desired_trajectory, jerks=p.j_desired_trajectory,
                           a_target=float(p.output_a_target), should_stop=bool(p.output_should_stop))


class TnLong(Plugin):
  def __init__(self, CP, params):
    super().__init__(CP, params)
    self.params = params
    self.enabled = bool(getattr(CP, "openpilotLongitudinalControl", False))
    self.last_level: int | None = None
    self.pending_level: int | None = None
    self.pending_count = 0

  def observe_can(self, can_list, now_ns: int) -> list:
    if not self.enabled:
      return []
    for _mono_time, frames in can_list:
      for address, data, source in frames:
        if address != STW_ACTN_RQ_ADDR or source != CANBUS.vehicle or len(data) < 2:
          continue
        level = DTR_LEVEL.get(int(data[1]))
        if level is not None:
          self._observe_level(level)
    return []

  def _observe_level(self, level: int) -> None:
    if self.last_level is None:
      self.last_level = level
      return
    if level == self.last_level:
      self.pending_level = None
      self.pending_count = 0
      return
    if level == self.pending_level:
      self.pending_count += 1
    else:
      self.pending_level = level
      self.pending_count = 1
    if self.pending_count < CONFIRM_FRAMES:
      return
    self.last_level = level
    self.pending_level = None
    self.pending_count = 0
    if self.params.get(SCROLL_LEVEL_PARAM, return_default=True) != level:
      self.params.put(SCROLL_LEVEL_PARAM, level)

  @classmethod
  def long_planner(cls, CP, CP_SP, params):
    return _TnPlanner(CP, CP_SP, params)
