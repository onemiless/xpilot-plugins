"""Failures first: incomplete steps, stale/future observations, unordered or
nonfinite distances, unknown command vocabulary, arrival-only zero inputs.
Expected command indices follow comma a03bb1d4 navigation/helpers.py.
"""
from types import SimpleNamespace as Step

import pytest

from ..model_navigation import navigation_input


def encode(steps, traveled=0., **changes):
  args = dict(complete=True, source_ns=1_000_000_000, now_ns=1_100_000_000)
  args.update(changes)
  return navigation_input(steps, traveled, **args)


@pytest.mark.parametrize('kind,modifier,command', [
  ('turn', 'right', 10), ('turn', 'left', 15), ('turn', 'uturn', 13),
  ('continue', 'straight', 8), ('on ramp', 'right', 18), ('on ramp', 'left', 19),
  ('off ramp', 'right', 20), ('off ramp', 'left', 21), ('fork', 'left', 24),
  ('roundabout', 'right', 26), ('exit rotary', 'left', 27), ('notification', '', 28),
])
def test_official_indices(kind, modifier, command):
  result = encode([Step(type=kind, modifier=modifier, distance=150.)], traveled=25.)
  assert result.valid and len(result.vector) == 47
  assert result.vector[command] == 1. and sum(result.vector[:46]) == 1.
  assert result.vector[-1] == 1.25


def test_skips_passed_departure_merge_arrival():
  result = encode([Step(type='depart', modifier='', distance=0.),
                   Step(type='turn', modifier='right', distance=10.),
                   Step(type='merge', modifier='left', distance=40.),
                   Step(type='turn', modifier='left', distance=100.),
                   Step(type='arrive', modifier='', distance=200.)], traveled=25.)
  assert result.valid and result.vector[15] == 1. and result.vector[-1] == .75
  zero = encode([Step(type='arrive', modifier='', distance=200.)])
  assert zero.valid and not any(zero.vector)


@pytest.mark.parametrize('changes', [dict(complete=False), dict(source_ns=0),
                                    dict(now_ns=3_000_000_001), dict(now_ns=999_999_999)])
def test_missing_or_stale_input_is_invalid_not_a_valid_zero(changes):
  result = encode([Step(type='turn', modifier='left', distance=100.)], **changes)
  assert not result.valid and not any(result.vector) and result.reason


@pytest.mark.parametrize('steps,traveled', [
  ([Step(type='invented', modifier='straight', distance=20.)], 0.),
  ([Step(type='turn', modifier='unknown', distance=20.)], 0.),
  ([Step(type='turn', modifier='left', distance=float('nan'))], 0.),
  ([Step(type='turn', modifier='left', distance=True)], 0.),
  ([Step(type='turn', modifier='left', distance=20.), Step(type='turn', modifier='left', distance=10.)], 0.),
  ([Step(type='turn', modifier='left', distance=20.)], float('inf')),
  ([Step(type='turn', modifier='left', distance=20.)], -1.),
  ([{}], 0.),
])
def test_invalid_data(steps, traveled):
  result = encode(steps, traveled)
  assert not result.valid and not any(result.vector)


def test_known_empty_complete_route_is_valid_zero():
  assert encode([]).valid
  assert not encode([], complete=False).valid
