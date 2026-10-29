"""Migration failures: wrong calibrated axis, stale/future input, bad health,
unavailable calibration, nonfinite samples, or cached data masking invalidation.
No sockets, Params, vehicle sends or live subscriptions are used.
"""
from types import SimpleNamespace as NS

import pytest

from ..motion import radar_yaw_rate_context


class Row:
  def __matmul__(self, xyz):
    return xyz[1]  # fixture rotates device y into calibrated z


class Calibrator:
  calib_valid = True
  calib_from_device = [None, None, Row()]
  feeds = 0

  def feed_extrinsics_calibration(self, calibration):
    self.feeds += 1


class Snapshot(dict):
  def __init__(self):
    super().__init__(deviceMotion=NS(inputsOK=True, sensorsOK=True, posenetOK=True,
                                    timestamp=3_000_000_000,
                                    angularVelocityDevice=NS(x=0.1, y=0.2, z=0.3, valid=True)),
                     extrinsicsCalibration=NS(rpyCalib=(0., 0., 0.), calStatus='calibrated'))
    self.seen = dict.fromkeys(self, True)
    self.valid = dict.fromkeys(self, True)
    self.logMonoTime = dict.fromkeys(self, 3_000_000_000)


def test_calibrated_axis_and_sample_timestamp():
  sm = Snapshot()
  sm['deviceMotion'].timestamp -= 10
  assert radar_yaw_rate_context(sm, 3_100_000_000, Calibrator()) == (0.2, 2_999_999_990)


@pytest.mark.parametrize('service,age', [
  ('deviceMotion', 150_000_001), ('extrinsicsCalibration', 2_000_000_001),
  ('deviceMotion', -1), ('extrinsicsCalibration', -1),
])
def test_stale_and_future_frames(service, age):
  sm = Snapshot()
  sm.logMonoTime[service] = 3_000_000_000 - age
  assert radar_yaw_rate_context(sm, 3_000_000_000, Calibrator()) == (None, 0)


@pytest.mark.parametrize('field', ['inputsOK', 'sensorsOK', 'posenetOK'])
def test_health_loss_invalidates_cached_motion(field):
  sm, calibrator, cache = Snapshot(), Calibrator(), {}
  assert radar_yaw_rate_context(sm, 3_000_000_000, calibrator, cache)[0] == 0.2
  setattr(sm['deviceMotion'], field, False)
  assert radar_yaw_rate_context(sm, 3_000_000_001, calibrator, cache) == (None, 0)


@pytest.mark.parametrize('invalid', ['unseen', 'invalid', 'angular', 'nan', 'rpy', 'calibration', 'sample_age'])
def test_missing_or_invalid_calibration_and_motion(invalid):
  sm, calibrator = Snapshot(), Calibrator()
  if invalid == 'unseen': sm.seen['deviceMotion'] = False
  elif invalid == 'invalid': sm.valid['extrinsicsCalibration'] = False
  elif invalid == 'angular': sm['deviceMotion'].angularVelocityDevice.valid = False
  elif invalid == 'nan': sm['deviceMotion'].angularVelocityDevice.y = float('nan')
  elif invalid == 'rpy': sm['extrinsicsCalibration'].rpyCalib = (0., float('inf'), 0.)
  elif invalid == 'calibration': calibrator.calib_valid = False
  elif invalid == 'sample_age': sm['deviceMotion'].timestamp -= 150_000_001
  assert radar_yaw_rate_context(sm, 3_000_000_000, calibrator) == (None, 0)


def test_cache_reuses_calibration_but_does_not_extend_sample_life():
  sm, calibrator, cache = Snapshot(), Calibrator(), {}
  assert radar_yaw_rate_context(sm, 3_000_000_000, calibrator, cache)[0] == 0.2
  assert radar_yaw_rate_context(sm, 3_150_000_000, calibrator, cache)[0] == 0.2
  assert calibrator.feeds == 1
  assert radar_yaw_rate_context(sm, 3_150_000_001, calibrator, cache) == (None, 0)
