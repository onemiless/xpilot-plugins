"""Failure cases before fixes: queued/slow work changes network after parking
ends; tile reads private stale state; code elevates or supplies executable paths;
binary provenance lacks a build recipe. Uses synthetic data only, no device I/O.
"""
import ast
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('checked_backend', ROOT / 'backend.py')
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)
state = {'parked': True, 'selected': False}
def parked():
  if not state['parked']:
    raise RuntimeError('synthetic onroad')
def queued(*_):
  state['parked'] = False
def select(_):
  state['selected'] = True
with tempfile.TemporaryDirectory() as directory, patch.object(backend, 'ROOT', Path(directory)), \
     patch.object(backend, 'parked', parked), patch.object(backend.fcntl, 'flock', queued), \
     patch.object(backend, 'select_node', select), patch.object(sys, 'stdin', io.StringIO('fixture')):
  try:
    backend.main('select')
  except RuntimeError:
    pass
assert not state['selected'], 'queued request mutated network after parking ended'
state.update(parked=True, selected=False)
def delayed_nodes(path, payload=None, **_):
  if payload is None:
    state['parked'] = False
    return {'proxies': {'C3-PROXY': {'all': ['fixture']}}}
  state['selected'] = True
  return {}
with tempfile.TemporaryDirectory() as directory, patch.object(backend, 'ROOT', Path(directory)), \
     patch.object(backend, 'parked', parked), patch.object(backend, 'api', delayed_nodes):
  try:
    backend.select_node('fixture')
  except RuntimeError:
    pass
assert not state['selected'], 'slow node lookup allowed an onroad selection'
tree = ast.parse((ROOT / 'ui.py').read_text(encoding='utf-8'))
tile = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'StarlinkTile')
assert 'read_text' not in ast.unparse(tile), 'tile reads private stale state'
assert 'PluginMsgs' in (ROOT / 'ui.py').read_text(encoding='utf-8')
assert (ROOT / 'build_native.py').is_file(), 'missing reproducible build entry'
for name in ('backend.py', 'client.py', 'pairing.py'):
  assert 'sudo' not in (ROOT / name).read_text(encoding='utf-8'), name
print('queued gate, tile message contract and host-only elevation verified')
