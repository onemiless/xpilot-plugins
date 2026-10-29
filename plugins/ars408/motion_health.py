"""Preserve the old radar-unavailable gate when calibrated motion is missing."""
import math
import time

from opendbc.car import structs
from openpilot.selfdrive.locationd.helpers import PoseCalibrator
from .constants import MOTION_MAX_AGE_NS
from .motion import radar_yaw_rate_context
from .transmitter import MAX_ABS_YAW_RATE_DEG_S


class MotionAwareRadar:
  def __init__(self, radar, *, clock=time.monotonic_ns):
    self._radar = radar
    self._clock = clock
    self._calibrator = PoseCalibrator()
    self._cache = {}
    self._sample = (None, 0)
    self._last_error_ns = None

  def __getattr__(self, name):
    return getattr(self._radar, name)

  def radar_context(self, context, now_ns):
    self._sample = (None, 0)
    self._sample = radar_yaw_rate_context(context, now_ns, self._calibrator, self._cache)

  def update(self, can_packets):
    result = self._radar.update(can_packets)
    now = self._clock()
    yaw, timestamp = self._sample
    healthy = (yaw is not None and math.isfinite(yaw) and
               abs(math.degrees(yaw)) <= MAX_ABS_YAW_RATE_DEG_S and timestamp > 0 and
               0 <= now - timestamp <= MOTION_MAX_AGE_NS)
    if not healthy:
      if result is None and (self._last_error_ns is None or now - self._last_error_ns >= 50_000_000):
        result = structs.RadarData()
      if result is not None:
        result.errors.radarUnavailableTemporary = True
        self._last_error_ns = now
    return result
