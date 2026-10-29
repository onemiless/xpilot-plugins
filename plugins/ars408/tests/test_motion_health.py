"""Failure cases: valid-looking targets after calibration/motion loss, erased
sensor faults, and silence instead of an unavailable report when no cycle arrives.
"""
from opendbc.car import structs
from openpilot.tesla.card_context import CardContext
from ..motion_health import MotionAwareRadar
from .test_plugin_context import fixture, NOW


class Radar:
  empty = False
  fault = False

  def update(self, packets):
    if self.empty:
      return None
    ret = structs.RadarData()
    ret.errors.radarFault = self.fault
    return ret


def test_missing_lost_and_recovered_motion_marks_receiver_unavailable():
  _, sm, _ = fixture()
  clock = [NOW]
  source = Radar()
  radar = MotionAwareRadar(source, clock=lambda: clock[0])
  assert radar.update([]).errors.radarUnavailableTemporary
  radar.radar_context(CardContext(sm, tuple(sm)), NOW)
  assert not radar.update([]).errors.radarUnavailableTemporary
  source.fault = True
  assert radar.update([]).errors.radarFault
  clock[0] += 150_000_001
  assert radar.update([]).errors.radarUnavailableTemporary
  clock[0] = NOW
  sm.valid['deviceMotion'] = False
  radar.radar_context(CardContext(sm, tuple(sm)), NOW)
  assert radar.update([]).errors.radarUnavailableTemporary
  sm.valid['deviceMotion'] = True
  radar.radar_context(CardContext(sm, tuple(sm)), NOW)
  assert not radar.update([]).errors.radarUnavailableTemporary


def test_no_cycle_reports_unavailable_at_bounded_rate():
  source = Radar()
  source.empty = True
  clock = [NOW]
  radar = MotionAwareRadar(source, clock=lambda: clock[0])
  assert radar.update([]).errors.radarUnavailableTemporary
  clock[0] += 1
  assert radar.update([]) is None
  clock[0] += 50_000_000
  assert radar.update([]).errors.radarUnavailableTemporary
