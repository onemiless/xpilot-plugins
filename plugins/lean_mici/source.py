"""S1 selection with a recoverable per-frame control envelope."""
from openpilot.tesla.model_source import ModelSource
from .profile import decode, reject_reason


class PhoneSource(ModelSource):
  def __init__(self, directory):
    self.envelope_reason = ''
    super().__init__(directory, decode, self._admit)

  def _admit(self, local, remote):
    self.envelope_reason = reject_reason(local, remote)
    return not self.envelope_reason

  def _reject(self, reason, lock=False):
    if reason == 'admission':
      # A control outlier is recoverable; structural/protocol failures still latch in S1.
      self.fresh = 0
      reason, lock = 'envelope:' + self.envelope_reason, False
    return super()._reject(reason, lock)
