#!/usr/bin/env python3
"""Native ABI/warp end-to-end: ctypes loads a mode-0644 library; no exec bit.
Failure modes: padded source stride/UV offset, non-square geometry, wrong matrix,
caller-owned buffer reuse, missing/oversized camera source, incomplete output.
"""
import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import numpy as np

p = argparse.ArgumentParser()
p.add_argument('--library', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
a.output.mkdir(parents=True, exist_ok=False)
result = {'pass': False, 'scope': 'native warp and ABI only; no VisionIPC/V4L2 execution'}
try:
  a.library.chmod(0o644)
  lib = ctypes.CDLL(str(a.library.resolve()))
  assert lib.lean_abi() == 1
  lib.lean_warp.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                            ctypes.c_void_p, ctypes.c_void_p]
  width, height, stride, uv_offset = 512, 256, 640, 640 * 288
  image = np.arange(uv_offset + stride * height // 2, dtype=np.uint8)
  output = np.zeros(512 * 256 * 3 // 2, dtype=np.uint8)
  matrix = np.eye(3, dtype=np.float32)
  assert lib.lean_warp(image.ctypes.data, width, height, stride, uv_offset, matrix.ctypes.data, output.ctypes.data) == 1
  expected = np.concatenate([image[:stride * height].reshape(height, stride)[:, :width].ravel(),
                             image[uv_offset:].reshape(height // 2, stride)[:, :width].ravel()])
  np.testing.assert_array_equal(output, expected)
  for w, h, s, off in [(0, height, stride, uv_offset), (511, height, stride, uv_offset),
                        (width, height, 100, uv_offset), (width, height, stride, 1)]:
    assert lib.lean_warp(image.ctypes.data, w, h, s, off, matrix.ctypes.data, output.ctypes.data) == 0
  (a.output / 'input.nv12').write_bytes(image.tobytes())
  (a.output / 'output.nv12').write_bytes(output.tobytes())
  result.update({'pass': True, 'stride': stride, 'uv_offset': uv_offset, 'exact_match': True,
                 'library_sha256': hashlib.sha256(a.library.read_bytes()).hexdigest()})
finally:
  (a.output / 'frames.csv').write_text('frameId,source\n1,native_warp\n')
  (a.output / 'wire.log').write_text('No TCP: native geometry/ctypes check only.\n')
  (a.output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
  (a.output / 'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n'
                                            for p in sorted(a.output.iterdir()) if p.name != 'SHA256SUMS'))
