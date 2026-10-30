"""Failure cases: worker runs without a manual request, onroad or while engaged;
start/stop UI invokes a privileged start before its service lease exists; stopping
must only alter the intent, not execute arbitrary backend operations.
Synthetic state only; this test never changes a device or network.
"""
import ast
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


class Params:
  def __init__(self, **values): self.values = values
  def get_bool(self, key): return bool(self.values.get(key, False))
  def put_bool(self, key, value, block=False): self.values[key] = value


def extract(path, name, cls=None):
  module = ast.parse(path.read_text(encoding='utf-8'))
  nodes = module.body
  if cls:
    nodes = next(n.body for n in nodes if isinstance(n, ast.ClassDef) and n.name == cls)
  return next(n for n in nodes if isinstance(n, ast.FunctionDef) and n.name == name)


def function(node, namespace):
  node.decorator_list = []
  module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
  exec(compile(module, '<fixture>', 'exec'), namespace)
  return namespace[node.name]


def main():
  source = (ROOT / 'plugin.py').read_text(encoding='utf-8')
  selector = 'should_run' if 'def should_run(' in source else 'always'
  params = Params(IsOffroad=True, IsEngaged=False, ExtStarlinkConnect=False)
  class Session:
    active = False
    def requested(self): return self.active
    def set_requested(self, value): self.active = value
    def clear(self): self.active = False
  session = Session()
  should = function(extract(ROOT / 'plugin.py', selector), {'ParamsView': lambda p: p, 'session': session})
  assert not should(False, params, None), 'worker started without a user request'
  params.put_bool('ExtStarlinkConnect', True)
  assert not should(False, params, None), 'stale persistent request survived reboot'
  session.set_requested(True)
  assert should(False, params, None), 'parked user request ignored'
  assert not should(True, params, None), 'worker allowed onroad'
  assert not session.requested() and not params.get_bool('ExtStarlinkConnect'), 'onroad request not cleared'
  params.put_bool('ExtStarlinkConnect', True); session.set_requested(True)
  params.put_bool('IsEngaged', True)
  assert not should(False, params, None), 'worker allowed while engaged'
  params.put_bool('IsEngaged', False); params.put_bool('IsOffroad', False)
  assert not should(False, params, None), 'worker allowed without offroad state'
  params = Params()
  page = SimpleNamespace(message='', last=1, run=lambda *a: (_ for _ in ()).throw(AssertionError('backend race')))
  connect = function(extract(ROOT / 'ui.py', 'connect', 'StarlinkPage'),
                     {'allowed': lambda: True, 'view': lambda _: params, 'ui_state': SimpleNamespace(params=params), 'tr': lambda s: s, 'session': session})
  connect(page, True); assert params.get_bool('ExtStarlinkConnect') and session.requested()
  connect(page, False); assert not params.get_bool('ExtStarlinkConnect') and not session.requested()
  print('manual request, parked gating, engaged/onroad stop and UI intent passed')


def test_manual_lifecycle():
  main()


if __name__ == '__main__':
  main()
