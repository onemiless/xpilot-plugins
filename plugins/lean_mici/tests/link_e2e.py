#!/usr/bin/env python3
"""Worker self-recovery, reconnect speed and desire edges over real sockets and a real mailbox (0.8.13).

Failure cases, listed before the fix:
S1 the worker is SIGKILLed or aborts (V4LEncoder assert) and no worker comes back.
S2 the worker hangs in a native call that never returns (encoder_close) and is never replaced.
S3 the supervisor outlives lean_modeld.
S4 restarts do not back off.
R1 the phone restarts on a new address; the cached address keeps failing and discovery never runs.
R2 a 300 ms TCP stall drops the link and re-warms.
R3 a network drop rebuilds the encoder.
R4 an encoder fault (RuntimeError) does not rebuild the encoder.
R5 another phone (NSD name not pinned) is connected after the pinned one went away.
D1 the desire rising edge falls on a frame that is never sent (capture failure / waiting for dual IDR) and is lost.
D2 the rising edge arrives while the reply queue is full and is lost.
D3 a held desire is sent as a pulse on every frame.
D4 after a sequence reset the desire is still on but is not sent again.
D5 an ABI 1 host (desire edge in META) changes behavior.
"""
import argparse, contextlib, json, multiprocessing, os, signal, socket, struct, sys, tempfile, threading, time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from plugins.lean_mici import worker, wire

HELLO = wire.HEADER.pack(b'BGM1', 4, 1, 0, 0, 16) + struct.pack('<QII', 123, 1024 * 1024, 0) + bytes(16)


def write_meta(fd, abi, desire=0, frame=1):
  level = [0.] * 8
  if desire:
    level[desire] = 1.
  eof = worker.now()
  os.pwrite(fd, worker.META.pack(b'XMS1', abi, 42, frame, frame, eof, eof, *([1., 0., 0., 0., 1., 0., 0., 0., 1.] * 2), *level,
                                 *([0.] * 4)), 0)


# ---- supervisor ----

def fake_worker(mode, root):
  def main(path, parent, navigation=None):
    worker.die_with_parent()
    with open(root / 'starts', 'a') as f:
      f.write(f'{time.monotonic()} {os.getpid()}\n')
    if mode == 'crash':
      os.kill(os.getpid(), signal.SIGKILL)
    time.sleep(60)  # hang: never beats
  return main


def supervisor(path, parent, mode, root):
  import types
  # runner's modeld import needs the built Params library; the supervisor itself does not use it.
  sys.modules['openpilot.selfdrive.modeld.modeld'] = types.SimpleNamespace(run_with_model_source=None)
  from plugins.lean_mici import runner
  worker.main = fake_worker(mode, Path(root))
  runner.supervise(path, parent, None, 500_000_000)


def supervisor_parent(path, mode, root, control):
  ctx = multiprocessing.get_context('fork')
  p = ctx.Process(target=supervisor, args=(path, os.getpid(), mode, root))
  p.start()
  control.send(p.pid)
  time.sleep(60)


def starts(root):
  f = root / 'starts'
  return [line.split() for line in f.read_text().splitlines()] if f.exists() else []


def gone(pid):
  try:
    os.kill(pid, 0)
  except ProcessLookupError:
    return True
  with open(f'/proc/{pid}/stat') as f:
    return f.read().split(')')[1].split()[0] == 'Z'


def wait(predicate, timeout, step=.02):
  end = time.monotonic() + timeout
  while time.monotonic() < end:
    if predicate():
      return True
    time.sleep(step)
  return predicate()


def check_supervisor(tmp, path):
  checks = []
  ctx = multiprocessing.get_context('fork')
  for mode in ('crash', 'hang'):
    root = Path(tmp) / mode
    root.mkdir()
    p = ctx.Process(target=supervisor, args=(path, os.getpid(), mode, str(root)))
    p.start()
    try:
      assert wait(lambda: len(starts(root)) >= 3, 6), f'S1/S2 {mode}: {starts(root)}'
      rows = starts(root)
      gaps = [float(b[0]) - float(a[0]) for a, b in zip(rows, rows[1:])]
      assert gaps[1] > gaps[0] + .8, f'S4 {mode} no backoff: {gaps}'
      assert all(gone(int(r[1])) for r in rows[:-1]), f'S2 {mode}: an old worker is still alive'
      checks += [f'{"S1" if mode == "crash" else "S2"}_{mode}_restarted', f'S4_{mode}_backoff']
    finally:
      p.kill()
      p.join()
  root = Path(tmp) / 'orphan'
  root.mkdir()
  parent_end, child_end = ctx.Pipe()
  middle = ctx.Process(target=supervisor_parent, args=(path, 'hang', str(root), child_end))
  middle.start()
  assert parent_end.poll(5)
  sup = parent_end.recv()
  assert wait(lambda: starts(root), 3)
  child = int(starts(root)[0][1])
  middle.kill()
  middle.join()
  assert wait(lambda: gone(sup) and gone(child), 3), 'S3 supervisor or worker outlived the parent'
  checks.append('S3_parent_death')
  return checks


# ---- link ----

class Peer:
  """One fake phone: listens, sends HELLO, reads FRAMEs, replies after an optional delay."""
  def __init__(self):
    self.server = socket.socket()
    self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    self.server.bind(('127.0.0.1', 0))
    self.server.listen(4)
    self.address = self.server.getsockname()
    self.accepts, self.frames, self.delay, self.reset_next = 0, [], 0., False
    self.conn, self.closed = None, threading.Event()
    threading.Thread(target=self.run, daemon=True).start()

  def run(self):
    while True:
      try:
        conn, _ = self.server.accept()
      except OSError:
        return
      self.accepts += 1
      self.conn = conn
      self.closed.clear()
      try:
        conn.sendall(HELLO)
        while True:
          head = wire.receive(conn, 152, time.monotonic() + 5)
          road = wire.HEADER.unpack_from(head)[-1]
          wire.receive(conn, road, time.monotonic() + 5)
          wide = struct.unpack('<I', wire.receive(conn, 4, time.monotonic() + 5))[0]
          wire.receive(conn, wide + 16, time.monotonic() + 5)
          flags, index = wire.HEADER.unpack_from(head)[3:5]
          eof = struct.unpack_from('<Q', head, 16)[0]
          desire = struct.unpack_from('<8f', head, 24)
          self.frames.append((self.accepts, index, flags, desire.index(1.) if 1. in desire else 0))
          reply_flags = 1 if self.reset_next else 0
          self.reset_next = False
          reply = struct.pack('<4sBBHIIQ', b'BGM1', 4, 0x11, reply_flags, index, 8440, eof) + struct.pack('<2066f', *([1.] * 2066))
          reply += struct.pack('<4I', 1000, 2000, 14000, 18000) + bytes(176)
          if self.delay:
            time.sleep(self.delay)
            self.delay = 0.
          conn.sendall(reply)
      except (OSError, TimeoutError, ConnectionError):
        pass
      finally:
        conn.close()
        self.closed.set()

  def close(self):
    self.server.close()
    if self.conn:
      with contextlib.suppress(OSError):
        self.conn.shutdown(socket.SHUT_RDWR)


def link_child(path, parent, root):
  from openpilot.tesla import manifest
  root = Path(root)
  manifest.INSTALL_DIR = root
  cameras = 0

  class Capture:
    def __init__(self, bitrate):
      nonlocal cameras
      cameras += 1
      (root / 'cameras').write_text(str(cameras))
      self.n = 0

    def close(self):
      pass

    def set_bitrate(self, bitrate):
      pass

    def capture(self, meta, reset):
      time.sleep(.05)
      self.n += 1
      if (root / 'fail_encoder').exists():
        (root / 'fail_encoder').unlink()
        raise RuntimeError('hardware encoder deadline')
      if (root / 'fail_capture').exists():
        (root / 'fail_capture').unlink()
        return None
      eof = worker.now()
      return 3 if reset else 0, b'road', b'wide', meta[:3] + (self.n, self.n, eof, eof) + meta[7:]

  def discover():
    with open(root / 'discover_calls', 'a') as f:
      f.write('x')
    return [tuple(x) for x in json.loads((root / 'peers').read_text())]

  with (patch('plugins.lean_mici.camera.Camera', Capture), patch.object(worker.resource, 'setrlimit'),
        patch.object(worker, 'scope', return_value=('127.0.0.1', None)), patch.object(worker, 'gateway', return_value=None),
        patch.object(worker, 'Beacon', side_effect=lambda a: contextlib.nullcontext()), patch.object(worker, 'discover', discover)):
    worker.main(path, parent)


def check_link(tmp, path, fd):
  checks = []
  root = Path(tmp) / 'link'
  (root / '.params').mkdir(parents=True)
  (root / '.params/ExtLeanMiciPeer').write_text(json.dumps({'name': 'test phone'}))
  a, b, c = Peer(), Peer(), Peer()
  (root / 'peers').write_text(json.dumps([['test phone', *a.address]]))
  write_meta(fd, 2)
  p = multiprocessing.get_context('spawn').Process(target=link_child, args=(path, os.getpid(), str(root)))
  p.start()
  calls = lambda: len((root / 'discover_calls').read_text()) if (root / 'discover_calls').exists() else 0
  cameras = lambda: int((root / 'cameras').read_text()) if (root / 'cameras').exists() else 0
  try:
    assert wait(lambda: len(a.frames) >= 5, 10), 'no frames on phone A'

    # D3/D1: a held desire is sent exactly once; the edge frame fails capture and the pulse moves to the next sent frame.
    (root / 'fail_capture').touch()
    write_meta(fd, 2, desire=3)
    mark = len(a.frames)
    assert wait(lambda: len(a.frames) >= mark + 8, 5)
    pulses = [f for f in a.frames[mark:] if f[3]]
    assert len(pulses) == 1 and pulses[0][3] == 3, f'D1/D3 {a.frames[mark:]}'
    assert pulses[0][2] == 3, f'D1 pulse not on the new dual-IDR frame: {pulses}'
    checks += ['D1_edge_on_skipped_frame_resent', 'D3_held_desire_one_pulse']

    # D4: phone sequence reset (flag on a non-IDR frame) drops the link; the new sequence resends the held desire once.
    a.reset_next = True
    assert wait(lambda: a.accepts >= 2 and len([f for f in a.frames if f[0] == 2]) >= 5, 10), 'D4 no reconnect'
    second = [f for f in a.frames if f[0] == 2]
    assert [f[3] for f in second].count(3) == 1 and second[0][3] == 3, f'D4 {second}'
    checks.append('D4_resend_after_sequence_reset')

    # D2: desire clears, then rises again while the queue is full (one reply held 0.6 s, IN_FLIGHT 2).
    write_meta(fd, 2, desire=0)
    time.sleep(.2)
    a.delay = .6
    time.sleep(.35)
    mark = len(a.frames)
    write_meta(fd, 2, desire=4)
    assert wait(lambda: len(a.frames) >= mark + 6, 5)
    assert [f[3] for f in a.frames[mark:]].count(4) == 1, f'D2 {a.frames[mark:]}'
    checks.append('D2_edge_while_queue_full')
    write_meta(fd, 2, desire=0)

    # R2: a 300 ms stall keeps the connection.
    accepts = a.accepts
    a.delay = .3
    mark = len(a.frames)
    assert wait(lambda: len(a.frames) >= mark + 10, 5) and a.accepts == accepts, 'R2 stall dropped the link'
    checks.append('R2_300ms_stall_keeps_link')

    # R4: an encoder fault rebuilds the encoder.
    before = cameras()
    (root / 'fail_encoder').touch()
    assert wait(lambda: cameras() == before + 1 and a.accepts == accepts + 1, 5), f'R4 cameras={cameras()}'
    checks.append('R4_encoder_fault_rebuilds')

    # R1/R3: phone A goes away, phone B (same pinned name) appears elsewhere. Three cached tries, then discovery.
    assert wait(lambda: len([f for f in a.frames if f[0] == a.accepts]) >= 3, 5)
    before, discovered = cameras(), calls()
    (root / 'peers').write_text(json.dumps([['test phone', *b.address]]))
    a.close()
    assert wait(lambda: len(b.frames) >= 3, 10), f'R1 never reached phone B (discover calls {calls()})'
    assert calls() == discovered + 1, f'R1 discovery ran {calls() - discovered} times'
    assert cameras() == before, 'R3 network drop rebuilt the encoder'
    checks += ['R1_cached_then_rediscover', 'R3_network_drop_keeps_encoder']

    # R5: phone B goes away; only an unpinned advertiser is left.
    (root / 'peers').write_text(json.dumps([['other phone', *c.address]]))
    b.close()
    time.sleep(5)
    assert c.accepts == 0, 'R5 connected to an unpinned phone'
    checks.append('R5_unpinned_not_connected')
  finally:
    p.kill()
    p.join()
    for peer in (a, b, c):
      peer.close()
  return checks


def check_abi1(tmp, path, fd):
  root = Path(tmp) / 'abi1'
  (root / '.params').mkdir(parents=True)
  (root / '.params/ExtLeanMiciPeer').write_text(json.dumps({'name': 'test phone'}))
  a = Peer()
  (root / 'peers').write_text(json.dumps([['test phone', *a.address]]))
  write_meta(fd, 1)
  p = multiprocessing.get_context('spawn').Process(target=link_child, args=(path, os.getpid(), str(root)))
  p.start()
  try:
    assert wait(lambda: len(a.frames) >= 5, 10), 'no frames under ABI 1'
    mark = len(a.frames)
    write_meta(fd, 1, desire=2, frame=7)  # ABI 1: the host's pulse frame, held in the slot
    assert wait(lambda: len(a.frames) >= mark + 6, 5)
    assert [f[3] for f in a.frames[mark:]].count(2) == 1, f'D5 {a.frames[mark:]}'
  finally:
    p.kill()
    p.join()
    a.close()
  return ['D5_abi1_pulse_once']


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument('--output', type=Path, required=True)
  args = ap.parse_args()
  args.output.mkdir(parents=True, exist_ok=False)
  with tempfile.TemporaryDirectory() as tmp:
    path = str(Path(tmp) / 'mailbox')
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    os.ftruncate(fd, worker.SIZE)
    try:
      checks = check_supervisor(tmp, path) + check_link(tmp, path, fd) + check_abi1(tmp, path, fd)
    finally:
      os.close(fd)
  result = {'pass': True, 'checks': checks, 'scope': 'real sockets, worker, supervisor and mailbox; camera and discovery are fixtures'}
  (args.output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
  print(json.dumps(result))


if __name__ == '__main__':
  main()
