"""Failures first: malformed/oversize/replayed/foreign/stale input, source freeze,
simulation, route cancellation, missing distance, expired display, pedal override,
no longitudinal authority, and attempts to accelerate. Uses synthetic positions.
"""
import json
from types import SimpleNamespace

import pytest

from ..protocol import parse_snapshot
from ..state import NavigationStore, instruction
from ..speed import SpeedPolicy


NOW = 1_800_000_000_000


def payload(**changes):
  value = {
    'schemaVersion': 3, 'messageType': 'navigation_snapshot', 'sessionId': 'fixture-a',
    'sequence': 1, 'routeRevision': 1, 'maneuverEventId': 12, 'sourcePlatform': 'android',
    'sourceWallTimeMs': NOW, 'validForMs': 1200, 'navigationMode': 'realtime',
    'routeActive': True, 'routeMatched': True, 'gpsWeak': False, 'coordinateSystem': 'gcj02',
    'location': {'latitude': 32., 'longitude': 120., 'accuracyM': 5., 'bearingDeg': 30.,
                 'speedKph': 40., 'observedAtMs': NOW},
    'guidance': {'maneuver': 'turn_left', 'maneuverDistanceM': 80, 'observedAtMs': NOW,
                 'currentRoad': 'Fixture Road', 'nextRoad': 'Next Road'},
    'lanes': {'observedAtMs': NOW, 'items': [
      {'index': 0, 'allowedActions': ['LEFT'], 'recommendedActions': ['LEFT'], 'recommended': True}]},
  }
  value.update(changes)
  return value


def accept(store, value=None, mono=10.):
  return store.accept(json.dumps(value or payload()).encode(), '192.168.1.2', NOW, mono)


def test_canonical_app_snapshot_and_ack():
  store = NavigationStore()
  ack = accept(store)
  assert ack == {'messageType': 'navassist_udp_ack', 'schemaVersion': 3,
                 'sessionId': 'fixture-a', 'sequence': 1}
  nav = instruction(store.current(NOW, 10.1), NOW)
  assert nav['maneuverDistance'] == 80 and nav['maneuverModifier'] == 'left'
  assert nav['lanes'][0]['active']


@pytest.mark.parametrize('change', [
  {'sequence': True}, {'coordinateSystem': 'bogus'}, {'routeActive': 1},
  {'sourceWallTimeMs': NOW + 2001}, {'sourceWallTimeMs': NOW - 3000},
  {'unexpected': 1}, {'validForMs': 0},
])
def test_reject_bad_snapshot(change):
  with pytest.raises(ValueError):
    accept(NavigationStore(), payload(**change))


def test_json_and_resource_bounds():
  for body in (b'{"schemaVersion":3,"schemaVersion":3}', b'[]', b' ' * 8193,
               b'{"value":NaN}'):
    with pytest.raises(ValueError):
      NavigationStore().accept(body, '192.168.1.2', NOW, 10.)


def test_replay_owner_revision_and_session_tombstones():
  store = NavigationStore()
  accept(store)
  with pytest.raises(ValueError):
    accept(store)
  with pytest.raises(ValueError):
    store.accept(json.dumps(payload(sequence=2)).encode(), '192.168.1.3', NOW, 10.2)
  with pytest.raises(ValueError):
    accept(store, payload(sequence=2, routeRevision=0))
  accept(store, payload(sessionId='fixture-b'), mono=12.)
  with pytest.raises(ValueError):
    accept(store, payload(sequence=3), mono=14.)


def test_checkpoint_survives_worker_restart(tmp_path):
  path = tmp_path / 'checkpoint.json'
  accept(NavigationStore(path))
  with pytest.raises(ValueError):
    accept(NavigationStore(path))


def test_expiry_freeze_and_inactive_never_become_instruction():
  store = NavigationStore()
  accept(store)
  assert store.current(NOW, 11.21) is None
  assert store.current(NOW - 1, 10.1) is None
  for change in ({'navigationMode': 'simulation'}, {'routeActive': False}, {'gpsWeak': True},
                 {'routeMatched': False}, {'coordinateSystem': 'unknown'}):
    candidate = NavigationStore()
    accept(candidate, payload(**change))
    assert instruction(candidate.current(NOW, 10.1), NOW) is None
  candidate = payload()
  candidate['location']['observedAtMs'] = NOW - 5000
  accept(NavigationStore(), candidate)  # Valid wire, unusable observation.
  assert instruction(parse_snapshot(json.dumps(candidate).encode()), NOW) is None


def test_missing_distance_not_interpreted_as_zero():
  value = payload()
  del value['guidance']['maneuverDistanceM']
  assert instruction(parse_snapshot(json.dumps(value).encode()), NOW) is None


def nav(distance=80):
  return SimpleNamespace(maneuverType='turn', maneuverModifier='left', maneuverDistance=distance)


def test_speed_only_tightens_and_latches_on_override_or_loss():
  policy = SpeedPolicy()
  target = policy.update(nav(), 15., 20., active=True)
  assert 5. <= target <= 15. and target < 20.
  assert policy.update(nav(), 15., 4., active=True) is None
  assert policy.update(nav(), 15., 20., active=True, override=True) is None
  assert policy.update(nav(), 15., 20., active=True) is None
  policy = SpeedPolicy()
  assert policy.update(nav(), 15., 20., active=False) is None
  assert policy.update(nav(), 15., 20., active=True) is not None
  assert policy.update(None, 15., 20., active=True) is None
  assert policy.update(nav(), 15., 20., active=True) is None


def test_speed_nonfinite_far_zero_and_no_stop():
  for distance in (float('nan'), float('inf'), -1, 2001):
    assert SpeedPolicy().update(nav(distance), 15., 20., active=True) is None
  assert SpeedPolicy().update(nav(0), 15., 20., active=True) == 5.
