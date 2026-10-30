"""Manual request marker must be transient, user-owned, and never follow links."""
import importlib.util
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('test_starlink_session', ROOT / 'session.py')
session = importlib.util.module_from_spec(spec)
spec.loader.exec_module(session)


def test_marker_lifecycle():
  with tempfile.TemporaryDirectory() as directory:
    session.MARKER = Path(directory) / 'request'
    assert not session.requested()
    session.set_requested(True)
    assert session.requested()
    assert session.MARKER.stat().st_mode & 0o777 == 0o600
    session.clear()
    assert not session.requested()
    target = Path(directory) / 'other'
    target.write_text('unchanged')
    session.MARKER.symlink_to(target)
    assert not session.requested()
    try:
      session.set_requested(True)
    except PermissionError:
      pass
    else:
      raise AssertionError('unsafe marker accepted')
    session.clear()
    assert target.read_text() == 'unchanged'


if __name__ == '__main__':
  test_marker_lifecycle()
  print('transient marker ownership and symlink checks passed')
