"""Failure: the plugin loses core status when the privileged PID file is removed."""
import ast
from pathlib import Path

source = (Path(__file__).resolve().parents[1] / 'backend.py').read_text(encoding='utf-8')
tree = ast.parse(source)
node = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == 'pid')
module = compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), 'backend.py', 'exec')
result = {'pid': 1234}
namespace = {'gateway': lambda action: result, 'Path': Path, 'ROOT': Path('/no-such-starlink-test'), 'CORE': Path('/unused')}
exec(module, namespace)
assert namespace['pid']() == 1234, 'running core not reported by host'
result['pid'] = None
assert namespace['pid']() is None
result['pid'] = -4
assert namespace['pid']() is None
print('plugin consumes host-owned PID status: PASS')
