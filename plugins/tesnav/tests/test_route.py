"""Failures first: absent/foreign/stale snapshot, wrong route/coordinate identity,
future sequence, changed same-revision geometry, invalid JSON and coordinates,
expiry/cancellation clearing, non-WGS84 publication and delayed old uploads.
"""
import json

import pytest

from .test_navigation import NOW, payload, accept
from ..state import NavigationStore
from ..route import RouteStore, parse_route


def route(**changes):
  value = dict(schemaVersion=3, messageType='navigation_route', sessionId='fixture-a',
               sequence=1, routeRevision=1, sourceWallTimeMs=NOW, coordinateSystem='wgs84',
               points=[{'latitude': 32., 'longitude': 120.}, {'latitude': 32.001, 'longitude': 120.001}])
  value.update(changes)
  return json.dumps(value).encode()


def fixture():
  nav = NavigationStore()
  accept(nav, payload(coordinateSystem='wgs84'))
  return nav, RouteStore()


def test_route_identity_and_expiry():
  nav, routes = fixture()
  routes.accept(route(), '192.168.1.2', nav, NOW, 10.)
  assert len(routes.coordinates(nav, NOW, 10.1)) == 2
  assert routes.coordinates(nav, NOW, 12.) is None
  assert routes.route is None
  accept(nav, payload(sequence=2, coordinateSystem='wgs84'), mono=12.)
  assert routes.coordinates(nav, NOW, 12.1) is None
  with pytest.raises(ValueError):
    routes.accept(route(), '192.168.1.2', nav, NOW, 12.1)


@pytest.mark.parametrize('change', [dict(sessionId='wrong'), dict(routeRevision=2), dict(sequence=2),
                                  dict(coordinateSystem='gcj02'), dict(sourceWallTimeMs=NOW-3000),
                                  dict(sourceWallTimeMs=NOW+2000)])
def test_reject_mismatch(change):
  nav, routes = fixture()
  with pytest.raises(ValueError):
    routes.accept(route(**change), '192.168.1.2', nav, NOW, 10.)
  assert routes.route is None


def test_foreign_and_mutation_and_cancel():
  nav, routes = fixture()
  with pytest.raises(ValueError):
    routes.accept(route(), '192.168.1.3', nav, NOW, 10.)
  routes.accept(route(), '192.168.1.2', nav, NOW, 10.)
  routes.accept(route(), '192.168.1.2', nav, NOW, 10.)  # retry of identical data
  accept(nav, payload(sequence=2, coordinateSystem='wgs84'))
  with pytest.raises(ValueError):
    routes.accept(route(sequence=2, points=[{'latitude': 31., 'longitude': 120.},
                                          {'latitude': 32., 'longitude': 120.}]),
                  '192.168.1.2', nav, NOW, 10.)
  accept(nav, payload(sequence=3, routeActive=False, coordinateSystem='wgs84'))
  assert routes.coordinates(nav, NOW, 10.) is None


def test_gcj02_is_not_silently_published_as_wgs84():
  nav, routes = fixture()
  nav = NavigationStore()
  accept(nav)
  routes.accept(route(coordinateSystem='gcj02'), '192.168.1.2', nav, NOW, 10.)
  assert routes.route.coordinate_system == 'gcj02'
  assert routes.coordinates(nav, NOW, 10.) is None


@pytest.mark.parametrize('body', [b'{}', b'{"schemaVersion":3,"schemaVersion":3}',
                                 route(points=[]), route(sequence=True),
                                 route(points=[{'latitude': float('nan'), 'longitude': 120.}]*2),
                                 route(points=[{'latitude': 32., 'longitude': 120.}]*2)])
def test_bad_route(body):
  with pytest.raises(ValueError):
    parse_route(body)
