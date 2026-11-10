"""Optional child: Python VisionIPC/warp and existing HEVC, bounded TCP and private S1 mailbox."""
from collections import deque
import contextlib
import ctypes
import fcntl
import mmap
import os
import resource
import signal
import socket
import struct
import sys
import threading
import time
import numpy as np
from queue import Full
from openpilot.common.swaglog import cloudlog
from openpilot.tesla.plugin_params import ParamsView

from .network import Beacon, discover, gateway, scope
from .wire import frame, hello, reply, receive, to_mailbox, receive_reply, vehicle, nav_ext

META = struct.Struct('<4sIQIIQQ30f')
ENVELOPE = struct.Struct('<QQQII')
OFFSET, SIZE = 256, 16384
IN_FLIGHT = 2  # Chipmunk sender: one on the phone + one queued; busy skips encoding, never drops encoded frames


def now():
  return time.clock_gettime_ns(getattr(time, 'CLOCK_BOOTTIME', time.CLOCK_MONOTONIC))


def publish(fd, mailbox, session, epoch, arrival, paired, data):
  payload = ENVELOPE.pack(session, epoch, arrival, paired, 0) + data
  length = struct.pack('<I', len(data))
  # pwrite updates ext4 inode timestamps and can wait on its journal while holding the reader's lock.
  fcntl.lockf(fd, fcntl.LOCK_EX, SIZE - OFFSET, OFFSET)
  try:
    mailbox[OFFSET + ENVELOPE.size - 4:OFFSET + ENVELOPE.size] = b"\0" * 4
    mailbox[OFFSET:OFFSET + len(payload)] = payload
    mailbox[OFFSET + ENVELOPE.size - 4:OFFSET + ENVELOPE.size] = length
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


def get_bitrate(params):
  # User-set param (dev-sp-npu BigmodelEncoderBitrate): per stream, bits/s.
  value = params.get('ExtLeanMiciBitrate', return_default=True)
  return min(max(value, 200_000), 20_000_000) if type(value) is int else 2_000_000


def link_stats(stats, dropped, bitrate):
  """One line per second: the uplink stages dev-sp-npu publishes per frame in modelDataV2SP.bigStages."""
  def ms(key):
    values = [row[key] for row in stats]
    return [round(float(x), 1) for x in np.percentile(values, (50, 95))] if values else None
  late = sum(row['eof_to_reply'] > 60 for row in stats)
  return dict(replies=len(stats), late=late, dropped=dropped, bitrate=bitrate, capture_ms=ms('capture'),
              eof_to_send_ms=ms('eof_to_send'), rtt_ms=ms('rtt'), eof_to_reply_ms=ms('eof_to_reply'))


def replies(conn, pending, cv, fd, navigation, stats, failure):
  """Replies arrive on their own thread, so the next frame goes out without waiting for this one."""
  last_nav = 0
  mailbox = mmap.mmap(fd, SIZE)
  try:
    while True:
      with cv:
        while not pending:
          cv.wait()
        sent = pending[0]
      if sent is None:
        return
      data = receive_reply(conn)
      arrival = now()
      frame_idx, eof, host_frame, session, flags, epoch, paired, sent_ns, capture_ms = sent
      index, reply_eof, reply_flags = reply(data)
      if (index, reply_eof) != (frame_idx, eof):
        raise ValueError('REPLY does not echo sent frame/EOF')
      if reply_flags & 1 and flags != 3:
        raise ValueError('phone sequence reset requires fresh dual IDR')
      publish(fd, mailbox, session, epoch, arrival, paired, to_mailbox(data, host_frame))
      with cv:
        if pending and pending[0] is sent:  # a reconnect may already have replaced the queue with its stop marker
          pending.popleft()
        stats.append({'capture': capture_ms, 'eof_to_send': (sent_ns - eof) / 1e6, 'rtt': (arrival - sent_ns) / 1e6,
                      'eof_to_reply': (arrival - eof) / 1e6})
        cv.notify_all()
      if navigation is not None and time.monotonic() - last_nav >= .5:
        last_nav = time.monotonic()
        try:
          navigation.put_nowait((last_nav, nav_ext(data)))
        except Full:
          pass
  except Exception as e:
    with cv:
      failure.append(e)
    try:
      conn.shutdown(socket.SHUT_RDWR)
    except OSError:
      pass
  finally:
    mailbox.close()


def main(path, parent, navigation=None):
  if sys.platform == 'linux':
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0):
      raise OSError(ctypes.get_errno(), 'PR_SET_PDEATHSIG')
    # dev-sp-npu bigmodeld placement: cores 2 and 6 at SCHED_FIFO 50, away from modeld (core 7, FIFO 54) and
    # controls/ui (cores 4/5, FIFO 53). Threads and the Venus encoder callbacks started later inherit it.
    os.sched_setaffinity(0, {2, 6} & os.sched_getaffinity(0) or os.sched_getaffinity(0))
    try:
      os.sched_setscheduler(0, os.SCHED_FIFO, os.sched_param(50))
    except PermissionError:
      cloudlog.warning('lean_mici: SCHED_FIFO not permitted, running at normal priority')
  if os.getppid() != parent:
    return
  from .camera import Camera
  from openpilot.cereal import messaging
  sm = messaging.SubMaster(['deviceMotion', 'extrinsicsCalibration'])
  # A stuck encoder/network stack stays optional. The parent kills this whole child offroad.
  signal.signal(signal.SIGTERM, signal.SIG_DFL)
  resource.setrlimit(resource.RLIMIT_AS, (1024 * 1024 * 1024, 1024 * 1024 * 1024))
  resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
  params = ParamsView(None, {'ExtLeanMiciPeer': {'type': 'JSON'},
                              'ExtLeanMiciBitrate': {'type': 'INT', 'default': '2000000'}})
  p = params.get('ExtLeanMiciPeer')
  pinned = p if isinstance(p, dict) and isinstance(p.get('name'), str) else None
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
              if not peers and sys.platform == 'linux':
                address = gateway()
                peers = [('hotspot gateway', address, 7070)] if address else []
              if len(peers) != 1:
                time.sleep(1)
                continue
              with socket.create_connection(peers[0][1:], timeout=1) as conn:
                conn.settimeout(.2)
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                hello(receive(conn, 48))
                if not pinned and peers[0][0] != 'hotspot gateway':
                  # HELLO instance_id changes on every server start; pin the NSD name, not that per-start identifier.
                  pinned = {'name': peers[0][0]}
                  params.put('ExtLeanMiciPeer', pinned)
                bitrate = get_bitrate(params)
                camera = Camera(bitrate)
                # A dup has its own timeout, so the reply thread's per-message deadlines never shorten a send.
                reader = conn.dup()
                reader.settimeout(.2)
                pending, cv, stats, failure = deque(), threading.Condition(), [], []
                thread = threading.Thread(target=replies, args=(reader, pending, cv, fd, navigation, stats, failure),
                                          daemon=True)
                thread.start()
                first_eof = None
                epoch += 1
                host = used = None
                paired = 0
                reset = True
                dropped = 0
                last_report = time.monotonic()
                try:
                  while os.getppid() == parent:
                    if failure:
                      raise failure[0]
                    if time.monotonic() - last_report >= 1:
                      last_report = time.monotonic()
                      with cv:
                        rows, stats[:] = stats[:], []
                      cloudlog.event('lean_mici.link', **link_stats(rows, dropped, bitrate))
                      dropped = 0
                      if (new := get_bitrate(params)) != bitrate:
                        camera.set_bitrate(new)
                        bitrate = new
                    # Frames come straight from camerad (dev-sp-npu bigmodeld), so a late modeld publish never delays
                    # the next send. modeld's latest META supplies session, calibration and its inputs, one frame old.
                    with cv:
                      if len(pending) >= IN_FLIGHT:
                        dropped += 1
                        cv.wait_for(lambda: len(pending) < IN_FLIGHT, .05)
                        continue
                    host = metadata(fd) or host
                    if host is None:
                      time.sleep(.005)
                      continue
                    meta = host if host[3] != used else host[:25] + (0.,) * 8 + host[33:]  # send a desire pulse once
                    used = host[3]
                    started = now()
                    try:
                      encoded = camera.capture(meta, reset)
                    except ValueError:
                      # modeld's first METAs carry no calibration yet. Dropping the link here would close an encoder
                      # that never got a frame, and V4LEncoder::encoder_close then waits forever for EOS.
                      time.sleep(.005)
                      continue
                    capture_ms = (now() - started) / 1e6
                    if encoded is None:
                      reset = True
                      continue
                    flags, road, wide, meta = encoded
                    if reset and flags != 3:
                      continue
                    if flags == 3:
                      epoch += 1
                      paired = 3
                    reset = False
                    if first_eof is None:
                      first_eof = meta[5]
                    frame_idx = (meta[5] - first_eof + 25_000_000) // 50_000_000
                    sm.update(0)
                    motion, calib = sm['deviceMotion'], sm['extrinsicsCalibration']
                    v, w = motion.velocityDevice, motion.angularVelocityDevice
                    rpy = calib.rpyCalib if len(calib.rpyCalib) == 3 else (float('nan'),) * 3
                    tail = vehicle(rpy, (v.x, v.y, v.z), (w.x, w.y, w.z), sm.valid['deviceMotion'],
                                   motion.sensorsOK, motion.inputsOK, motion.posenetOK,
                                   sm.valid['extrinsicsCalibration'] and calib.calStatus != 'invalid',
                                   sm.logMonoTime['deviceMotion'], meta[5])
                    first, second = frame(meta, road, wide, flags, frame_idx, tail)
                    with cv:
                      pending.append((frame_idx, meta[5], meta[3], meta[2], flags, epoch, paired, now(), capture_ms))
                      cv.notify_all()
                    conn.sendall(first)
                    conn.sendall(second)
                finally:
                  with cv:
                    pending.clear()
                    pending.append(None)  # stop marker for the reply thread
                    cv.notify_all()
                  with contextlib.suppress(OSError):
                    conn.shutdown(socket.SHUT_RDWR)  # also ends a reply wait on the dup
                  thread.join(1)
                  reader.close()
            except Exception:
              cloudlog.exception('lean_mici.link_lost')  # why the phone link dropped (each drop costs a reconnect and warm-up)
              time.sleep(.1)
            finally:
              if camera is not None:
                camera.close()
      except Exception:
        cloudlog.exception('lean_mici.beacon_lost')
        time.sleep(1)
  finally:
    os.close(fd)
