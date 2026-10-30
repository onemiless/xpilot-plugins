"""Short-lived, token-protected private-LAN import form. Never logs request URLs."""
import hmac
import http.server
import ipaddress
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import time

ROOT = Path('/data/mihomo')
PORT = 17894
PAGE = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Starlink</title><style>body{max-width:650px;margin:30px auto;padding:20px;background:#141a24;color:white;font:18px sans-serif}
textarea{box-sizing:border-box;width:100%;height:210px}button{padding:16px;width:100%;margin:20px 0}</style>
<h1>Starlink · 添加上网配置</h1><p>手机与设备连接同一私有网络，粘贴订阅/节点链接或上传 Clash YAML。页面十分钟内有效，成功导入后失效。</p>
<textarea id="value" autocomplete="off" spellcheck="false"></textarea>
<input id="file" type="file" accept=".yaml,.yml,.txt"><button id="save">校验并保存</button><p id="msg"></p>
<script>const v=document.getElementById('value'),f=document.getElementById('file'),b=document.getElementById('save'),m=document.getElementById('msg');
f.onchange=async()=>{if(f.files[0])v.value=await f.files[0].text()};
b.onclick=async()=>{b.disabled=true;m.textContent='正在校验…';try{const r=await fetch(location.pathname,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({value:v.value})});const d=await r.json();m.textContent=d.ok?'已保存，请返回设备启动 Starlink。':(d.error||'保存失败');if(d.ok)v.value=''}catch(e){m.textContent='连接失败，请重新生成二维码'}finally{b.disabled=false}};</script></html>"""


def parked():
  return (ROOT.parent/'params/d/IsOffroad').read_text().strip() == '1' and (
    not (ROOT.parent/'params/d/IsEngaged').exists() or (ROOT.parent/'params/d/IsEngaged').read_text().strip() != '1')


def stop_old():
  try:
    state = json.loads((ROOT/'pair.json').read_text())
    pid = state['pid']
    if Path(f'/proc/{pid}').stat().st_uid == os.getuid() and b'starlink-pair' in Path(f'/proc/{pid}/cmdline').read_bytes():
      os.kill(pid, signal.SIGTERM)
      for _ in range(20):
        if not Path(f'/proc/{pid}').exists():
          break
        time.sleep(.05)
  except (OSError, KeyError, ValueError):
    pass


def create():
  assert parked(), 'Park before changing the VPN'
  addresses = json.loads(subprocess.check_output(['ip', '-j', '-4', 'addr', 'show', 'wlan0'], text=True))
  candidates = [a['local'] for d in addresses for a in d.get('addr_info', [])
                if a.get('scope') == 'global' and any(ipaddress.ip_address(a['local']) in n for n in
                (ipaddress.ip_network('10.0.0.0/8'), ipaddress.ip_network('172.16.0.0/12'), ipaddress.ip_network('192.168.0.0/16')))]
  assert candidates, 'Connect to a private Wi-Fi network first'
  ROOT.mkdir(parents=True, exist_ok=True)
  stop_old()
  state = {'bind': candidates[0], 'token': secrets.token_urlsafe(32), 'expires': time.time() + 600}
  path = ROOT/'pair.json'
  path.write_text(json.dumps(state))
  path.chmod(0o600)
  fds = (int(os.environ['STARLINK_BACKEND_FD']),)
  own = os.open(__file__, os.O_RDONLY)
  try:
    with (ROOT/'pair.log').open('ab') as log:
      proc = subprocess.Popen(['/usr/bin/python3', f'/proc/self/fd/{own}', 'serve', 'starlink-pair'],
                              pass_fds=(*fds, own), env=os.environ, stdout=log, stderr=log, start_new_session=True)
    state['pid'] = proc.pid
    path.write_text(json.dumps(state))
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
      if proc.poll() is not None:
        raise RuntimeError('Phone pairing could not start')
      if (ROOT/'pair.ready').exists():
        break
      time.sleep(.05)
    else:
      raise RuntimeError('Phone pairing could not start')
  finally:
    os.close(own)
  return {'url': f"http://{state['bind']}:{PORT}/p/{state['token']}", 'expires': state['expires']}


def serve():
  state = json.loads((ROOT/'pair.json').read_text())
  origin = f"http://{state['bind']}:{PORT}"
  done = False

  class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_):
      pass

    def allowed(self):
      return parked() and time.time() < state['expires'] and self.headers.get('Host') == f"{state['bind']}:{PORT}" and hmac.compare_digest(self.path, '/p/' + state['token'])

    def send(self, code, body, kind='application/json; charset=utf-8'):
      data = body.encode()
      self.send_response(code)
      for key, value in {'Content-Type': kind, 'Content-Length': str(len(data)), 'Cache-Control': 'no-store',
                         'Referrer-Policy': 'no-referrer', 'X-Content-Type-Options': 'nosniff'}.items():
        self.send_header(key, value)
      self.end_headers()
      self.wfile.write(data)

    def do_GET(self):
      if not self.allowed():
        return self.send(403, '{"error":"Pairing expired"}')
      self.send(200, PAGE, 'text/html; charset=utf-8')

    def do_POST(self):
      nonlocal done
      if not self.allowed() or self.headers.get('Origin') not in (None, origin):
        return self.send(403, '{}')
      try:
        length = int(self.headers.get('Content-Length', 0))
        assert 0 < length <= 2_000_000
        value = json.loads(self.rfile.read(length))['value']
        assert isinstance(value, str)
        backend = f"/proc/{os.getpid()}/fd/{os.environ['STARLINK_BACKEND_FD']}"
        result = subprocess.run(['/usr/bin/python3', backend, 'import'], input=value, capture_output=True,
                                text=True, timeout=100, env=os.environ)
        response = json.loads(result.stdout.splitlines()[-1])
        if result.returncode:
          return self.send(400, '{"error":"Import failed; check the configuration"}')
        self.send(200, '{"ok":true}')
        done = True
      except Exception:
        self.send(400, '{"error":"Import failed; check the configuration"}')

  class Server(http.server.HTTPServer):
    def get_request(self):
      connection, address = super().get_request()
      connection.settimeout(5)
      return connection, address

  server = Server((state['bind'], PORT), Handler)
  server.timeout = 1
  (ROOT/'pair.ready').write_text('1')
  try:
    while not done and time.time() < state['expires'] and parked():
      server.handle_request()
  finally:
    server.server_close()
    (ROOT/'pair.ready').unlink(missing_ok=True)


if __name__ == '__main__':
  os.umask(0o077)
  try:
    if sys.argv[1] == 'serve':
      serve()
    else:
      (ROOT/'pair.ready').unlink(missing_ok=True)
      print(json.dumps(create()))
  except Exception:
    print(json.dumps({'error': 'Phone pairing could not start'}))
    sys.exit(1)
