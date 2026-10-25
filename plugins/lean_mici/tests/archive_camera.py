"""Offline replay fixture only; never imported by the device plugin."""
from fractions import Fraction

import av
import numpy as np


def warp(buf, matrix):
  if not np.isfinite(matrix).all() or abs(np.linalg.det(matrix)) < 1e-9:
    raise ValueError('camera calibration not ready')
  image = np.frombuffer(buf.data, dtype=np.uint8)
  planes = []
  for scale in (1, 2):
    height, width = 256 // scale, 512 // scale
    y, x = np.indices((height, width), dtype=np.float32)
    m = matrix if scale == 1 else matrix * np.array([[1, 1, .5], [1, 1, .5], [2, 2, 1]], dtype=np.float32)
    denominator = m[2, 0] * x + m[2, 1] * y + m[2, 2]
    sx = np.rint((m[0, 0] * x + m[0, 1] * y + m[0, 2]) / denominator).clip(0, buf.width // scale - 1).astype(np.int32)
    sy = np.rint((m[1, 0] * x + m[1, 1] * y + m[1, 2]) / denominator).clip(0, buf.height // scale - 1).astype(np.int32)
    if scale == 1:
      planes.append(image[sy * buf.stride + sx].ravel())
    else:
      offsets = buf.uv_offset + sy * buf.stride + 2 * sx
      planes.append(np.stack((image[offsets], image[offsets + 1]), axis=-1).ravel())
  return np.concatenate(planes).reshape(384, 512)


def idr(data):
  # Annex-B NAL type, independent of the encoder's key-frame prediction.
  return any(part and ((part[0] >> 1) & 63) in (19, 20) for part in data.replace(b'\0\0\0\1', b'\0\0\1').split(b'\0\0\1')[1:])


class Encoder:
  def __init__(self, wide=False):
    self.wide, self.sequence = wide, 0
    from openpilot.common.hardware import AGNOS
    name = 'hevc_v4l2m2m' if AGNOS else 'libx265'
    self.codec = av.CodecContext.create(name, 'w')
    self.codec.width, self.codec.height = 512, 256
    self.codec.time_base, self.codec.framerate = Fraction(1, 20), Fraction(20, 1)
    self.codec.pix_fmt = 'yuv420p' if name == 'libx265' else 'nv12'
    self.codec.bit_rate, self.codec.gop_size, self.codec.max_b_frames = 2_000_000, 20, 0
    if name == 'libx265':
      self.codec.options = {'preset': 'ultrafast', 'tune': 'zerolatency', 'forced-idr': '1',
                            'x265-params': 'keyint=20:min-keyint=20:scenecut=0:repeat-headers=1:aud=1:open-gop=0:bframes=0:frame-threads=1:pools=none:log-level=error'}
    self.codec.open()

  def encode(self, image, reset=False):
    if reset:
      self.sequence = 0
    frame = av.VideoFrame.from_ndarray(image, format='nv12').reformat(format=self.codec.pix_fmt)
    frame.pts = self.sequence
    if reset or self.wide and self.sequence == 10:
      frame.pict_type = av.video.frame.PictureType.I
    self.sequence += 1
    packets = self.codec.encode(frame)
    if len(packets) != 1:
      raise ValueError('encoder must emit one complete AU without delay')
    data = bytes(packets[0])
    return data, idr(data)
