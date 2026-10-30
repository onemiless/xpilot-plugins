"""Failure: removing the privileged marker makes a healthy VPN appear disconnected."""
import ast
import json
from pathlib import Path
import subprocess
import types

source = (Path(__file__).resolve().parents[1] / 'backend.py').read_text(encoding='utf-8')
tree = ast.parse(source)
function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'tun_ready')
program = compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])), 'backend.py', 'exec')

class FakePath:
  def __init__(self, name): self.name = str(name)
  def __truediv__(self, name): return FakePath(self.name + '/' + name)
  def exists(self): return False
  def read_text(self): return '1'

class FakeSubprocess:
  TimeoutExpired = subprocess.TimeoutExpired
  def __init__(self): self.route = 'default dev c3mh0'
  def run(self, args, **_kwargs):
    value = self.route if args[1] == 'route' else json.dumps([{'priority': 17880, 'table': '17890'}])
    return types.SimpleNamespace(stdout=value)

process = FakeSubprocess()
namespace = {'Path': FakePath, 'ROOT': FakePath('/unused'), 'subprocess': process, 'json': json}
exec(program, namespace)
assert namespace['tun_ready'](), 'active interface and route must not require a privileged marker'
process.route = ''
assert not namespace['tun_ready'](), 'missing route must not appear connected'
print('VPN status uses actual interface and route without privileged marker: PASS')
