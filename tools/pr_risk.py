#!/usr/bin/env python3
"""Decide whether a pull request may merge without a human review.

  python3 tools/pr_risk.py <base> <head>   # prints {"auto": bool, "reasons": [...]}; exit 0 either way

Auto only when every changed file is plugins/<id>/..., dist/<id>-*.tar.gz or index.json, and every touched plugin,
before and after, is a display plugin (category ui/overlays) with no claims, no safety/rules, no platform, only
.py/.json/.md files, and Python that imports only the UI host API and a few stdlib modules, with no dynamic code.
Reads the head as git objects only; never runs pull request code, so it is safe in a privileged workflow.
This is a guard rail, not a sandbox: the workflow also requires a trusted author.
"""
import ast
import json
import re
import subprocess
import sys

CATEGORIES = {"ui", "overlays"}
SUFFIXES = (".py", ".json", ".md")
IMPORTS = {"math", "time", "datetime", "pyray", "openpilot.common.constants", "openpilot.selfdrive.ui.ui_state",
           "openpilot.tesla.plugin", "openpilot.tesla.ui.plugin_ui", "openpilot.tesla.ui.tile", "openpilot.tesla.ui.tiles"}
NAMES = {"eval", "exec", "compile", "__import__", "open", "getattr", "setattr", "delattr", "globals", "locals", "vars",
         "breakpoint", "input", "memoryview"}
ATTRS = {"params", "pm"}  # ui_state.params / a PubMaster would let a display plugin change settings or publish


def git(*args: str) -> str:
  return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def show(rev: str, path: str) -> str | None:
  proc = subprocess.run(["git", "show", f"{rev}:{path}"], capture_output=True, text=True)
  return proc.stdout if proc.returncode == 0 else None


def code_problems(path: str, src: str) -> list[str]:
  try:
    tree = ast.parse(src)
  except SyntaxError:
    return [f"{path}: does not parse"]
  out = []
  for node in ast.walk(tree):
    if isinstance(node, ast.Import):
      out += [f"{path}: import {a.name}" for a in node.names if a.name not in IMPORTS]
    elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module not in IMPORTS:
      out.append(f"{path}: from {node.module} import")
    elif isinstance(node, ast.Name) and node.id in NAMES:
      out.append(f"{path}: uses {node.id}")
    elif isinstance(node, ast.Attribute) and (node.attr in ATTRS or node.attr.startswith("__") and node.attr != "__init__"):
      out.append(f"{path}: uses .{node.attr}")
  return out


def manifest_problems(rev: str, pid: str, side: str) -> list[str]:
  text = show(rev, f"plugins/{pid}/plugin.json")
  if text is None:
    return [f"{pid}: no plugin.json at {side}"]
  m = json.loads(text)
  out = []
  if m.get("category") not in CATEGORIES:
    out.append(f"{pid}: category {m.get('category')!r} at {side}")
  out += [f"{pid}: has {k} at {side}" for k in ("claims", "safety", "rules", "platform") if m.get(k)]
  return out


def classify(base: str, head: str) -> list[str]:
  reasons, touched = [], set()
  for path in git("diff", "--name-only", "--no-renames", f"{base}...{head}").split():
    if path == "index.json":
      continue
    if m := re.fullmatch(r"dist/([a-z0-9_]+)-[0-9][^/]*\.tar\.gz", path):
      touched.add(m[1])
    elif m := re.fullmatch(r"plugins/([a-z0-9_]+)/.+", path):
      touched.add(m[1])
    else:
      reasons.append(f"{path}: outside plugins/<id>/, dist/ and index.json")
  for pid in sorted(touched):
    found = manifest_problems(head, pid, "head")
    if show(base, f"plugins/{pid}/plugin.json") is not None:
      found = manifest_problems(base, pid, "base") + found
    reasons += found
    if found:  # not a display plugin: the code scan would only add noise
      continue
    for path in git("ls-tree", "-r", "--name-only", head, f"plugins/{pid}/").split():
      if not path.endswith(SUFFIXES):
        reasons.append(f"{path}: not .py/.json/.md")
      elif path.endswith(".py"):
        reasons += code_problems(path, show(head, path) or "")
  return reasons


if __name__ == "__main__":
  reasons = classify(sys.argv[1], sys.argv[2])
  print(json.dumps({"auto": not reasons, "reasons": reasons}, indent=2))
