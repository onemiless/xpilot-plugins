"""Restart only this optional network worker; the host's driving policy is unchanged."""
import time
from openpilot.system.manager.process import PythonProcess


class RestartingProcess(PythonProcess):
  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.next_start = 0.
    self.failures = 0
    self.started_at = 0.

  def start(self):
    now = time.monotonic()
    if self.proc is not None and self.proc.exitcode is not None:
      self.stop()
      self.failures = 0 if now - self.started_at > 60 else min(5, self.failures + 1)
      self.next_start = now + min(30, 2 ** self.failures)
    if self.proc is None and now >= self.next_start:
      self.started_at = now
      super().start()
