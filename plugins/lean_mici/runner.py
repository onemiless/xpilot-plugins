import json
import mmap
import multiprocessing
import os
from pathlib import Path
import signal
import threading
import time
from queue import Empty

from openpilot.selfdrive.modeld.modeld import run_with_model_source
from openpilot.common.swaglog import cloudlog
from .source import PhoneSource
from openpilot.tesla.manifest import INSTALL_DIR


def data_dir():
  path = INSTALL_DIR / '.data' / 'lean_mici'
  path.mkdir(parents=True, exist_ok=True, mode=0o700)
  return path


def status(directory, source, stop, navigation):
  from openpilot.tesla import plugins
  from .plugin import LeanMici
  last_report, nav_route = time.monotonic(), False
  while not stop.is_set():
    try:
      timestamp, nav = navigation.get_nowait()
      if time.monotonic() - timestamp < 1:
        nav_route = bool(nav and nav['health'] & 2)
        plugins.send_msg(LeanMici, 'lean_mici.navigation', nav, valid_for_ms=1500)
    except Empty:
      pass
    now = time.monotonic()
    if now - last_report >= 1:
      cloudlog.event('lean_mici.adoption', counts=source.take_counts(), nav_route=nav_route)
      last_report = now
    if not source.sent:
      stop.wait(.05)
      continue
    state = ('Phone active' if source.selected else 'Phone warming' if source.reason in ('warmup','warmup_flags','need_dual_idr') else
             'Local fallback' if source.epoch or source.locked else 'Local ready')
    path = directory / 'status.tmp'
    path.write_text(json.dumps({'state': state, 'version': '0.8.6', 'updated_at': time.time()}))
    os.replace(path, directory / 'status.json')
    stop.wait(.5)


STALE_NS = 5_000_000_000  # worker heartbeat age that counts as hung (e.g. blocked in V4LEncoder::encoder_close)


def supervise(path, parent, navigation, stale_ns=STALE_NS):
  """Restart the worker after a crash or hang, with 1 s .. 30 s backoff. Forked before modeld loads its model, so
  forking the worker from here never copies the model's page tables or GPU fds."""
  from . import worker
  worker.die_with_parent()
  fd = os.open(path, os.O_RDWR)
  shared = mmap.mmap(fd, worker.SIZE)
  def age():
    return worker.now() - int.from_bytes(shared[worker.HEARTBEAT:worker.HEARTBEAT + 8], 'little')
  backoff, me = 1, os.getpid()
  try:
    while os.getppid() == parent:
      shared[worker.HEARTBEAT:worker.HEARTBEAT + 8] = worker.now().to_bytes(8, 'little')
      started = time.monotonic()
      pid = os.fork()
      if pid == 0:
        code = 0
        try:
          worker.main(path, me, navigation)
        except BaseException:
          code = 1
        finally:
          os._exit(code)
      while not (done := os.waitpid(pid, os.WNOHANG))[0]:
        if os.getppid() != parent or age() > stale_ns:
          os.kill(pid, signal.SIGKILL)
          done = os.waitpid(pid, 0)
          break
        time.sleep(.2)
      if os.getppid() != parent:
        break
      if time.monotonic() - started > 60:
        backoff = 1
      cloudlog.warning(f'lean_mici: worker exited (status {done[1]}, heartbeat age {age() / 1e9:.1f} s), restart in {backoff} s')
      time.sleep(backoff)
      backoff = min(backoff * 2, 30)
  finally:
    shared.close()
    os.close(fd)


def main():
  directory = data_dir()
  mailbox = Path("/dev/shm/lean_mici")
  mailbox.mkdir(mode=0o700, exist_ok=True)
  source = PhoneSource(mailbox)
  navigation = multiprocessing.Queue(maxsize=2)
  process = multiprocessing.Process(target=supervise, args=(source.path, os.getpid(), navigation), daemon=True)
  stop = threading.Event()
  reporter = threading.Thread(target=status, args=(directory, source, stop, navigation), daemon=True)
  def terminate(signum, frame):
    raise SystemExit(0)
  old_handler = signal.signal(signal.SIGTERM, terminate)
  try:
    process.start()
    reporter.start()
    run_with_model_source(source)
  finally:
    stop.set()
    reporter.join(2)
    if process.pid is not None:
      process.terminate()
      process.join(2)
      if process.is_alive():
        process.kill()
        process.join()
    source.close()
    (directory / 'status.json').unlink(missing_ok=True)
    signal.signal(signal.SIGTERM, old_handler)


if __name__ == '__main__':
  main()
