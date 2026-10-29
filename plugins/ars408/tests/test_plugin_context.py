"""API wiring failures: no declared subscriptions, cold/stale samples emitting
motion, invalidation retaining an old yaw, wrong mount sign, or excessive TX rate.
Uses real capnp readers, PoseCalibrator, host CardContext and radar DBC. No sockets.
"""
from types import SimpleNamespace

import pytest
from opendbc.car import structs
from openpilot.cereal import log
from openpilot.tesla.card_context import CardContext, METADATA

from ..plugin import ARS408
from .test_transmitter import decode

NOW = 3_000_000_000


def fixture(inverted=True):
  params = SimpleNamespace(get_bool=lambda key: inverted if key == 'ExtARS408Inverted' else False)
  plugin = ARS408(SimpleNamespace(brand='tesla'), params)
  motion = log.DeviceMotion.new_message()
  motion.inputsOK = motion.sensorsOK = motion.posenetOK = True
  motion.timestamp = NOW
  motion.angularVelocityDevice = {'x': 0.1, 'y': 0.2, 'z': 0.3, 'valid': True}
  calibration = log.ExtrinsicsCalibration.new_message()
  calibration.rpyCalib = [0., 0., 0.]
  calibration.calStatus = 'calibrated'
  class Snapshot(dict):
    pass
  sm = Snapshot(deviceMotion=motion.as_reader(), extrinsicsCalibration=calibration.as_reader())
  for key in METADATA:
    setattr(sm, key, dict.fromkeys(sm, NOW if key == 'logMonoTime' else True))
  cs = structs.CarState()
  cs.canValid = True
  cs.vEgoRaw = 10.
  cs.gearShifter = 'drive'
  return plugin, sm, cs


@pytest.mark.parametrize('inverted,sign', [(True, 1), (False, -1)])
def test_host_snapshot_reaches_calibrated_encoder_and_declared_sends(inverted, sign):
  plugin, sm, cs = fixture(inverted)
  assert set(plugin.SERVICES) == set(sm)
  assert plugin.motion_transmitter.update(0, cs, NOW) == []
  plugin.card_context(CardContext(sm, plugin.SERVICES), NOW)
  frames = plugin.motion_transmitter.update(0, cs, NOW)
  assert len(frames) == 2
  assert decode(frames[0], 'SpeedInformation')['RadarDevice_Speed'] == pytest.approx(10.)
  assert decode(frames[1], 'YawRateInformation')['RadarDevice_YawRate'] == pytest.approx(sign * 17.19, abs=.01)
  assert plugin.control_sends(cs, None, NOW) == frames
  assert plugin.motion_transmitter.update(5, cs, NOW + 150_000_001) == []


@pytest.mark.parametrize('failure', ['invalid', 'unseen', 'stale', 'uncalibrated'])
def test_api_invalidates_previous_sample(failure):
  plugin, sm, cs = fixture()
  plugin.card_context(CardContext(sm, plugin.SERVICES), NOW)
  assert plugin.motion_transmitter.yaw_rate_valid(NOW)
  if failure == 'invalid':
    sm.valid['deviceMotion'] = False
  elif failure == 'unseen':
    sm.seen['deviceMotion'] = False
  elif failure == 'stale':
    sm.logMonoTime['deviceMotion'] = NOW - 150_000_001
  else:
    calibration = log.ExtrinsicsCalibration.new_message()
    calibration.rpyCalib = [0., 0., 0.]
    calibration.calStatus = 'uncalibrated'
    sm['extrinsicsCalibration'] = calibration.as_reader()
  plugin.card_context(CardContext(sm, plugin.SERVICES), NOW + 1)
  assert plugin.motion_transmitter.update(5, cs, NOW + 1) == []
  assert plugin.control_sends(cs, None, NOW + 1) == []


def test_send_scheduler_and_expired_motion():
  plugin, sm, cs = fixture()
  plugin.card_context(CardContext(sm, plugin.SERVICES), NOW)
  for delta, count in [(0, 2), (1, 0), (49_999_999, 0), (50_000_000, 2),
                       (50_000_001, 0), (100_000_000, 2)]:
    assert len(plugin.control_sends(cs, None, NOW + delta)) == count
  assert plugin.control_sends(cs, None, NOW + 200_000_000) == []


def test_host_manifest_and_real_panda_accept_only_declared_frames():
  import json
  from pathlib import Path
  from openpilot.tesla import panda_rules
  from opendbc.safety.tests.xpilot_rules_helpers import configure, harness, packet
  from opendbc.safety.tests.test_xpilot_sensor_motion import motion_rule

  manifest = json.loads((Path(__file__).parents[1] / 'plugin.json').read_text())
  assert set(manifest['claims']) == {'source:radar', 'can.tx:0x300:1', 'can.tx:0x301:1'}
  assert manifest['requires']['rule_types'] == ['sensor_motion.v1']
  rules = tuple(panda_rules.encode_rule(r) for r in manifest['rules'])
  assert rules == (motion_rule(0x300), motion_rule(0x301))
  plugin, sm, cs = fixture()
  plugin.card_context(CardContext(sm, plugin.SERVICES), NOW)
  safety = harness()
  configure(safety, rules)
  safety.set_controls_allowed(False)
  safety.set_timer(NOW // 1000)
  frames = plugin.control_sends(cs, None, NOW)
  assert len(frames) == 2
  assert not safety.safety_tx_hook(packet(*frames[0]))  # no fresh radar status
  safety.safety_rx_hook(packet(0x60A, bytes(4)))
  assert all(safety.safety_tx_hook(packet(*frame)) for frame in frames)
  assert not any(safety.safety_tx_hook(packet(*frame)) for frame in frames)  # rate limit
