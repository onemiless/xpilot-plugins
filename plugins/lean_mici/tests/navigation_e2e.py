#!/usr/bin/env python3
"""Wire fixtures derived from lean-release cc5d145; no device or control writes."""
import json
import math
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from plugins.lean_mici import wire

meta = (b'XMS1', 1, 1, 23, 23, 1_000_000_000, 1_000_000_000, *([0.] * 30))
vehicle = wire.vehicle((0, 0, 0), (12.34, 0, 0), (0, 0, math.pi / 180), True, True, True, True, True,
                       950_000_000, meta[5])
head, _ = wire.frame(meta, b'road', b'wide', 3, 23, vehicle)
assert struct.unpack_from('<HhB3x', head, 144) == (1234, 100, 9)
assert wire.vehicle((0, 0, 0), (-.5, 0, 0), (0, 0, 0), True, True, True, True, True, 950_000_000, meta[5]) == (50, 0, 11)
assert wire.vehicle((0, 0, 0), (0, 0, 0), (0, 0, 0), True, True, True, True, True, 899_999_999, meta[5]) == (0, -32768, 0)
assert wire.vehicle((0, 0, 0), (0, 0, 0), (0, 0, 0), True, True, False, True, True, 950_000_000, meta[5]) == (0, 0, 1)
assert wire.vehicle((0, 0, 0), (math.nan, 0, 0), (0, 0, math.nan), True, True, True, True, True, 950_000_000, meta[5]) == (0, -32768, 0)

reply = bytearray(struct.pack('<4sBBHIIQ', b'BGM1', 4, 0x11, 0, 23, 8440, meta[5]) + struct.pack('<2066f', *([1.] * 2066)) + bytes(192))
reply[8304:8306] = bytes((5, 3))
struct.pack_into('<BBBBIIIHBBH', reply, 8308, 2, 9, 10, 0, 120, 1000, 100, 50, 3, 0, 9000)
struct.pack_into('<BBHHBB', reply, 8348, 1, 20, 600, 40, 1, 0)
struct.pack_into('<iiHBB', reply, 8356, 250000000, 1210000000, 120, 36, 8)
nav = wire.nav_ext(reply)
assert nav['health'] == 3 and nav['state'][1] == 9 and nav['state'][4] == 120
assert nav['curve'][0] == 1 and nav['points'][0] == (250000000, 1210000000, 120, 36, 8)
bad = reply.copy(); bad[8348] = 9
assert wire.nav_ext(bad)['curve'] is None
assert wire.nav_ext(reply[:-1]) is None
bad = reply.copy(); bad[4] = 2
assert wire.nav_ext(bad) is None
empty = reply.copy(); empty[8304:8464] = bytes(160)
assert wire.nav_ext(empty) is None
assert len(wire.to_mailbox(reply, 23)) == 8320
Path(sys.argv[1]).write_text(json.dumps({'frame_tail': 'pass', 'nav_ext_valid_invalid_absent': 'pass', 'mailbox': 'pass'}) + '\n')
