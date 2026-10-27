"""Bounded BGM1 v4 client; explicit serialization into the unchanged S1 v2 ABI."""
import struct
import time
import math
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


def vehicle(rpy, velocity, angular_velocity, event_ok, sensors_ok, inputs_ok, posenet_ok, calib_ok, timestamp, eof):
  if not timestamp or eof > timestamp + 100_000_000 or not all(math.isfinite(x) for x in rpy):
    return 0, -32768, 0
  roll, pitch, yaw = rpy
  cr, sr, cp, sp, cy, sy = math.cos(roll), math.sin(roll), math.cos(pitch), math.sin(pitch), math.cos(yaw), math.sin(yaw)
  rot = np.array([[cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
                  [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
                  [-sp, cp * sr, cp * cr]])
  v = rot.T @ velocity
  w = rot.T @ angular_velocity
  speed, rate, flags = 0, -32768, 0
  if event_ok and sensors_ok and inputs_ok and posenet_ok and calib_ok and math.isfinite(v[0]):
    speed = min(65535, math.floor(abs(v[0]) * 100 + .5))
    flags |= 8 | (2 if v[0] < -.3 else 0) | (4 if math.hypot(v[0], v[1]) < .1 else 0)
  if event_ok and sensors_ok and calib_ok and math.isfinite(w[2]):
    value = max(-32767, min(32767, w[2] * 18000 / math.pi))
    rate = math.floor(value + .5) if value >= 0 else math.ceil(value - .5)
    flags |= 1
  return speed, rate, flags


def nav_ext(data):
  if len(data) != 8480:
    return None
  magic, version, kind, flags, _, size = HEADER.unpack_from(data)
  if (magic, version, kind, size) != (b'BGM1', 4, 0x11, 8440) or flags & ~3:
    return None
  present, health = struct.unpack_from('<BB', data, 8304)
  if not present or present & 0xf0 or health & 0xf0:
    return None
  state = struct.unpack_from('<BBBBIIIHBBH', data, 8308) if present & 1 else None
  curve = struct.unpack_from('<BBHHBB', data, 8348) if present & 4 else None
  if curve and curve[0] > 8:
    curve = None
  points = [struct.unpack_from('<iiHBB', data, 8356 + 12 * i) for i in range(curve[0])] if curve else []
  nav = {'health': health, 'state': state, 'curve': curve, 'points': points, 'road': None}
  if present & 2:  # ROAD_ATTR
    limit, source, ahead, road_class, ahead_m, age, edge, lanes, _, match_dm, _ = struct.unpack_from('<BBBBHHBBHHH', data, 8332)
    nav['road'] = {'speed_limit_kph': limit, 'speed_limit_source': source, 'speed_limit_ahead_kph': ahead,
                   'speed_limit_ahead_m': ahead_m, 'road_class': road_class, 'age_ms': age, 'lane_count': lanes,
                   'ramp': bool(edge & 1), 'tunnel': bool(edge & 2), 'bridge': bool(edge & 4), 'roundabout': bool(edge & 8),
                   'toll': bool(edge & 16), 'oneway': bool(edge & 32), 'matched': bool(edge & 128)}
  # Model blinker predictions (meta heads 33/34 + 4k: driver blinker at 0,2,4,6,8,10 s), logits -> probability.
  meta = np.frombuffer(data, '<f4', 55, 24 + 800 * 4).astype(np.float64)
  if np.isfinite(meta).all():
    nav['blinker'] = {side: [round(float(p), 3) for p in 1 / (1 + np.exp(-np.clip(meta[i:55:4], -30, 30)))]
                      for side, i in (('left', 33), ('right', 34))}
  eof = struct.unpack_from('<Q', data, 16)[0]
  if state and curve and state[7] != 65535 and curve[3] != 65535 and eof > max(state[7], curve[3]) * 1_000_000:
    nav['frame_eof_ns'] = eof
    nav['sample_time_ns'] = eof - curve[3] * 1_000_000
    nav['valid_until_ns'] = min(nav['sample_time_ns'], eof - state[7] * 1_000_000) + 2_000_000_000
    nav['target_points'] = [{'distance_m': p[2], 'velocity': p[3] / 3.6} for p in points
                            if p[3] and p[2] <= 600 and -900000000 <= p[0] <= 900000000
                            and -1800000000 <= p[1] <= 1800000000 and (p[0] or p[1])]
  return nav


def frame(meta, road, wide, flags, frame_idx, motion=(0, -32768, 0)):
  if not 0 < len(road) <= MAX_SEGMENT or not 0 < len(wide) <= MAX_SEGMENT or flags & ~3:
    raise ValueError('bad FRAME segment/flags')
  # META: magic/version/session/road ID/wide ID/road EOF/wide EOF/warp[18]/desire[8]/traffic[2]/action_t[2].
  values = meta[7:]
  header = HEADER.pack(b'BGM1', 4, 0x10, flags, frame_idx, len(road))
  header += struct.pack('<Q30f', meta[5], *values[18:26], *values[26:30], *values[:18]) + struct.pack('<HhB3x', *motion)
  return header + road, struct.pack('<I', len(wide)) + wide + bytes(16)


def to_mailbox(data, host_frame_id):
  frame_id, eof, flags = reply(data)
  # v4 adds 160B NavExt after unchanged outputs/telemetry; the mailbox ABI excludes it.
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
