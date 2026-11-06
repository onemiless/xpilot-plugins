"""Before implementation: model name must not grant coverage; reject missing,
stale/future, mismatched, incomplete and faulted frames. Empty is not permission.
"""
from dataclasses import replace
from types import SimpleNamespace as NS

from ..radar_source import RadarSource, radar_healthy


def frame():
  return NS(errors=NS(canError=False, radarFault=False, wrongConfig=False, radarUnavailableTemporary=False), points=[])


def source(name='fixture_radar'):
  return RadarSource(name, 'generation-a', 1_000_000_000, True, True)


def test_compatible_model_names_share_identical_contract():
  for name in ('ars408', 'fixture_other_model'):
    assert radar_healthy(frame(), source(name), now_ns=1_050_000_000,
                         radar_mono_ns=1_000_000_000, model_mono_ns=1_000_000_000)


def test_missing_coverage_or_incomplete_frame_never_means_clear():
  for profile in (None, replace(source(), adjacent_targets=False), replace(source(), complete=False),
                  replace(source(), generation=''), replace(source(), sample_mono_ns=900_000_000)):
    assert not radar_healthy(frame(), profile, now_ns=1_050_000_000,
                             radar_mono_ns=1_000_000_000, model_mono_ns=1_000_000_000)


def test_freshness_alignment_errors_and_transport_validity():
  for change in ({'now_ns': 1_300_000_001}, {'radar_mono_ns': 1_100_000_000},
                 {'model_mono_ns': 800_000_000}, {'transport_valid': False}):
    args=dict(now_ns=1_050_000_000, radar_mono_ns=1_000_000_000, model_mono_ns=1_000_000_000)
    args.update(change)
    assert not radar_healthy(frame(), source(), **args)
  bad=frame()
  bad.errors.radarFault=True
  assert not radar_healthy(bad, source(), now_ns=1_050_000_000,
                           radar_mono_ns=1_000_000_000, model_mono_ns=1_000_000_000)
