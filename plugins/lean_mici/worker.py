"""Optional child: Python VisionIPC/warp and existing HEVC, bounded TCP and private S1 mailbox."""
import fcntl
import os
import resource
import signal
import socket
import struct
import time
from jeepney.wrappers import DBusErrorResponse

from .network import Beacon, discover, scope
from .wire import frame, hello, reply, receive, to_mailbox, receive_reply

META = struct.Struct('<4sIQIIQQ30f')
ENVELOPE = struct.Struct('<QQQII')
OFFSET, SIZE = 256, 16384


def now():
  return time.clock_gettime_ns(getattr(time, 'CLOCK_BOOTTIME', time.CLOCK_MONOTONIC))


def publish(fd, session, epoch, arrival, paired, data):
  try:
    fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB, SIZE - OFFSET, OFFSET)
  except BlockingIOError:
    return
  try:
    os.pwrite(fd, bytes(ENVELOPE.size), OFFSET)
    os.pwrite(fd, data, OFFSET + ENVELOPE.size)
    envelope = ENVELOPE.pack(session, epoch, arrival, paired, len(data))
    os.pwrite(fd, envelope[:-4], OFFSET)
    os.pwrite(fd, envelope[-4:], OFFSET + ENVELOPE.size - 4)
  finally:
    fcntl.lockf(fd, fcntl.LOCK_UN, SIZE - OFFSET, OFFSET)


def metadata(fd):
  try:
    fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB, OFFSET, 0)
  except BlockingIOError:
    return None
  try:
    data = META.unpack(os.pread(fd, META.size, 0))
  finally:
    fcntl.lockf(fd, fcntl.LOCK_UN, OFFSET, 0)
  return data if data[:2] == (b'XMS1', 1) else None


def main(path):
  from .camera import Camera
  # A stuck encoder/network stack stays optional. The parent kills this whole child offroad.
  signal.signal(signal.SIGTERM, signal.SIG_DFL)
  resource.setrlimit(resource.RLIMIT_AS, (1024 * 1024 * 1024, 1024 * 1024 * 1024))
  resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
  camera = Camera()
  fd = os.open(path, os.O_RDWR)
  epoch = 0
  try:
    while True:
      try:
        address, _ = scope('wlan0')
        with Beacon(address):
          while True:
            peers = discover()
            if len(peers) != 1:
              time.sleep(1)
              continue
            try:
              with socket.create_connection(peers[0], timeout=1) as conn:
                conn.settimeout(.2)
                hello(receive(conn, 48))
                first_eof = None
                epoch += 1
                last = None
                paired = 0
                reset = True
                while True:
                  meta = metadata(fd)
                  if meta is None or meta[3] == last:
                    time.sleep(.001)
                    continue
                  if last is not None and meta[3] != last + 1:
                    reset = True
                    epoch += 1
                    paired = 0
                  last = meta[3]
                  encoded = camera.capture(meta, reset)
                  if encoded is None:
                    reset = True
                    continue
                  flags, road, wide = encoded
                  if reset and flags != 3:
                    continue
                  if flags == 3:
                    epoch += 1
                    paired = 3
                  reset = False
                  if first_eof is None:
                    first_eof = meta[5]
                  frame_idx = (meta[5] - first_eof + 25_000_000) // 50_000_000
                  first, second = frame(meta, road, wide, flags, frame_idx)
                  conn.sendall(first)
                  conn.sendall(second)
                  data = receive_reply(conn)
                  arrival = now()
                  index, eof, _ = reply(data)
                  if (index, eof) != (frame_idx, meta[5]):
                    raise ValueError('REPLY does not echo sent frame/EOF')
                  publish(fd, meta[2], epoch, arrival, paired, to_mailbox(data, meta[3]))
            except OSError:
              time.sleep(.1)
      except (OSError, DBusErrorResponse):
        time.sleep(1)
  finally:
    os.close(fd)
