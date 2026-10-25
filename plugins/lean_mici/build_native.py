#!/usr/bin/env python3
"""Build privately against the matching system checkout. No model assets are copied.
--host builds only the ctypes/warp seam for Mac E2E; it cannot pass prepare_install.
Full AGNOS build requires its existing QCOM headers and PIC system libraries.
"""
import argparse
import os
from pathlib import Path
import shlex
import subprocess
import sys

p = argparse.ArgumentParser()
p.add_argument('--system', type=Path)
p.add_argument('--host', action='store_true')
p.add_argument('--output', type=Path)
a = p.parse_args()
root = Path(__file__).resolve().parent
out = a.output or root / 'native/liblean_mici.so'
command = [os.environ.get('CXX', 'c++'), '-std=c++17', '-O3', '-fPIC', '-shared',
           str(root / 'native/warp_pack.cpp'), str(root / 'native/bridge.cpp')]
if not a.host:
  if a.system is None or sys.platform != 'linux':
    p.error('full build requires Linux/AGNOS and --system matching S1/S2')
  system = a.system.resolve()
  command += ['-DLEAN_CAMERA', '-DQCOM', str(root / 'native/camera.cpp'), '-I' + str(system),
              '-I' + str(system / 'openpilot'), '-I' + str(system / 'opendbc_repo'), '-I' + str(system / 'msgq_repo'), '-I' + str(system / 'openpilot/cereal/gen/cpp'),
              '-L' + str(system / 'openpilot/system/loggerd'), '-L' + str(system / 'openpilot/common'),
              '-L' + str(system / 'openpilot/cereal/messaging'), '-L' + str(system / 'msgq_repo/msgq'),
              '-Wl,-Bsymbolic', '-Wl,--no-undefined', '-llogger', '-lcommon', '-lmessaging', '-lvisionipc',
              '-lmsgq', '-lcapnp', '-lkj', '-lzmq', '-ljson11', '-lssl', '-lcrypto', '-lzstd', '-pthread']
command += ['-o', str(out)]
print(shlex.join(command))
subprocess.run(command, check=True)
out.chmod(0o644)
