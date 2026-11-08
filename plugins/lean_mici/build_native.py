#!/usr/bin/env python3
"""Run from the matching built xpilot checkout on the parked comma device."""
import json
from pathlib import Path
import platform
import subprocess
import sys
import capnproto
import json11
import zeromq
import zstd

here = Path(__file__).resolve().parent
subprocess.run([
  'clang++', '-shared', '-fPIC', '-O2', '-std=c++17', '-D__COMMA_HARDWARE__',
  *(f'-I{p.INCLUDE_DIR}' for p in (capnproto, json11, zeromq, zstd)),
  *(f'-L{p.LIB_DIR}' for p in (capnproto, json11, zeromq, zstd)),
  *(f'-Wl,-rpath,{p.LIB_DIR}' for p in (capnproto, json11, zeromq, zstd)),
  '-I.', '-Iopenpilot', '-Imsgq_repo', '-Iopenpilot/cereal/gen/cpp', '-Ithird_party/linux/include',
  str(here / 'camera.cc'), str(here / 'warp_pack.cpp'),
  'openpilot/system/loggerd/encoder/v4l_encoder.cc', 'openpilot/system/loggerd/encoder/encoder.cc',
  '-Wl,--start-group', 'openpilot/common/libcommon.a', 'msgq_repo/libvisionipc.a', 'msgq_repo/libmsgq.a',
  'openpilot/cereal/libcereal.a', 'openpilot/cereal/libsocketmaster.a', '-Wl,--end-group',
  '-lcapnp', '-lkj', '-lzmq', '-lpthread', '-ljson11', '-lzstd', '-o', str(here / 'libcamera.so'),
], check=True)
manifest = here / 'plugin.json'
m = json.loads(manifest.read_text())
m['platform'] = {'machine': platform.machine(), 'python': sys.implementation.cache_tag}
manifest.write_text(json.dumps(m, indent=2) + '\n')
