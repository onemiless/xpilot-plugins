"""Optional child: Python VisionIPC/warp and existing HEVC, bounded TCP and private S1 mailbox."""
import ctypes
import fcntl
import os
import resource
import signal
import socket
import struct
import sys
import time
from openpilot.tesla.plugin_params import ParamsView

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


def main(path, parent):
  if sys.platform == 'linux':
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0):
      raise OSError(ctypes.get_errno(), 'PR_SET_PDEATHSIG')
  if os.getppid() != parent:
    return
  from .camera import Camera
  # A stuck encoder/network stack stays optional. The parent kills this whole child offroad.
  signal.signal(signal.SIGTERM, signal.SIG_DFL)
  resource.setrlimit(resource.RLIMIT_AS, (1024 * 1024 * 1024, 1024 * 1024 * 1024))
  resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
  params = ParamsView(None, {'ExtLeanMiciPeer': {'type': 'JSON'}})
  pinned = params.get('ExtLeanMiciPeer')
  ignored = set()
  fd = os.open(path, os.O_RDWR)
  epoch = 0
  try:
    while os.getppid() == parent:
      try:
        address, _ = scope('wlan0')
        with Beacon(address):
          while os.getppid() == parent:
            camera = None
            try:
              peers = discover()
              if pinned:
                for peer in peers:
                  if peer[0] != pinned['name'] and peer[0] not in ignored:
                    print('lean_mici: ignoring unpinned advertiser', peer[0], flush=True)
                    ignored.add(peer[0])
                peers = [peer for peer in peers if peer[0] == pinned['name']]
              if len(peers) != 1:
                time.sleep(1)
                continue
              with socket.create_connection(peers[0][1:], timeout=1) as conn:
                conn.settimeout(.2)
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                hello(receive(conn, 48))
                if not pinned:
                  # HELLO instance_id changes on every server start; pin the NSD name, not that per-start identifier.
                  pinned = {'name': peers[0][0]}
                  params.put('ExtLeanMiciPeer', pinned)
                camera = Camera()
                first_eof = None
                epoch += 1
                last = None
                paired = 0
                reset = True
                while os.getppid() == parent:
                  # These unlocked words are hints only; the full metadata is still validated under its lock.
                  if os.pread(fd,4,0)!=b'XMS1' or last is not None and int.from_bytes(os.pread(fd,4,16),'little')==last:
                    time.sleep(.001)
                    continue
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
                  index, eof, reply_flags = reply(data)
                  if (index, eof) != (frame_idx, meta[5]):
                    raise ValueError('REPLY does not echo sent frame/EOF')
                  if reply_flags & 1 and flags != 3:
                    raise ValueError('phone sequence reset requires fresh dual IDR')
                  publish(fd, meta[2], epoch, arrival, paired, to_mailbox(data, meta[3]))
            except Exception:
              time.sleep(.1)
            finally:
              camera = None
      except Exception:
        time.sleep(1)
  finally:
    os.close(fd)
