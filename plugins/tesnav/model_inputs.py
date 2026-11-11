"""Offline-only navigation input contract and model ABI adapter.

No IPC, Params, model execution, plugin loading or control authority. A live host
must supply its resolved owner/route identity, manage epochs and honor reset_required
before any inference. This module is not advertised as a live plugin capability.
"""
from dataclasses import dataclass
import hashlib

import numpy as np


@dataclass(frozen=True)
class NavigationIdentity:
  provider: str
  session: str
  revision: int

  def __post_init__(self):
    if (any(not isinstance(s, str) or not 1 <= len(s) <= 128 for s in (self.provider, self.session))
        or type(self.revision) is not int or not 0 <= self.revision < 2**63):
      raise ValueError('Invalid navigation identity')


@dataclass(frozen=True)
class MapFrame:
  identity: NavigationIdentity
  source_ns: int  # original location/fix time, not publication/receive time
  pixels: np.ndarray  # owned 256x256 uint8, official 0..255 grayscale convention
  valid: bool = True
  coordinate_system: str = 'wgs84'


@dataclass(frozen=True)
class NavigationFrame:
  identity: NavigationIdentity
  source_ns: int  # original complete instruction observation time
  vector: np.ndarray  # canonical (47,) float32; no model-specific batch dimension
  valid: bool = True


@dataclass(frozen=True)
class PreparedInputs:
  valid: bool
  inputs: dict
  generation: str
  reset_required: bool
  reason: str


class ModelInputAdapter:
  """One model generation. prepare() creates fresh arrays, including on failure.

Supported verified pairs: uint8 map + (47,) navigation; float32 map + (1,47)
navigation. Other model inputs are untouched. These shapes do not prove a
model's provenance, full ABI or driving suitability.
"""
  def __init__(self, input_specs, *, generation):
    if not isinstance(generation, str) or not generation:
      raise ValueError('Model generation is required')
    try:
      map_shape, map_dtype = input_specs['nav_map']
      nav_shape, nav_dtype = input_specs['navigation']
      pair = np.dtype(map_dtype), tuple(nav_shape)
      if (tuple(map_shape) != (1, 1, 256, 256) or np.dtype(nav_dtype) != np.dtype('float32')
          or pair not in ((np.dtype('uint8'), (47,)), (np.dtype('float32'), (1, 47)))):
        raise ValueError('Unsupported navigation model ABI')
    except (KeyError, TypeError) as error:
      raise ValueError('Missing or invalid navigation model ABI') from error
    self.generation = generation
    self.specs = {'nav_map': (tuple(map_shape), np.dtype(map_dtype)),
                  'navigation': (tuple(nav_shape), np.dtype(nav_dtype))}
    self.identity, self.last, self.previous = None, {}, None

  def _result(self, arrays, valid, reason):
    state = self.identity, valid
    reset = state != self.previous
    self.previous = state
    return PreparedInputs(valid, arrays, self.generation, reset, reason)

  def prepare(self, map_frame, navigation_frame, *, expected_identity, now_ns, authorized):
    arrays = {name: np.zeros(shape, dtype=dtype) for name, (shape, dtype) in self.specs.items()}
    if expected_identity != self.identity:
      self.identity, self.last = expected_identity, {}
    if authorized is not True or not isinstance(expected_identity, NavigationIdentity):
      return self._result(arrays, False, 'not_authorized')
    if type(now_ns) is not int or now_ns <= 0:
      return self._result(arrays, False, 'invalid_clock')
    if not isinstance(map_frame, MapFrame) or not isinstance(navigation_frame, NavigationFrame):
      return self._result(arrays, False, 'missing_input')
    frames = (('nav_map', map_frame, 500_000_000), ('navigation', navigation_frame, 2_000_000_000))
    for _, frame, ttl in frames:
      if frame.valid is not True or frame.identity != expected_identity:
        return self._result(arrays, False, 'invalid_identity_or_frame')
      if type(frame.source_ns) is not int or frame.source_ns <= 0 or not 0 <= now_ns-frame.source_ns <= ttl:
        return self._result(arrays, False, 'expired_input')
    if map_frame.coordinate_system != 'wgs84':
      return self._result(arrays, False, 'coordinate_system')
    if (not isinstance(map_frame.pixels, np.ndarray) or map_frame.pixels.shape != (256, 256)
        or map_frame.pixels.dtype != np.uint8 or not isinstance(navigation_frame.vector, np.ndarray)
        or navigation_frame.vector.shape != (47,) or navigation_frame.vector.dtype != np.float32):
      return self._result(arrays, False, 'invalid_shape_or_dtype')
    pixels, vector = map_frame.pixels.copy(), navigation_frame.vector.copy()
    if (not np.isfinite(vector).all() or not np.isin(vector[:46], (0., 1.)).all()
        or vector[:46].sum() > 1 or not 0 <= vector[46] <= 100000
        or (vector[:46].sum() == 0 and vector[46] != 0)):
      return self._result(arrays, False, 'invalid_vector')
    accepted = {}
    for (name, frame, _), data in zip(frames, (pixels, vector), strict=True):
      digest = hashlib.sha256(data.tobytes()).digest()
      previous = self.last.get(name)
      if previous and (frame.source_ns < previous[0] or (frame.source_ns == previous[0] and digest != previous[1])):
        return self._result(arrays, False, 'reordered_or_mutated_input')
      accepted[name] = frame.source_ns, digest
    # Commit only after both inputs pass. No normalization: the RL graph divides
    # its 0..255 float map internally. No source-buffer views reach the consumer.
    arrays['nav_map'][:] = pixels
    arrays['navigation'][:] = vector
    self.last.update(accepted)
    return self._result(arrays, True, '')
