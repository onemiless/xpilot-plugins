#!/usr/bin/env python3
"""Build dist/<id>-<version>.tar.gz for every plugins/<id>/ and index.json. Reproducible: sorted entries, fixed
mtime/owner/mode, gzip without timestamp, so a reviewer can rebuild and compare sha256.

  python3 tools/build_index.py           # write dist/ and index.json
  python3 tools/build_index.py --check   # exit 1 if dist/ or index.json is out of date (run before merging)
"""
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
RAW = "https://raw.githubusercontent.com/onemiless/xpilot-plugins/main"
API = 1
REQUIRED = ("id", "name", "version", "api", "category", "entry")
SKIP = {"__pycache__", ".pytest_cache", "tests"}  # tests run in CI, devices do not need them


def archive(plugin: Path) -> bytes:
  raw = io.BytesIO()
  with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
    for path in sorted(p for p in plugin.rglob("*") if not SKIP & set(p.relative_to(plugin).parts)):
      if path.is_symlink():
        raise ValueError(f"{path}: links are not allowed")
      info = tarfile.TarInfo(f"{plugin.name}/{path.relative_to(plugin).as_posix()}")
      info.mtime, info.uid, info.gid, info.uname, info.gname = 0, 0, 0, "", ""
      if path.is_dir():
        info.type, info.mode = tarfile.DIRTYPE, 0o755
        tar.addfile(info)
      else:
        data = path.read_bytes()
        info.size, info.mode = len(data), 0o644
        tar.addfile(info, io.BytesIO(data))
  out = io.BytesIO()
  with gzip.GzipFile(fileobj=out, mode="wb", mtime=0, filename="") as gz:
    gz.write(raw.getvalue())
  return out.getvalue()


def build() -> dict[Path, bytes]:
  files, entries = {}, []
  for plugin in sorted(p.parent for p in (ROOT / "plugins").glob("*/plugin.json")):
    m = json.loads((plugin / "plugin.json").read_text())
    missing = [f for f in REQUIRED if f not in m]
    if missing or m["id"] != plugin.name or m["api"] != API:
      raise ValueError(f"{plugin.name}: bad manifest (missing {missing}, id {m.get('id')}, api {m.get('api')})")
    if not (plugin / "__init__.py").is_file():
      raise ValueError(f"{plugin.name}: missing __init__.py")
    if m.get("platform") is None and any(p.suffix == ".so" or ".so." in p.name for p in plugin.rglob("*")):
      raise ValueError(f"{plugin.name}: has binaries but no platform (run its build_native.py on the target)")
    if m.get("platform") is None and (plugin / "build_native.py").exists():
      print(f"skipped {plugin.name}: native plugin not built yet (run its build_native.py on the target)", file=sys.stderr)
      continue
    if "default" in m and type(m["default"]) is not bool:
      raise ValueError(f"{plugin.name}: default must be boolean")
    data = archive(plugin)
    name = f"dist/{m['id']}-{m['version']}.tar.gz"
    files[ROOT / name] = data
    entries.append({"id": m["id"], "name": m["name"], "version": m["version"], "api": m["api"], "category": m["category"],
                    "author": m.get("author", ""), "description": m.get("description", ""), "url": f"{RAW}/{name}",
                    "sha256": hashlib.sha256(data).hexdigest(), "platform": m.get("platform"), "default": m.get("default", False),
                    "claims": m.get("claims", [])})  # host derives the store category from claims (manifest.category_of)
  files[ROOT / "index.json"] = (json.dumps({"api": API, "plugins": entries}, indent=2) + "\n").encode()
  return files


if __name__ == "__main__":
  files = build()
  if "--check" in sys.argv:
    stale = [str(p.relative_to(ROOT)) for p, d in files.items() if not p.exists() or p.read_bytes() != d]
    if stale:
      print("out of date:", ", ".join(stale), "- run python3 tools/build_index.py")
      sys.exit(1)
  else:
    (ROOT / "dist").mkdir(exist_ok=True)
    for path, data in files.items():
      path.write_bytes(data)
