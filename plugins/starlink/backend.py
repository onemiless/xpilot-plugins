"""Manage the C3 touchscreen proxy and its dedicated global TUN gateway."""
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import yaml
from concurrent.futures import ThreadPoolExecutor

ROOT = Path('/data/mihomo')
CORE = ROOT / 'mihomo'
if not __package__:
  __package__ = 'starlink'
CONFIG = ROOT / 'config.yaml'
def gateway(action):
  from openpilot.tesla.network import NetworkVPN
  return NetworkVPN('starlink').request(action)


def parked():
  assert Path('/data/params/d/IsOffroad').read_text().strip() == '1', 'Park before changing the VPN'
  assert not Path('/data/params/d/IsEngaged').exists() or Path('/data/params/d/IsEngaged').read_text().strip() != '1', 'Disengage before changing the VPN'



def normalize(raw):
  from .formats import normalize as parse
  return parse(raw, (ROOT / "secret").read_text().strip())

def pid():
  try:
    value = gateway('status').get('pid')
    if type(value) is int and value > 0:
      return value
  except (OSError, ValueError, RuntimeError): pass
  return None

def api(path, payload=None, timeout=4):
  headers = {'Authorization': 'Bearer ' + (ROOT / 'secret').read_text().strip()}
  data = None if payload is None else json.dumps(payload).encode()
  if data: headers['Content-Type'] = 'application/json'
  req = urllib.request.Request('http://127.0.0.1:17891' + path, data=data, headers=headers, method='GET' if payload is None else 'PUT')
  if payload is not None:
    parked()
  with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=timeout) as response:
    content = response.read(2_000_001); assert len(content) <= 2_000_000; return json.loads(content) if content else {}

def stop():
  (ROOT / 'enabled').unlink(missing_ok=True)
  # Stop every managed core before deleting its TUN, even if the PID file was lost.
  try: gateway('stopcore')
  finally: gateway('disable')

def tun_ready():
  try:
    if Path('/sys/class/net/c3mh0/carrier').read_text().strip() != '1': return False
    routes = subprocess.run(['ip', 'route', 'show', 'table', '17890'], capture_output=True, text=True, timeout=2).stdout
    rules = json.loads(subprocess.run(['ip', '-j', 'rule'], capture_output=True, text=True, timeout=2).stdout)
    return 'default dev c3mh0' in routes and any(r.get('priority') == 17880 and str(r.get('table')) == '17890' for r in rules)
  except (OSError, ValueError, subprocess.TimeoutExpired): return False

def start():
  parked()
  assert CONFIG.exists(), 'Import a subscription or node first'
  d = yaml.safe_load(CONFIG.read_text())
  assert isinstance(d, dict) and d.get('external-controller') == '127.0.0.1:17891' and d.get('mixed-port') == 17890, 'Reimport config to use managed loopback settings'
  assert not d.get('allow-lan') and d.get('bind-address') == '127.0.0.1' and not d.get('listeners') and not d.get('iptables'), 'Reimport config to use managed loopback settings'
  assert not any(d.get(k) for k in ('port', 'socks-port', 'redir-port', 'tproxy-port', 'external-controller-unix', 'external-controller-pipe', 'external-controller-tls')), 'Reimport config to use managed loopback settings'
  assert d.get('tun', {}).get('enable') and not d['tun'].get('auto-route') and not d['tun'].get('auto-redirect') and d['tun'].get('device') == 'c3mh0', 'Reimport config to use managed TUN settings'
  use_tun = True
  if pid():
    try:
      api('/version')
      if not use_tun or tun_ready():
        (ROOT / 'enabled').write_text('1')
        return
    except Exception: pass
  # Clear partial launches and stale routes before preparing a new interface.
  stop()
  try:
    if use_tun: gateway('prepare')
    gateway('startcore')
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
      try:
        if pid() and api('/version', timeout=1).get('version'): break
      except Exception: pass
      time.sleep(.1)
    else: raise RuntimeError('Core is not ready; check its private log')
    api('/proxies/GLOBAL', {'name': 'C3-PROXY'})
    if (ROOT/'selected_node').exists():
      selected = (ROOT/'selected_node').read_text()
      if selected in api('/proxies/C3-PROXY').get('all', []): api('/proxies/C3-PROXY', {'name': selected})
    if use_tun:
      deadline = time.monotonic() + 5
      while time.monotonic() < deadline:
        try:
          if Path('/sys/class/net/c3mh0/carrier').read_text().strip() == '1': break
        except OSError: pass
        time.sleep(.1)
      else: raise RuntimeError('TUN is not ready; routes were not enabled')
      gateway('enable')
      if not tun_ready(): raise RuntimeError('VPN routes are not ready; retry')
    (ROOT / 'enabled').write_text('1')
  except Exception:
    stop()
    raise

def status():
  d = yaml.safe_load(CONFIG.read_text()) if CONFIG.exists() else {}
  result = {'running': bool(pid()), 'configured': bool(d), 'gateway': bool(pid()) and tun_ready(), 'nodes': len(d.get('proxies', [])), 'groups': [], 'message': 'No subscription or node configured' if not d else 'Configuration saved'}
  if result['running']:
    result['version'] = api('/version').get('version')
    result['groups'] = [{'name': name, 'now': p.get('now', ''), 'all': p.get('all', [])} for name, p in api('/proxies').get('proxies', {}).items() if p.get('type') == 'Selector' and name != 'GLOBAL']
  return result

def node_list():
  proxies = api('/proxies').get('proxies', {})
  group = proxies.get('C3-PROXY', {})
  try: cached = json.loads((ROOT/'latency.json').read_text()).get('results', {})
  except (OSError, ValueError): cached = {}
  names = group.get('all', [])
  rows = [{'name': n, 'delay': cached.get(n), 'tested': n in cached, 'selected': n == group.get('now')} for n in names if proxies.get(n, {}).get('type', '').lower() not in ('direct', 'reject', 'rejectdrop')]
  rows.sort(key=lambda r: (r['delay'] is None, r['delay'] or 0, r['name']))
  return {'nodes': rows, 'selected': group.get('now'), 'running': bool(pid())}

def measure_nodes(select_best=False):
  names = [r['name'] for r in node_list()['nodes']][:128]
  assert names, 'Start the VPN and load nodes before testing'
  def measure(name):
    query = urllib.parse.urlencode({'url': 'https://www.gstatic.com/generate_204', 'timeout': 4000})
    try:
      delay = api('/proxies/'+urllib.parse.quote(name, safe='')+'/delay?'+query, timeout=6).get('delay')
      return name, delay if isinstance(delay, (int,float)) and delay > 0 else None
    except Exception: return name, None
  with ThreadPoolExecutor(max_workers=8) as pool: results = dict(pool.map(measure, names))
  (ROOT/'latency.json').write_text(json.dumps({'time': time.time(), 'results': results}))
  good = [(delay, n) for n, delay in results.items() if delay is not None]
  if select_best:
    assert good, 'All nodes failed; previous selection kept'
    select_node(min(good)[1])
  return node_list()

def select_node(name):
  parked()
  names = api('/proxies').get('proxies', {}).get('C3-PROXY', {}).get('all', [])
  assert name in names, 'Node list changed; refresh it'
  parked()
  api('/proxies/C3-PROXY', {'name': name})
  (ROOT/'selected_node').write_text(name)
  (ROOT/'selected_node').chmod(0o600)
  api('/proxies/GLOBAL', {'name': 'C3-PROXY'})
  return node_list()

def main(command):
  os.umask(0o077)
  ROOT.mkdir(parents=True, exist_ok=True)
  if command == 'labels':
    d = yaml.safe_load(CONFIG.read_text()) if CONFIG.exists() else {}
    return {'labels': [str(p.get('name', ''))[:128] for p in d.get('proxies', [])][:128]}
  if command == 'status': return status()
  if command != 'cleanup': parked()
  with (ROOT / 'operation.lock').open('a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    if command != 'cleanup':
      parked()
    if command == 'preflight':
      assert CORE.is_file() and os.access(CORE, os.X_OK), 'Existing mihomo core is required at /data/mihomo/mihomo'
      if not (ROOT/'secret').exists():
        import secrets
        (ROOT/'secret').write_text(secrets.token_urlsafe(32))
        (ROOT/'secret').chmod(0o600)
      backup = ROOT/'config.before-starlink.yaml'
      if CONFIG.exists() and not backup.exists():
        backup.write_bytes(CONFIG.read_bytes())
        backup.chmod(0o600)
    elif command == 'import':
      value = sys.stdin.read(2_000_001).strip(); assert len(value) <= 2_000_000, 'Configuration is too large'
      if value.startswith(('clash://', 'clash-meta://')):
        value = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(value).query)).get('url', '')
      if value.startswith(('https://', 'http://')):
        try:
          req = urllib.request.Request(value, headers={'User-Agent': 'Clash.Meta'})
          with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=25) as response: raw = response.read(2_000_001).decode('utf-8-sig')
          assert len(raw) <= 2_000_000
        except Exception: raise ValueError('Subscription download failed; check the link and network') from None
      else: raw = value
      d = normalize(raw); candidate = ROOT / 'candidate.yaml'
      candidate.write_text(yaml.safe_dump(d, allow_unicode=True, sort_keys=False)); candidate.chmod(0o600)
      check = subprocess.run([str(CORE), '-t', '-d', str(ROOT), '-f', str(candidate)], capture_output=True, timeout=35)
      if check.returncode: raise ValueError('Configuration validation failed; import compatible Clash YAML')
      old = CONFIG.read_bytes() if CONFIG.exists() else None; running = bool(pid())
      parked()
      if running: stop()
      parked()
      os.replace(candidate, CONFIG)
      try:
        if running: start()
      except Exception:
        if old is not None: CONFIG.write_bytes(old)
        else: CONFIG.unlink(missing_ok=True)
        if running and old is not None: start()
        raise
    elif command == 'start': start()
    elif command in ('stop', 'cleanup'): stop()
    elif command == 'restart':
      stop(); start()
    elif command == 'nodes': return node_list()
    elif command == 'measure': return measure_nodes()
    elif command == 'fastest': return measure_nodes(select_best=True)
    elif command == 'select': return select_node(sys.stdin.read(4096).strip())
    elif command == 'next':
      groups = status()['groups']; assert groups, 'Start the VPN and load nodes first'
      g = groups[0]; choices = g['all']; assert choices, 'No nodes available'
      index = choices.index(g['now']) if g['now'] in choices else -1
      api('/proxies/' + urllib.parse.quote(g['name'], safe=''), {'name': choices[(index + 1) % len(choices)]})
    elif command == 'test':
      assert pid(), 'Start the VPN first'
      use_tun = yaml.safe_load(CONFIG.read_text()).get('tun', {}).get('enable', False)
      if use_tun: assert tun_ready(), 'TUN is not ready; restart the VPN'
      transport = ['--noproxy', '*', '-4'] if use_tun else ['--proxy', 'http://127.0.0.1:17890', '--noproxy', '']
      # Public REST API quotas are shared by every user of a node. A quota 403
      # says nothing about proxy connectivity, so test the normal website.
      result = subprocess.run(['curl', '-fsS', *transport, '--connect-timeout', '8', '--max-time', '20', '-o', '/dev/null', '-w', '%{http_code}', 'https://github.com/'], capture_output=True, text=True)
      code = result.stdout.strip()
      if result.returncode == 0:
        message = 'GitHub reached through the VPN' if use_tun else 'GitHub reached through the proxy'
      elif code.isdigit() and int(code) >= 400:
        message = f'GitHub returned HTTP {code}; try another node'
      else:
        errors = {6: 'DNS lookup failed', 7: 'Proxy connection failed', 28: 'Node connection timed out', 35: 'TLS connection failed', 60: 'Certificate validation failed'}
        message = errors.get(result.returncode, 'Connection failed') + '; check the node and network'
      return {'ok': result.returncode == 0, 'message': message, 'http_code': code}
    else: raise ValueError('Unknown operation')
  return status()

if __name__ == '__main__':
  try: print(json.dumps(main(sys.argv[1]), ensure_ascii=False))
  except Exception as error:
    message = str(error) if isinstance(error, (AssertionError, ValueError, RuntimeError)) else 'Operation failed; check network and configuration'
    print(json.dumps({'error': message}, ensure_ascii=False)); sys.exit(1)
