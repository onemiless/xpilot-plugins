"""Calibrated radar motion extraction from the working C3XL card adapter.

Pure snapshot consumer: the caller supplies its PoseCalibrator and optional cache.
No subscription, CAN transmission or fallback to uncalibrated CarState.yawRate.
"""
import math

from .constants import MOTION_MAX_AGE_NS

RADAR_CALIBRATION_MAX_AGE_NS = 2_000_000_000


def radar_yaw_rate_context(sm, now_ns: int, calibrator, cache: dict | None = None) -> tuple[float | None, int]:
  for service, max_age in (("deviceMotion", MOTION_MAX_AGE_NS), ("extrinsicsCalibration", RADAR_CALIBRATION_MAX_AGE_NS)):
    if (not sm.seen[service] or not sm.valid[service] or sm.logMonoTime[service] <= 0 or
        not 0 <= now_ns - sm.logMonoTime[service] <= max_age):
      return None, 0

  motion = sm["deviceMotion"]
  calibration = sm["extrinsicsCalibration"]
  angular = motion.angularVelocityDevice
  rpy = tuple(calibration.rpyCalib)
  xyz = (angular.x, angular.y, angular.z)
  if (not (motion.inputsOK and motion.sensorsOK and motion.posenetOK and angular.valid) or
      motion.timestamp <= 0 or not 0 <= now_ns - motion.timestamp <= MOTION_MAX_AGE_NS or
      len(rpy) != 3 or not all(math.isfinite(v) for v in rpy) or
      not all(math.isfinite(v) for v in xyz)):
    return None, 0

  # Check freshness/health above even when returning a cached measurement.
  calibration_key = (sm.logMonoTime["extrinsicsCalibration"], rpy, str(calibration.calStatus))
  if cache is None or cache.get("calibration_key") != calibration_key:
    calibrator.feed_extrinsics_calibration(calibration)
    if cache is not None:
      cache["calibration_key"] = calibration_key
  if not calibrator.calib_valid:
    return None, 0
  motion_key = (calibration_key, sm.logMonoTime["deviceMotion"], motion.timestamp, xyz)
  if cache is not None and cache.get("motion_key") == motion_key:
    return cache["result"]
  # Only the calibrated angular z component is needed, not a complete Pose
  # or its velocity/acceleration/orientation/covariance transformations.
  yaw_rate = float(calibrator.calib_from_device[2] @ xyz)
  result = (yaw_rate, min(motion.timestamp, sm.logMonoTime["deviceMotion"])) if math.isfinite(yaw_rate) else (None, 0)
  if cache is not None:
    cache.update(motion_key=motion_key, result=result)
  return result
