"""Rebuild the pinned Mihomo source for Linux ARM64, not a binary downloader.

python build_native.py --source /path/to/mihomo --go /path/to/go --output /tmp/mihomo
Add --publish to replace bundled resources and write source/build provenance.
Requires the reviewed MetaCubeX Go 1.26 toolchain; build dependencies are not
installed on the driving runtime. SOURCE_DATE_EPOCH fixes the embedded timestamp.
"""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess

COMMIT = '88dcbf7f1614a67c3b36b848ee3592dfa92ada36'
VERSION = 'v1.19.32'
TOOLCHAIN_SHA256 = '2483b14ce111fa89ac671cdb00073c5b19670198be1334f491a8f3fe36affeb0'
PREVIOUS_CORE_SHA256 = 'c80df2ca5159567804da883401e0118908fc1155e05df2baf3dbd4d3ecfc3754'


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--source', type=Path, required=True)
  parser.add_argument('--go', type=Path, required=True)
  parser.add_argument('--output', type=Path, required=True)
  parser.add_argument('--publish', action='store_true')
  args = parser.parse_args()
  source = args.source.resolve()
  head = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
  if head != COMMIT or subprocess.check_output(['git', '-C', str(source), 'status', '--porcelain']):
    raise RuntimeError('Build requires the clean pinned source commit')
  go = str(args.go.resolve())
  toolchain = subprocess.check_output([go, 'version'], text=True).strip()
  if 'go1.26.' not in toolchain:
    raise RuntimeError('Use the reviewed Go 1.26 toolchain')
  timestamp = os.environ.get('SOURCE_DATE_EPOCH', '1792972800')
  if not timestamp.isdigit():
    raise ValueError('SOURCE_DATE_EPOCH must be an integer')
  flags = ('-extldflags --static -X github.com/metacubex/mihomo/constant.Version=' + VERSION
           + ' -X github.com/metacubex/mihomo/constant.BuildTime=' + timestamp + ' -w -s -buildid=')
  env = dict(os.environ, CGO_ENABLED='0', GOOS='linux', GOARCH='arm64', GOTOOLCHAIN='local')
  args.output.parent.mkdir(parents=True, exist_ok=True)
  subprocess.run([go, 'build', '-tags', 'with_gvisor', '-trimpath', '-buildvcs=false', '-ldflags', flags,
                  '-o', str(args.output.resolve()), '.'], cwd=source, env=env, check=True)
  data = args.output.read_bytes()
  if data[:5] != b'\x7fELF\x02' or int.from_bytes(data[18:20], 'little') != 183:
    raise RuntimeError('Expected Linux ARM64 ELF')
  compressed = gzip.compress(data, compresslevel=9, mtime=0)
  metadata = {'version': VERSION, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
              'compressed_sha256': hashlib.sha256(compressed).hexdigest(),
              'source': 'https://github.com/MetaCubeX/mihomo/tree/' + COMMIT,
              'source_commit': COMMIT, 'toolchain': toolchain, 'source_date_epoch': timestamp,
              'build_tags': ['with_gvisor'], 'ldflags': flags,
              'reference_toolchain_archive': 'go1.26.windows-amd64.zip',
              'reference_toolchain_sha256': TOOLCHAIN_SHA256,
              'compatible_existing_sha256': [PREVIOUS_CORE_SHA256],
              'upstream_build': 'https://github.com/MetaCubeX/mihomo/blob/' + COMMIT + '/.github/workflows/build.yml'}
  args.output.with_suffix('.build.json').write_text(json.dumps(metadata, indent=2) + '\n')
  if args.publish:
    root = Path(__file__).resolve().parent
    (root / 'mihomo.gz').write_bytes(compressed)
    (root / 'core.json').write_text(json.dumps(metadata, indent=2) + '\n')
  print(json.dumps({'sha256': metadata['sha256'], 'compressed_size': len(compressed), 'published': args.publish}))


if __name__ == '__main__':
  main()
