"""Bounded BGM1 v2; no protocol conversion or model padding."""
import struct
import numpy as np

HEADER = struct.Struct('<4sBBHII')
MAX_SEGMENT = 1024 * 1024


def receive(sock, count):
  data = bytearray()
  while len(data) < count:
    chunk = sock.recv(count - len(data))
    if not chunk:
      raise ConnectionError('truncated BGM1 message')
    data.extend(chunk)
  return bytes(data)


def hello(data):
  if len(data) != 48 or HEADER.unpack_from(data) != (b'BGM1', 2, 1, 0, 0, 16):
    raise ValueError('BGM1 v2 HELLO required')
  instance, limit, reserved = struct.unpack_from('<QII', data, 16)
  if not instance or not 0 < limit <= MAX_SEGMENT or reserved:
    raise ValueError('bad HELLO limits')
  return instance, limit


def reply(data):
  if len(data) != 8320:
    raise ValueError('REPLY must be exactly 8320 bytes')
  magic, version, kind, flags, frame, size = HEADER.unpack_from(data)
  if (magic, version, kind, size) != (b'BGM1', 2, 0x11, 8280) or flags & ~3:
    raise ValueError('bad REPLY ABI')
  raw = np.frombuffer(data, '<f4', 2066, 24)
  if not np.isfinite(raw).all() or not np.any(raw):
    raise ValueError('bad REPLY output')
  return frame, struct.unpack_from('<Q', data, 16)[0], flags


def frame(meta, road, wide, flags):
  if not 0 < len(road) <= MAX_SEGMENT or not 0 < len(wide) <= MAX_SEGMENT or flags & ~3:
    raise ValueError('bad FRAME segment/flags')
  # META: magic/version/session/road ID/wide ID/road EOF/wide EOF/warp[18]/desire[8]/traffic[2]/action_t[2].
  values = meta[7:]
  header = HEADER.pack(b'BGM1', 2, 0x10, flags, meta[3], len(road))
  header += struct.pack('<Q30f', meta[5], *values[18:26], *values[26:30], *values[:18]) + bytes(16)
  return header + road, struct.pack('<I', len(wide)) + wide + bytes(16)
