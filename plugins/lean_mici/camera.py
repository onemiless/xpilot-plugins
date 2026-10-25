"""Calibrated model views through the system's Venus V4LEncoder, without PyAV."""
import ctypes
from pathlib import Path

import numpy as np


def idr(data):
  return any(part and ((part[0] >> 1) & 63) in (19, 20) for part in data.replace(b'\0\0\0\1', b'\0\0\1').split(b'\0\0\1')[1:])


def library():
  lib = ctypes.CDLL(str(Path(__file__).with_name('libcamera.so')))
  lib.lean_open.restype = ctypes.c_void_p
  lib.lean_close.argtypes = [ctypes.c_void_p]
  lib.lean_capture.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
  lib.lean_error.restype = ctypes.c_char_p
  return lib


class Camera:
  def __init__(self):
    self.lib = library()
    self.handle = self.lib.lean_open()
    if not self.handle:
      raise RuntimeError(self.lib.lean_error().decode())
    self.buffers = [ctypes.create_string_buffer(1024 * 1024) for _ in range(2)]
    self.sizes = (ctypes.c_uint32 * 2)()

  def close(self):
    if self.handle:
      self.lib.lean_close(self.handle)
      self.handle = None

  def capture(self, meta, reset):
    from .worker import META
    matrices = np.array(meta[7:25], dtype=np.float32).reshape(2, 3, 3)
    if not np.isfinite(matrices).all() or (np.abs(np.linalg.det(matrices)) < 1e-9).any():
      raise ValueError('camera calibration not ready')
    result = self.lib.lean_capture(self.handle, META.pack(*meta), reset, *self.buffers, self.sizes)
    if result < 0:
      raise RuntimeError(self.lib.lean_error().decode())
    if not result:
      return None
    road, wide = (b.raw[:n] for b, n in zip(self.buffers, self.sizes))
    return int(idr(road)) | int(idr(wide)) << 1, road, wide
