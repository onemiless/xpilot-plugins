"""Calibrated model views through the system's Venus V4LEncoder, without PyAV."""
import ctypes
from pathlib import Path

import numpy as np


def library():
  lib = ctypes.CDLL(str(Path(__file__).with_name('libcamera.so')))
  lib.lean_open.restype = ctypes.c_void_p
  lib.lean_open.argtypes = [ctypes.c_int]
  lib.lean_set_bitrate.argtypes = [ctypes.c_void_p, ctypes.c_int]
  lib.lean_close.argtypes = [ctypes.c_void_p]
  lib.lean_capture.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                              ctypes.c_void_p]
  lib.lean_error.restype = ctypes.c_char_p
  return lib


class Camera:
  def __init__(self, bitrate):
    self.lib = library()
    self.handle = self.lib.lean_open(bitrate)
    if not self.handle:
      raise RuntimeError(self.lib.lean_error().decode())
    self.buffers = [ctypes.create_string_buffer(1024 * 1024) for _ in range(2)]
    self.sizes = (ctypes.c_uint32 * 3)()
    self.times = (ctypes.c_uint64 * 7)()

  def set_bitrate(self, bitrate):
    self.lib.lean_set_bitrate(self.handle, bitrate)

  def close(self):
    if self.handle:
      self.lib.lean_close(self.handle)
      self.handle = None

  def capture(self, meta, reset):
    from .worker import META
    matrices = np.array(meta[7:25], dtype=np.float32).reshape(2, 3, 3)
    if not np.isfinite(matrices).all() or (np.abs(np.linalg.det(matrices)) < 1e-9).any():
      raise ValueError('camera calibration not ready')
    packed = ctypes.create_string_buffer(META.pack(*meta), META.size)
    result = self.lib.lean_capture(self.handle, packed, reset, *self.buffers, self.sizes, self.times)
    if result < 0:
      raise RuntimeError(self.lib.lean_error().decode())
    if not result:
      return None
    road, wide = (ctypes.string_at(b, n) for b, n in zip(self.buffers, self.sizes[:2]))
    # Keyframe bits come from Venus (V4L2 keyframe flag), not from scanning the Annex-B NALs.
    return self.sizes[2], road, wide, META.unpack(packed.raw), tuple(self.times)
