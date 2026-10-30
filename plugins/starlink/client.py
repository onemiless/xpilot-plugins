"""Public worker code stays open so uninstall can still clean up routing."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path('/data/mihomo')
PACKAGE = Path(__file__).resolve().parent
_fds = {}


def script(name):
  if name not in _fds:
    _fds[name] = os.open(PACKAGE / name, os.O_RDONLY)
  return f'/proc/{os.getpid()}/fd/{_fds[name]}'


def call(operation, value=None, timeout=120):
  if operation not in {'status', 'labels', 'preflight', 'start', 'stop', 'cleanup', 'restart', 'import', 'nodes', 'select', 'measure', 'fastest', 'test'}:
    raise ValueError('Unknown operation')
  env = {**os.environ, 'PYTHONPATH': str(PACKAGE.parent) + os.pathsep + os.environ.get('PYTHONPATH', '')}
  process = subprocess.Popen(['/usr/bin/python3', script('backend.py'), operation], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
  try:
    stdout, _ = process.communicate(value, timeout=timeout)
  except BaseException:
    process.kill()
    process.wait()
    raise
  try:
    result = json.loads(stdout.splitlines()[-1])
  except (ValueError, IndexError):
    raise RuntimeError('Starlink worker failed; check system Python and permissions') from None
  if result.get('error'):
    raise RuntimeError(result['error'])
  return result


def ensure_core():
  ROOT.mkdir(parents=True, exist_ok=True)
  target = ROOT / 'mihomo'
  metadata = json.loads((PACKAGE / 'core.json').read_text())
  if target.exists():
    with target.open('rb') as existing:
      checksum = hashlib.file_digest(existing, 'sha256').hexdigest()
    if checksum not in [metadata['sha256'], *metadata.get('compatible_existing_sha256', [])]:
      raise RuntimeError('Existing VPN core is not an approved build; preserved unchanged')
    version = subprocess.run([str(target), '-v'], capture_output=True, text=True, timeout=5)
    if version.returncode or metadata['version'] not in version.stdout:
      raise RuntimeError('Existing VPN core version is incompatible; preserved unchanged')
    return
  temporary = ROOT / 'mihomo.starlink-pending'
  digest = hashlib.sha256()
  total = 0
  try:
    with gzip.open(PACKAGE / 'mihomo.gz', 'rb') as source, temporary.open('wb') as dest:
      while data := source.read(1024 * 1024):
        total += len(data)
        if total > metadata['size']:
          raise ValueError('Core image exceeds expected size')
        digest.update(data)
        dest.write(data)
      dest.flush()
      os.fsync(dest.fileno())
    if total != metadata['size'] or digest.hexdigest() != metadata['sha256']:
      raise ValueError('Core image checksum mismatch')
    temporary.chmod(0o755)
    os.replace(temporary, target)
  finally:
    temporary.unlink(missing_ok=True)


def pair():
  script('backend.py')
  env = {**os.environ, 'STARLINK_BACKEND_FD': str(_fds['backend.py']),
         'PYTHONPATH': str(PACKAGE.parent) + os.pathsep + os.environ.get('PYTHONPATH', '')}
  result = subprocess.run(['/usr/bin/python3', script('pairing.py'), 'create'], env=env,
                          pass_fds=tuple(_fds.values()), capture_output=True, text=True, timeout=10)
  try:
    data = json.loads(result.stdout.splitlines()[-1])
  except (ValueError, IndexError):
    raise RuntimeError('Phone pairing could not start') from None
  if data.get('error'):
    raise RuntimeError(data['error'])
  return data


def stop_pair():
  try:
    state = json.loads((ROOT/'pair.json').read_text())
    pid = state['pid']
    if Path(f'/proc/{pid}').stat().st_uid == os.getuid() and b'starlink-pair' in Path(f'/proc/{pid}/cmdline').read_bytes():
      os.kill(pid, 15)
  except (OSError, ValueError, KeyError):
    pass
