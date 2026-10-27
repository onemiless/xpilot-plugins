#!/usr/bin/env python3
"""Adoption counters: final decision, exact rejection, interval reset and concurrent updates."""
import argparse
import hashlib
import json
import sys
import threading
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
base = types.ModuleType('openpilot.tesla.model_source')
class ModelSource:
  def __init__(self, *args):
    self.selected = False
    self.reason = ''
  def pick(self, outcome):
    self.selected, self.reason = outcome
    if not self.selected and self.reason != 'warmup':
      self._reject(self.reason)
      if self.reason == 'late':
        self._reject('late')
    return outcome
  def _reject(self, reason, lock=False):
    self.reason = reason
base.ModelSource = ModelSource
sys.modules['openpilot.tesla.model_source'] = base
profile = types.ModuleType('plugins.lean_mici.profile')
profile.decode = lambda x: x
profile.reject_reason = lambda local, remote: 'physical_curvature'
sys.modules[profile.__name__] = profile
from plugins.lean_mici.source import PhoneSource

p = argparse.ArgumentParser()
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
a.output.mkdir(parents=True, exist_ok=False)
s = PhoneSource(None)
for decision in [(True, ''), (False, 'late'), (False, 'warmup'), (False, 'envelope:physical_curvature')]:
  assert s.pick(decision) == decision
assert s.take_counts() == {'adopted': 1, 'late': 2, 'warmup': 1, 'envelope:physical_curvature': 1}
assert s.take_counts() == {}
threads = [threading.Thread(target=lambda: [s.pick((False, 'late')) for _ in range(1000)]) for _ in range(4)]
for t in threads: t.start()
for t in threads: t.join()
assert s.take_counts() == {'late': 8000}
(a.output / 'result.json').write_text(json.dumps({'pass': True, 'concurrent_decisions': 4000}, indent=2) + '\n')
(a.output / 'SHA256SUMS').write_text(hashlib.sha256((a.output / 'result.json').read_bytes()).hexdigest() + '  result.json\n')
