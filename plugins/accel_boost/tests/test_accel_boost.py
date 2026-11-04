"""sunnypilot test_accel_boost.py (#2089) on the port; the param-switch case is the host's enable switch here."""
from types import SimpleNamespace

import pytest

from ..boost import ACCEL_BOOST_MAX, AccelBoost


def make_sm(brake_prob=0.05, gas_prob=0.3, lat_accel=0.0, v_ego=30.0, personality=0, enabled=True, should_stop=False):
  meta = SimpleNamespace(disengagePredictions=SimpleNamespace(brakePressProbs=[0.0, brake_prob, 0.0, 0.0, 0.0, 0.0],
                                                              gasPressProbs=[0.0, gas_prob, 0.0, 0.0, 0.0, 0.0]))
  action = SimpleNamespace(shouldStop=should_stop, desiredCurvature=lat_accel / v_ego ** 2)
  return {'modelV2': SimpleNamespace(meta=meta, action=action), 'carState': SimpleNamespace(vEgo=v_ego),
          'selfdriveState': SimpleNamespace(enabled=enabled, personality=personality)}


def settle(sm, frames=100):
  boost = AccelBoost()
  for _ in range(frames):
    boost.update(sm, 0.0, 9.0, 9.0)
  return boost.total_boost


@pytest.mark.parametrize("personality", [0, 1, 2])
def test_personality_max(personality):
  assert abs(settle(make_sm(personality=personality)) - ACCEL_BOOST_MAX[personality]) < 1e-6


def test_brake_gate_closes():
  assert settle(make_sm(brake_prob=0.5)) == 0.0


def test_gas_reopens():
  assert abs(settle(make_sm(brake_prob=0.5, gas_prob=0.9)) - ACCEL_BOOST_MAX[0]) < 1e-6


def test_should_stop():
  assert settle(make_sm(gas_prob=0.9, should_stop=True)) == 0.0


@pytest.mark.parametrize("lat_accel,scale", [(1.5, 1.0), (2.0, 0.5), (2.5, 0.0), (-2.0, 0.5)])
def test_curve_fade(lat_accel, scale):
  assert abs(settle(make_sm(lat_accel=lat_accel)) - scale * ACCEL_BOOST_MAX[0]) < 1e-6


def test_low_speed():
  assert settle(make_sm(v_ego=0.5)) == 0.0


def test_disabled_resets():
  boost = AccelBoost()
  for _ in range(100):
    boost.update(make_sm(), 0.0, 9.0, 9.0)
  assert boost.update(make_sm(enabled=False), 0.0, 9.0, 9.0) == 0.0
  assert boost.boost == 0.0 and boost.total_boost == 0.0


def test_build_and_release_rates():
  boost = AccelBoost()
  boost.update(make_sm(), 0.0, 9.0, 9.0)
  assert abs(boost.total_boost - 0.01) < 1e-6
  for _ in range(100):
    boost.update(make_sm(), 0.0, 9.0, 9.0)
  boost.update(make_sm(brake_prob=0.5), 0.0, 9.0, 9.0)
  assert abs(boost.total_boost - (ACCEL_BOOST_MAX[0] - 0.05)) < 1e-6


def test_only_with_openpilot_longitudinal():
  from ..plugin import AccelBoostPlugin
  assert AccelBoostPlugin.accel_boost(SimpleNamespace(openpilotLongitudinalControl=False), None, None) is None
  assert isinstance(AccelBoostPlugin.accel_boost(SimpleNamespace(openpilotLongitudinalControl=True), None, None), AccelBoost)
