#!/usr/bin/env python3
"""Failure modes of tools/pr_risk.py, each a real commit in a throwaway git repository:
- a change outside plugins/<id>/, dist/ and index.json (CI, tools, plugins/conftest.py) is auto-merged
- a plugin with claims, safety/rules, a platform, or a control category is auto-merged
- a control plugin moved to category ui in the same pull request is auto-merged
- a display plugin that imports outside the allowlist, uses dynamic code, dunder attributes or ui_state.params is auto-merged
- a binary file or a deleted plugin is auto-merged
- a plain display plugin change is not auto-merged
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

TOOL = Path(__file__).resolve().parent / "pr_risk.py"
TILE = "from openpilot.tesla.ui.tile import Tile\nimport time\n\nclass T(Tile):\n  def state(self, sm):\n    return time.time()\n"


def manifest(**kw):
  return json.dumps({"id": "t", "category": "ui", "claims": [], **kw})


CASES = {  # label: (files to write, files to delete, expected auto)
  "display-ok": ({"plugins/t/ui.py": TILE + "# v2\n", "index.json": "{}"}, [], True),
  "new-display-plugin": ({"plugins/n/plugin.json": manifest(id="n"), "plugins/n/ui.py": TILE}, [], True),
  "ci-file": ({".github/workflows/ci.yml": "x"}, [], False),
  "tools-file": ({"tools/build_index.py": "x"}, [], False),
  "conftest": ({"plugins/conftest.py": "x"}, [], False),
  "claims": ({"plugins/t/plugin.json": manifest(claims=["can.tx:0x3E9:1"])}, [], False),
  "safety": ({"plugins/t/plugin.json": manifest(safety={"param_bit": "0x8000"})}, [], False),
  "rules": ({"plugins/t/plugin.json": manifest(rules=[{"type": "template_clone.v1"}])}, [], False),
  "platform": ({"plugins/t/plugin.json": manifest(platform={"machine": "aarch64"})}, [], False),
  "control-category": ({"plugins/t/plugin.json": manifest(category="longitudinal_planning")}, [], False),
  "control-to-ui": ({"plugins/c/plugin.json": manifest(id="c")}, [], False),
  "import-os": ({"plugins/t/ui.py": "import os\n"}, [], False),
  "from-subprocess": ({"plugins/t/ui.py": "from subprocess import run\n"}, [], False),
  "params-import": ({"plugins/t/ui.py": "from openpilot.common.params import Params\n"}, [], False),
  "eval": ({"plugins/t/ui.py": "eval('1')\n"}, [], False),
  "ui-state-params": ({"plugins/t/ui.py": "from openpilot.selfdrive.ui.ui_state import ui_state\nui_state.params.put('X', '1')\n"}, [], False),
  "dunder": ({"plugins/t/ui.py": "x = ().__class__\n"}, [], False),
  "syntax-error": ({"plugins/t/ui.py": "def (\n"}, [], False),
  "binary": ({"plugins/t/lib.so": "\0"}, [], False),
  "deleted-plugin": ({}, ["plugins/t/plugin.json", "plugins/t/ui.py"], False),
}


def main():
  records = []
  with tempfile.TemporaryDirectory() as tmp:
    def git(*args):
      subprocess.run(["git", "-C", tmp, "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True, capture_output=True)

    def write(files):
      for path, text in files.items():
        (Path(tmp) / path).parent.mkdir(parents=True, exist_ok=True)
        (Path(tmp) / path).write_text(text)

    git("init", "-q", "-b", "main")
    write({"plugins/t/plugin.json": manifest(), "plugins/t/ui.py": TILE,
           "plugins/c/plugin.json": manifest(id="c", category="lateral_control"), "plugins/c/plugin.py": "x = 1\n"})
    git("add", "-A")
    git("commit", "-qm", "base")
    for label, (files, deleted, expected) in CASES.items():
      git("checkout", "-q", "-B", label, "main")
      write(files)
      for path in deleted:
        (Path(tmp) / path).unlink()
      git("add", "-A")
      git("commit", "-qm", label)
      proc = subprocess.run([sys.executable, str(TOOL), "main", label], cwd=tmp, capture_output=True, text=True)
      result = json.loads(proc.stdout)
      records.append({"label": label, "expected": expected, **result})
      assert proc.returncode == 0 and result["auto"] is expected, records[-1]
  print(json.dumps(records, indent=2))


if __name__ == "__main__":
  main()
