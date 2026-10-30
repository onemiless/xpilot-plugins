"""Failures first: wrong coordinate system/order, degenerate and nonfinite route,
off-route fix advancing progress, backwards progress and endpoint extrapolation.
Synthetic WGS84 coordinates only; no native map renderer or GPS device.
"""
import numpy as np
import pytest

from ..route_geometry import RouteTracker


LINE = [(120., 32.), (120., 32.001), (120., 32.002)]


def test_northbound_progress_pose_and_no_reverse():
  tracker = RouteTracker(LINE, coordinate_system='wgs84')
  lon, lat, bearing = tracker.snap(120., 32.0005)
  assert lon == 120. and lat == pytest.approx(32.0005) and bearing == 0.
  first = tracker.progress
  tracker.snap(120., 32.0004)
  assert tracker.progress == first
  assert tracker.pose(1e6)[:2] == LINE[-1]


def test_eastbound_and_duplicate_points():
  tracker = RouteTracker([(120., 32.), (120., 32.), (120.002, 32.)], coordinate_system='wgs84')
  assert tracker.snap(120.001, 32.)[2] == pytest.approx(90.)


def test_far_fix_does_not_advance():
  tracker = RouteTracker(LINE, coordinate_system='wgs84')
  tracker.snap(120., 32.0005)
  before = tracker.progress
  with pytest.raises(ValueError):
    tracker.snap(121., 33.)
  assert tracker.progress == before


@pytest.mark.parametrize('route,system', [([], 'wgs84'), ([(120., 32.)]*2, 'wgs84'),
                                        (LINE, 'gcj02'), ([(120., np.nan), (120., 32.)], 'wgs84'),
                                        ([(32., 120.), (32., 121.)], 'wgs84'),
                                        ([(179.99, 0.), (-179.99, 0.)], 'wgs84')])
def test_invalid_routes(route, system):
  with pytest.raises(ValueError):
    RouteTracker(route, coordinate_system=system)


@pytest.mark.parametrize('fix', [(np.nan, 32.), (120., np.inf), (32., 120.)])
def test_invalid_fix(fix):
  tracker = RouteTracker(LINE, coordinate_system='wgs84')
  with pytest.raises(ValueError):
    tracker.snap(*fix)
  assert tracker.progress == -np.inf


def test_new_route_starts_without_old_progress():
  old = RouteTracker(LINE, coordinate_system='wgs84')
  old.snap(120., 32.0018)
  new = RouteTracker(LINE, coordinate_system='wgs84')
  new.snap(120., 32.0001)
  assert new.progress < old.progress
