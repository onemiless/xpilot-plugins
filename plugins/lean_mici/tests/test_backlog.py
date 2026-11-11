import ast
from pathlib import Path
from unittest.mock import Mock


def test_busy_skips_encoding_without_reset():
  tree = ast.parse((Path(__file__).parents[1] / 'worker.py').read_text())
  loop = next(n for n in ast.walk(tree) if isinstance(n, ast.While)
              and any(isinstance(c, ast.Try) and any(isinstance(x, ast.Assign) and
                      isinstance(x.value, ast.Call) and isinstance(x.value.func, ast.Attribute) and
                      x.value.func.attr == 'capture' for x in c.body) for c in n.body))
  capture = next(i for i, n in enumerate(loop.body) if isinstance(n, ast.Try))
  start = 3  # skip beat(), failure check, 1 s report
  # Execute the actual sender prefix once, with an already full reply queue.
  body = loop.body[start:capture + 1]
  camera = Mock()
  cv = Mock()
  cv.__enter__ = Mock(return_value=cv)
  cv.__exit__ = Mock(return_value=False)
  state = dict(pending=[1, 2], IN_FLIGHT=2, cv=cv, dropped=0, epoch=7, reset=False,
               paired=3, camera=camera, metadata=lambda fd: tuple(range(40)), fd=0,
               host=None, used=None, now=lambda: 0)
  code = ast.fix_missing_locations(ast.Module(body=[ast.For(target=ast.Name(id='_', ctx=ast.Store()),
           iter=ast.Tuple(elts=[ast.Constant(0)], ctx=ast.Load()), body=body, orelse=[])], type_ignores=[]))
  exec(compile(code, '<sender-prefix>', 'exec'), state)
  camera.capture.assert_not_called()
  assert (state['epoch'], state['reset'], state['paired'], state['dropped']) == (7, False, 3, 1)
  assert cv.wait_for.call_args.args[1] == .05
  assert not cv.wait_for.call_args.args[0]()
