#!/usr/bin/env python3
"""Exercise wire-to-target with a fake broker and save repeatable results."""
import json
import math
from pathlib import Path
import struct
import sys
import types

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
openpilot = types.ModuleType('openpilot'); openpilot.__path__ = []
tesla = types.ModuleType('openpilot.tesla'); tesla.__path__ = []
base = types.ModuleType('openpilot.tesla.plugin'); base.Plugin = type('Plugin', (), {})
class Broker:
  nav = None
  def latest(self, kind, source):
    assert (kind, source) == ('lean_mici.navigation', 'lean_mici')
    return self.nav
tesla.plugins = types.SimpleNamespace(PluginMsgs=Broker)
sys.modules.update({'openpilot': openpilot, 'openpilot.tesla': tesla, 'openpilot.tesla.plugin': base})
from plugins.lean_mici import plugin, wire

reply = bytearray(struct.pack('<4sBBHIIQ', b'BGM1', 4, 0x11, 0, 1, 8440, 10_000_000_000) + bytes(8456))
reply[8304:8306] = bytes((5, 3))
struct.pack_into('<BBBBIIIHBBH', reply, 8308, 2, 9, 10, 0, 120, 1000, 100, 100, 3, 0, 9000)
struct.pack_into('<BBHHBB', reply, 8348, 1, 20, 600, 200, 1, 0)
struct.pack_into('<iiHBB', reply, 8356, 250000000, 1210000000, 100, 36, 8)
nav = wire.nav_ext(reply)
assert nav['sample_time_ns'] == 9_800_000_000 and nav['valid_until_ns'] == 11_800_000_000
plugin.time.clock_gettime_ns = lambda clock: 10_800_000_000
target = plugin.LeanMici.long_target(None, None)
target.msgs.nav = nav
v = target.update(None, 20, 30)
assert math.isclose(v, math.sqrt(100 + 2 * 80))
assert target.update(None, 20, 10) is None
for health in (0, 1, 2, 11):
  target.msgs.nav = dict(nav, health=health)
  assert target.update(None, 20, 30) is None
target.msgs.nav = dict(nav, valid_until_ns=10_800_000_000)
assert target.update(None, 20, 30) is None
for bad in (None, {}, dict(nav, target_points=[{'distance_m': float('nan'), 'velocity': 10}]), dict(nav, target_points=[])):
  target.msgs.nav = bad
  assert target.update(None, 20, 30) is None
Path(sys.argv[1]).write_text(json.dumps({'wire_ages': 'pass', 'curve_tightens': 'pass', 'distance_compensation': 'pass', 'health': 'pass', 'expiry': 'pass', 'cruise_cap': 'pass', 'bad_data': 'pass'}, indent=2) + '\n')
print('curve_speed_e2e: PASS')
