"""S1 selection with a recoverable per-frame control envelope."""
from collections import Counter
from threading import Lock

from openpilot.tesla.model_source import ModelSource
from .profile import decode, reject_reason


class PhoneSource(ModelSource):
  def __init__(self, directory):
    self.envelope_reason = ''
    self.counts = Counter()
    self.counts_lock = Lock()
    super().__init__(directory, decode, self._admit)

  def pick(self, local):
    result = super().pick(local)
    if self.selected or self.reason == 'warmup':
      with self.counts_lock:
        self.counts['adopted' if self.selected else 'warmup'] += 1
    return result

  def take_counts(self):
    with self.counts_lock:
      counts = dict(self.counts)
      self.counts.clear()
    return counts

  def _admit(self, local, remote):
    self.envelope_reason = reject_reason(local, remote)
    return not self.envelope_reason

  def _reject(self, reason, lock=False):
    if reason == 'admission':
      # A control outlier is recoverable; structural/protocol failures still latch in S1.
      self.fresh = 0
      reason, lock = 'envelope:' + self.envelope_reason, False
    result = super()._reject(reason, lock)
    with self.counts_lock:
      self.counts[self.reason] += 1
    return result
