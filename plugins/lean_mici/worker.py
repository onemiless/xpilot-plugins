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
from .wire import frame, hello, reply, receive, to_mailbox, receive_reply, vehicle, nav_ext, phone_timing

META = struct.Struct('<4sIQIIQQ30f')
ENVELOPE = struct.Struct('<QQQII')
OFFSET, SIZE = 256, 16384
HEARTBEAT = 160  # u64 BOOTTIME ns in the unused tail of the META slot [160, 256); the runner's supervisor reads it
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
  return data if data[:2] in ((b'XMS1', 1), (b'XMS1', 2)) else None


def desire_class(meta):
  return next((i for i, x in enumerate(meta[25:33]) if x > .5), 0)


def with_desire(host, pulse):
  return host if pulse else host[:25] + (0.,) * 8 + host[33:]


def die_with_parent():
  if sys.platform == 'linux':
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0):
      raise OSError(ctypes.get_errno(), 'PR_SET_PDEATHSIG')


def get_bitrate(params):
  # User-set param (dev-sp-npu BigmodelEncoderBitrate): per stream, bits/s.
  value = params.get('ExtLeanMiciBitrate', return_default=True)
  return min(max(value, 200_000), 20_000_000) if type(value) is int else 2_000_000


STAGES = ('recv', 'warp', 'pair', 'wake', 'encode', 'release', 'py_post')


def stages(times, sent):
  """ms per uplink stage from the native BOOTTIME stamps; they add up to EOF->send. None if the stamps are missing."""
  if not times or not all(times):
    return None
  points = (*times, sent)
  return {k: (b - a) / 1e6 for k, a, b in zip(STAGES, points, points[1:])}


def link_stats(stats, dropped, bitrate):
  """One line per second: the uplink stages dev-sp-npu publishes per frame in modelDataV2SP.bigStages."""
  def ms(key):
    values = [row[key] for row in stats if key in row]
    return [round(float(x), 1) for x in np.percentile(values, (50, 95))] if values else None
  late = sum(row['eof_to_reply'] > 60 for row in stats)
  # Phone-side times are absent (0) on older phone apps; then the network share stays unknown rather than negative.
  return dict(replies=len(stats), late=late, dropped=dropped, bitrate=bitrate,
              **{f'{k}_ms': ms(k) for k in (*STAGES, 'send')}, eof_to_send_ms=ms('eof_to_send'), rtt_ms=ms('rtt'), eof_to_reply_ms=ms('eof_to_reply'),
              srv_recv_ms=ms('srv_recv'), srv_prep_ms=ms('srv_prep'), srv_htp_ms=ms('srv_htp'),
              srv_total_ms=ms('srv_total'), net_ms=ms('net'))


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
      frame_idx, eof, host_frame, session, flags, epoch, paired, sent_ns, row = sent
      index, reply_eof, reply_flags = reply(data)
      if (index, reply_eof) != (frame_idx, eof):
        raise ValueError('REPLY does not echo sent frame/EOF')
      if reply_flags & 1 and flags != 3:
        raise ValueError('phone sequence reset requires fresh dual IDR')
      publish(fd, mailbox, session, epoch, arrival, paired, to_mailbox(data, host_frame))
      with cv:
        if pending and pending[0] is sent:  # a reconnect may already have replaced the queue with its stop marker
          pending.popleft()
        row = {**row, 'eof_to_send': (sent_ns - eof) / 1e6, 'rtt': (arrival - sent_ns) / 1e6,
               'eof_to_reply': (arrival - eof) / 1e6}
        timing = phone_timing(data)
        if timing[3]:
          row.update(zip(('srv_recv', 'srv_prep', 'srv_htp', 'srv_total'), (t / 1e3 for t in timing)))
          row['net'] = row['rtt'] - row['srv_total']
        stats.append(row)
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
  die_with_parent()
  if sys.platform == 'linux':
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
  shared = mmap.mmap(fd, SIZE)
  def beat():
    # Every loop that can block calls this; a worker stuck in native code (encoder_close) stops beating and is killed.
    struct.pack_into('<Q', shared, HEARTBEAT, now())
  epoch = 0
  # dev-sp-npu server_locator: retry the last good address, rediscover after three failed attempts.
  cached, misses = None, 0
  # One encoder pair across reconnects; only an encoder fault (RuntimeError) or 10 s without a link closes it.
  camera, bitrate, lost = None, None, time.monotonic()
  try:
    while os.getppid() == parent:
      beat()
      try:
        address, _ = scope('wlan0')
        with Beacon(address):
          while os.getppid() == parent:
            beat()
            if camera is not None and time.monotonic() - lost > 10:
              camera.close()
              camera = None
            try:
              if cached and misses < 3:
                peers = [cached]
              else:
                cached = None
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
              beat()
              misses += 1
              with socket.create_connection(peers[0][1:], timeout=1) as conn:
                conn.settimeout(1.)
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                hello(receive(conn, 48))
                cached, misses = peers[0], 0
                if not pinned and peers[0][0] != 'hotspot gateway':
                  # HELLO instance_id changes on every server start; pin the NSD name, not that per-start identifier.
                  pinned = {'name': peers[0][0]}
                  params.put('ExtLeanMiciPeer', pinned)
                if camera is None:
                  bitrate = get_bitrate(params)
                  camera = Camera(bitrate)
                # A dup has its own timeout, so the reply thread's per-message deadlines never shorten a send.
                reader = conn.dup()
                reader.settimeout(1.)
                pending, cv, stats, failure = deque(), threading.Condition(), [], []
                thread = threading.Thread(target=replies, args=(reader, pending, cv, fd, navigation, stats, failure),
                                          daemon=True)
                thread.start()
                first_eof = None
                epoch += 1
                host = used = None
                latched = 0  # ABI 2: desire class of the last frame actually sent
                paired = 0
                reset = True
                dropped = 0
                last_report = time.monotonic()
                try:
                  while os.getppid() == parent:
                    beat()
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
                    if host[1] == 1:  # ABI 1 host sends the rising edge on one frame; pass it on once
                      meta = with_desire(host, host[3] != used)
                      used = host[3]
                    else:  # ABI 2 host sends the level; a new sequence (reset) clears the latch
                      desire = desire_class(host)
                      meta = with_desire(host, desire and desire != (0 if reset else latched))
                    try:
                      encoded = camera.capture(meta, reset)
                    except ValueError:
                      # modeld's first METAs carry no calibration yet. Dropping the link here would close an encoder
                      # that never got a frame, and V4LEncoder::encoder_close then waits forever for EOS.
                      time.sleep(.005)
                      continue
                    except RuntimeError:
                      camera.close()
                      camera = None
                      raise
                    if encoded is None:
                      reset = True
                      continue
                    flags, road, wide, meta, *times = encoded
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
                    sent = now()
                    row = stages(times[0] if times else None, sent) or {}
                    with cv:
                      pending.append((frame_idx, meta[5], meta[3], meta[2], flags, epoch, paired, sent, row))
                      cv.notify_all()
                    conn.sendall(first)
                    conn.sendall(second)
                    row['send'] = (now() - sent) / 1e6  # the reply thread copies row only after the phone answered
                    if host[1] != 1:
                      latched = desire
                finally:
                  lost = time.monotonic()
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
      except Exception:
        cloudlog.exception('lean_mici.beacon_lost')
        time.sleep(1)
  finally:
    if camera is not None:
      camera.close()
    shared.close()
    os.close(fd)
