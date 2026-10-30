"""Parked physical E2E, scoped to Starlink. No credentials or node names in output."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import urllib.error
import urllib.request

from openpilot.common.params import Params
from openpilot.cereal import messaging
from openpilot.tesla import plugins
from xpilot_ext.starlink import client, session

params = Params()
settings = plugins.view(params)
assert params.get_bool('IsOffroad') and not params.get_bool('IsEngaged')
root = client.ROOT
original = hashlib.sha256((root/'config.yaml').read_bytes()).hexdigest()
report = []


def state():
  return client.call('status', timeout=12)


def wait_for(predicate, timeout=60):
  end = time.monotonic() + timeout
  while time.monotonic() < end:
    if predicate():
      return
    time.sleep(.5)
  raise AssertionError('condition timed out')


def ready():
  try:
    s = state()
    return bool(s.get('running') and s.get('gateway'))
  except Exception:
    return False


def pid():
  return int((root/'pid').read_text())


def daemon_pid():
  sm = messaging.SubMaster(['managerState'])
  for _ in range(20):
    sm.update(100)
    if sm.updated['managerState']:
      return next((p.pid for p in sm['managerState'].processes if p.name == 'starlinkd' and p.running), 0)
  return 0


def policy_count():
  return sum(r.get('priority') == 17880 and str(r.get('table')) == '17890'
             for r in json.loads(subprocess.check_output(['ip', '-j', 'rule'], text=True)))


def record(name, **values):
  item = {'case': name, **values}
  report.append(item)
  print(json.dumps(item), flush=True)


def unchanged():
  assert hashlib.sha256((root/'config.yaml').read_bytes()).hexdigest() == original


session.set_requested(True)
settings.put_bool('ExtStarlinkConnect', True, block=True)
settings.put_bool('ExtStarlinkEnabled', True, block=True)
wait_for(ready)
before = pid()
client.call('start')
client.call('start')
assert pid() == before and policy_count() == 1
record('idempotent_start', single_policy=True)

(root/'pid').unlink()
client.call('start')
wait_for(ready)
assert policy_count() == 1
record('lost_pid_repair', restored=True)

from openpilot.tesla.network import NetworkVPN
NetworkVPN('starlink').request('disable')
wait_for(ready)
record('tun_repair', automatic=True)

daemon = daemon_pid()
assert daemon > 1
os.kill(daemon, signal.SIGSTOP)
try:
  core = pid()
  assert Path(f'/proc/{core}').stat().st_uid == 61400
  assert Path(f'/proc/{core}/cmdline').read_bytes().split(b'\0')[0] == b'/data/mihomo/mihomo'
  subprocess.run(['sudo', '-n', '/bin/kill', '-KILL', str(core)], check=True)
  wait_for(lambda: policy_count() == 0, timeout=8)
  record('independent_crash_cleanup', policy_removed=True)
finally:
  os.kill(daemon, signal.SIGCONT)
wait_for(ready)
record('core_recovery', automatic=True)

old_daemon = daemon_pid()
assert old_daemon > 1
os.kill(old_daemon, signal.SIGKILL)
wait_for(lambda: (new := daemon_pid()) > 1 and new != old_daemon)
wait_for(ready)
record('daemon_recovery', automatic=True)

settings.put_bool('ExtStarlinkEnabled', False, block=True)
wait_for(lambda: policy_count() == 0 and not (root/'pid').exists(), timeout=25)
record('plugin_disable_cleanup', policy_removed=True)
settings.put_bool('ExtStarlinkEnabled', True, block=True)
session.set_requested(True)
settings.put_bool('ExtStarlinkConnect', True, block=True)  # manual request after disable
wait_for(ready)

result = client.call('test', timeout=30)
if not result.get('ok'):
  client.call('fastest', timeout=120)
  result = client.call('test', timeout=30)
assert result.get('ok'), 'internet verification failed'
record('real_ipv4_internet', github_http=result.get('http_code'))

pair = client.pair()
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
assert opener.open(pair['url'], timeout=5).status == 200
try:
  opener.open(pair['url'].rsplit('/', 1)[0] + '/wrong-token', timeout=5)
  raise AssertionError('pairing token not checked')
except urllib.error.HTTPError as error:
  assert error.code == 403
request = urllib.request.Request(pair['url'], data=b'{}', headers={'Origin': 'http://invalid.example', 'Content-Type': 'application/json'})
try:
  opener.open(request, timeout=5)
  raise AssertionError('pairing origin not checked')
except urllib.error.HTTPError as error:
  assert error.code == 403
client.stop_pair()
record('pairing_access_controls', valid_get=True, bad_token_rejected=True, bad_origin_rejected=True)
unchanged()
record('private_config_preserved', unchanged=True)
Path('/data/xpilot-install-20261026/starlink-e2e.json').write_text(json.dumps(report, indent=2))
print('STARLINK_PARKED_E2E_PASSED', flush=True)
