import json
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
    path.write_text(json.dumps({'state': state, 'version': '0.8.2', 'updated_at': time.time()}))
    os.replace(path, directory / 'status.json')
    stop.wait(.5)


def main():
  from .worker import main as child
  directory = data_dir()
  source = PhoneSource(directory)
  navigation = multiprocessing.Queue(maxsize=2)
  process = multiprocessing.Process(target=child, args=(source.path, os.getpid(), navigation), daemon=True)
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
