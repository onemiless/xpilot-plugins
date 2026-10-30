"""Transient, user-owned request to run the VPN for this boot only."""
import os
from pathlib import Path
import stat

MARKER = Path('/tmp') / f'xpilot-starlink-manual-{os.getuid()}.request'


def requested():
  try:
    st = MARKER.lstat()
    return stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid() and stat.S_IMODE(st.st_mode) == 0o600 and st.st_nlink == 1
  except FileNotFoundError:
    return False


def clear():
  if requested():
    MARKER.unlink(missing_ok=True)


def set_requested(value):
  if not value:
    clear()
    return
  if requested():
    return
  try:
    fd = os.open(MARKER, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
  except FileExistsError:
    raise PermissionError('Unsafe VPN request marker') from None
  try:
    os.fchmod(fd, 0o600)
  finally:
    os.close(fd)
