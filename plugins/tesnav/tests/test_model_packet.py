"""Offline producer-to-host failures: route dropout/change, GCJ02, incomplete
steps, mismatched rendered identity and a stale fix republished as a new frame.
No map downloads, live Params, VisionIPC, camera/model execution or CAN.
"""
import numpy as np
import pytest
from types import SimpleNamespace as Step

from ..model_inputs import MapFrame, NavigationIdentity, ModelInputAdapter
from ..model_packet import build_model_packet
from ..route import RouteStore
from ..state import NavigationStore
from .test_navigation import NOW, payload, accept
from .test_route import route


def fixture(system='wgs84'):
  nav, routes = NavigationStore(), RouteStore()
  accept(nav, payload(coordinateSystem=system))
  routes.accept(route(coordinateSystem=system), '192.168.1.2', nav, NOW, 10.)
  key = NavigationIdentity('tesnav', 'fixture-a', 1)
  frame = MapFrame(key, 10_000_000_000, np.full((256, 256), 128, dtype=np.uint8), coordinate_system=system)
  return nav, routes, key, frame


def packet(nav, routes, key, frame, **changes):
  args = dict(steps_identity=key, steps_source_ns=10_000_000_000,
              complete=True, traveled=0., wall_ms=NOW, now_ns=10_100_000_000)
  args.update(changes)
  return build_model_packet(nav, routes, frame, [Step(type='turn', modifier='left', distance=80.)], **args)


def test_producer_to_host_and_dropout_clears_arrays():
  nav, routes, key, frame = fixture()
  pair = packet(nav, routes, key, frame)
  adapter = ModelInputAdapter({'nav_map': ((1, 1, 256, 256), np.float32),
                              'navigation': ((1, 47), np.float32)}, generation='offline-model')
  result = adapter.prepare(*pair, expected_identity=key, now_ns=10_100_000_000, authorized=True)
  assert result.valid and result.inputs['navigation'][0, 15] == 1.
  assert result.inputs['navigation'][0, 46] == pytest.approx(.8)
  assert result.inputs['nav_map'].min() == 128
  lost = packet(nav, routes, key, frame, now_ns=12_000_000_000, wall_ms=NOW+2000)
  cleared = adapter.prepare(*lost, expected_identity=key, now_ns=12_000_000_000, authorized=True)
  assert not cleared.valid and cleared.reset_required
  assert all(not a.any() for a in cleared.inputs.values())


def test_identity_route_switch_and_incomplete_steps():
  nav, routes, key, frame = fixture()
  assert packet(nav, routes, key, frame, complete=False) == (None, None)
  other = NavigationIdentity('tesnav', 'fixture-a', 2)
  assert packet(nav, routes, other, frame) == (None, None)
  accept(nav, payload(coordinateSystem='wgs84', sequence=2, routeRevision=2))
  assert packet(nav, routes, key, frame) == (None, None)


def test_gcj02_not_relabelled_for_model():
  assert packet(*fixture('gcj02')) == (None, None)


def test_bad_render_does_not_acquire_validity():
  nav, routes, key, frame = fixture()
  invalid = MapFrame(key, frame.source_ns, frame.pixels, valid=False)
  assert packet(nav, routes, key, invalid) == (None, None)
  stale = MapFrame(key, 9_000_000_000, frame.pixels)
  assert packet(nav, routes, key, stale) == (None, None)
