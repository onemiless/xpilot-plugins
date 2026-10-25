"""Bounded BGM1 v4 client; explicit serialization into the unchanged S1 v2 ABI."""
import struct
import time
import numpy as np

HEADER = struct.Struct('<4sBBHII')
MAX_SEGMENT = 1024 * 1024


def receive(sock, count, deadline=None):
  deadline = time.monotonic() + .2 if deadline is None else deadline
  timeout = sock.gettimeout()
  data = bytearray()
  try:
    while len(data) < count:
      remaining = deadline - time.monotonic()
      if remaining <= 0:
        raise TimeoutError('BGM1 message deadline')
      sock.settimeout(min(timeout, remaining) if timeout is not None else remaining)
      chunk = sock.recv(count - len(data))
      if not chunk:
        raise ConnectionError('truncated BGM1 message')
      data.extend(chunk)
    if time.monotonic() > deadline:
      raise TimeoutError('BGM1 message deadline')
  finally:
    sock.settimeout(timeout)
  return bytes(data)


def hello(data):
  if len(data) != 48 or HEADER.unpack_from(data) != (b'BGM1', 4, 1, 0, 0, 16):
    raise ValueError('BGM1 v4 HELLO required')
  instance, limit, reserved = struct.unpack_from('<QII', data, 16)
  if not instance or not 0 < limit <= MAX_SEGMENT or reserved:
    raise ValueError('bad HELLO limits')
  return instance, limit


def reply(data):
  if len(data) != 8480:
    raise ValueError('REPLY must be exactly 8480 bytes')
  magic, version, kind, flags, frame, size = HEADER.unpack_from(data)
  if (magic, version, kind, size) != (b'BGM1', 4, 0x11, 8440) or flags & ~3:
    raise ValueError('bad REPLY ABI')
  raw = np.frombuffer(data, '<f4', 2066, 24)
  if not np.isfinite(raw).all() or not np.any(raw):
    raise ValueError('bad REPLY output')
  return frame, struct.unpack_from('<Q', data, 16)[0], flags


def frame(meta, road, wide, flags, frame_idx):
  if not 0 < len(road) <= MAX_SEGMENT or not 0 < len(wide) <= MAX_SEGMENT or flags & ~3:
    raise ValueError('bad FRAME segment/flags')
  # META: magic/version/session/road ID/wide ID/road EOF/wide EOF/warp[18]/desire[8]/traffic[2]/action_t[2].
  values = meta[7:]
  header = HEADER.pack(b'BGM1', 4, 0x10, flags, frame_idx, len(road))
  header += struct.pack('<Q30f', meta[5], *values[18:26], *values[26:30], *values[:18]) + struct.pack('<HhB3x', 0, -32768, 0)
  return header + road, struct.pack('<I', len(wide)) + wide + bytes(16)


def to_mailbox(data, host_frame_id):
  frame_id, eof, flags = reply(data)
  # v4 adds 160B NavExt after unchanged outputs/telemetry. Navigation is not consumed.
  return HEADER.pack(b'BGM1', 2, 0x11, flags, host_frame_id, 8280) + struct.pack('<Q', eof) + data[24:8304] + data[8464:8480]


def receive_reply(sock):
  deadline = time.monotonic() + .2
  header = receive(sock, HEADER.size, deadline)
  magic, version, kind, flags, index, length = HEADER.unpack(header)
  if (magic, version) != (b'BGM1', 4):
    raise ValueError('bad v4 message header')
  if kind == 0x7f and length == 8:
    return header + receive(sock, 24, deadline)
  if kind != 0x11 or length != 8440:
    raise ValueError(f'bad v4 reply framing: type={kind} length={length}')
  return header + receive(sock, 8464, deadline)
