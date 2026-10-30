"""Radar health is a frame contract, never a sensor-model flag.

The caller must obtain source/generation/completeness from the actual frame
producer. A selected manifest or an empty points list is not that evidence.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class RadarSource:
  provider: str
  generation: str
  sample_mono_ns: int
  complete: bool
  adjacent_targets: bool


def radar_healthy(radar, source, *, now_ns, radar_mono_ns, model_mono_ns, transport_valid=True):
  if (not transport_valid or not isinstance(source, RadarSource) or not source.provider
      or not source.generation or source.complete is not True or source.adjacent_targets is not True
      or source.sample_mono_ns != radar_mono_ns):
    return False
  if any(type(stamp) is not int or not 0 < stamp <= now_ns or now_ns-stamp > 200_000_000
         for stamp in (radar_mono_ns, model_mono_ns)):
    return False
  if abs(radar_mono_ns-model_mono_ns) > 100_000_000:
    return False
  errors=radar.errors
  return not any((errors.canError, errors.radarFault, errors.wrongConfig, errors.radarUnavailableTemporary))


def efficiency_radar_healthy(sm, now_ns, source):
  healthy = all(sm.seen[s] and sm.alive[s] and sm.valid[s] for s in ('radarTracks', 'modelV2'))
  return radar_healthy(sm['radarTracks'], source, now_ns=now_ns,
                       radar_mono_ns=sm.logMonoTime['radarTracks'],
                       model_mono_ns=sm.logMonoTime['modelV2'], transport_valid=healthy)
