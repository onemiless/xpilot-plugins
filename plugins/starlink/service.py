"""Keep the requested VPN alive; ordinary connectivity is restored on failure."""
import json
import os
from pathlib import Path
import signal
import time

from openpilot.common.params import Params
from openpilot.tesla.plugins import view
from openpilot.tesla import plugins
from .plugin import Starlink
from . import client, session


def write_state(data):
  path = client.ROOT / 'starlink-status.json'
  temporary = path.with_suffix('.pending')
  temporary.write_text(json.dumps(data))
  temporary.chmod(0o600)
  os.replace(temporary, path)
  plugins.send_msg(Starlink, 'network.vpn', data, valid_for_ms=6000)


def interrupted(*_):
  raise KeyboardInterrupt


def main():
  params = Params()
  settings = view(params)
  signal.signal(signal.SIGINT, interrupted)
  signal.signal(signal.SIGTERM, interrupted)
  try:
    # Cache cleanup code before a store removal can unlink the package.
    client.script('backend.py')
    client.script('pairing.py')
    client.ensure_core()
    client.call('cleanup')
    lease = {'pid': os.getpid(), 'start': Path(f'/proc/{os.getpid()}/stat').read_text().rsplit(')', 1)[1].split()[19]}
    path = client.ROOT / 'network-lease.json'
    path.write_text(json.dumps(lease))
    path.chmod(0o600)
    initialized, failures = False, 0
    while True:
      try:
        parked = params.get_bool('IsOffroad') and not params.get_bool('IsEngaged')
        if not initialized and parked:
          client.call('preflight')
          initialized = True
        desired = settings.get_bool('ExtStarlinkConnect')
        state = client.call('status', timeout=12)
        ready = bool(state.get('running') and state.get('gateway'))
        if not desired and state.get('running'):
          client.call('cleanup')
          state = client.call('status', timeout=12)
        elif desired and state.get('configured') and not ready:
          if parked:
            client.call('start')
            state = client.call('status', timeout=12)
          else:
            client.call('cleanup')
            state['message'] = 'Reconnect after parking'
        write_state({key: state.get(key) for key in ('running', 'gateway', 'configured', 'nodes', 'message')})
        failures = 0
      except Exception:
        # Never persist API payloads, node credentials or raw worker stderr.
        write_state({'running': False, 'gateway': False, 'message': 'VPN unavailable; retrying'})
        try:
          client.call('cleanup', timeout=20)
        except Exception:
          pass
        failures += 1
      time.sleep(min(30, 3 * 2 ** min(failures, 3)))
  except KeyboardInterrupt:
    pass
  finally:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    # A stopped session must be started deliberately again, including after onroad.
    session.clear()
    settings.put_bool('ExtStarlinkConnect', False, block=True)
    client.stop_pair()
    try:
      client.call('cleanup', timeout=20)
    except Exception:
      pass
    (client.ROOT/'network-lease.json').unlink(missing_ok=True)
