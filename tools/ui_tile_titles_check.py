"""Failure: a downloaded tile title bypasses the host translation flag."""
import ast
from pathlib import Path
root=Path(__file__).resolve().parents[1]
checked=0
for path in sorted((root / "plugins").glob("*/ui.py")):
  if path.parent.name == "ip_tile": continue  # IP is an unchanged acronym; its existing network read is out of scope.
  module=ast.parse(path.read_text(encoding="utf8"))
  for cls in module.body:
    if not isinstance(cls, ast.ClassDef) or not any(isinstance(b, ast.Name) and b.id=="Tile" for b in cls.bases): continue
    spec=next((n for n in cls.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id=="SPEC" for t in n.targets)), None)
    if spec is None: continue
    flag=next((n for n in cls.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id=="TRANSLATE_TITLE" for t in n.targets)), None)
    assert flag is not None and isinstance(flag.value, ast.Constant) and flag.value.value is True, f"{path.parent.name}: title translation disabled"
    checked+=1
assert checked >= 10, f"expected all tiles, got {checked}"
print(f"{checked} downloaded tile titles opt into host translation")
