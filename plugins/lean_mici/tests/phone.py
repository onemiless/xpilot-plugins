#!/usr/bin/env python3
"""LAN-only auto-start/HELLO probe. ADB is used only for allowlisted identity/logs.
No model preference writes, Android intents, input injection or ADB forwarding.
Full frame/numeric qualification requires Walnut selected through its existing App.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time

p = argparse.ArgumentParser()
p.add_argument('--address', required=True, help='Mac LAN IPv4, never a device address')
p.add_argument('--output', type=Path, required=True)
p.add_argument('--seconds', type=int, default=45)
a = p.parse_args()
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from plugins.lean_mici.network import Beacon, discover
from plugins.lean_mici.wire import receive

a.output.mkdir(parents=True, exist_ok=False)
result = {'pass': False, 'serial': '3B162S00PHS00000', 'frames': 0,
          'latency_ms': None, 'adoption': None, 'numeric_match': None}
wirelog = a.output / 'hello.wire'
wirelog.touch()
command = ['adb', '-s', result['serial'], 'shell']
for name, args in [('os', ['getprop', 'ro.chipmunk.version']), ('model', ['getprop', 'ro.product.model']),
                   ('before', ['dumpsys', 'activity', 'services', 'com.chipmunk.inference'])]:
  (a.output / (name + '.log')).write_bytes(subprocess.check_output(command + args))
try:
  with Beacon(a.address) as beacon:
    deadline = time.monotonic() + a.seconds
    while time.monotonic() < deadline:
      peers = discover('en0' if sys.platform == 'darwin' else 'wlan0')
      if len(peers) == 1:
        host, port = peers[0]
        try:
          with socket.create_connection((host, port), timeout=2) as conn:
            conn.settimeout(2)
            header = receive(conn, 16)
            magic, version, kind, flags, frame, length = struct.unpack('<4sBBHII', header)
            body = receive(conn, length + 16) if length <= 1024 else b''
            wirelog.write_bytes(header + body)
            result.update({'hello': {'magic': magic.decode('ascii'), 'version': version, 'type': kind,
                                     'length': length, 'wire_bytes': len(header + body)},
                           'beacon_requests': beacon.requests})
            result['pass'] = (magic, version, kind, length, len(header + body)) == (b'BGM1', 2, 1, 16, 48)
            if not result['pass']:
              result['blocker'] = 'phone HELLO ABI differs from required BGM1 v2; no FRAME sent'
            break
        except (OSError, ValueError) as error:
          result['last_connection_error'] = repr(error)
      time.sleep(.5)
    else:
      result['blocker'] = 'no unique LAN _bigmodel._tcp server after beacon'
    result['beacon_requests'] = beacon.requests
finally:
  (a.output / 'after.log').write_bytes(subprocess.check_output(command + ['dumpsys', 'activity', 'services', 'com.chipmunk.inference']))
  (a.output / 'frames.csv').write_text('epoch,frameId,EOF,arrival,publish,source,reject_reason\n')
  (a.output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
  (a.output / 'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n'
                                            for p in sorted(a.output.iterdir()) if p.name != 'SHA256SUMS'))
print(json.dumps(result, indent=2))
sys.exit(0 if result['pass'] else 1)
