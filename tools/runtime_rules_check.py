#!/usr/bin/env python3
"""Published archives -> real host validator/discovery/card setup -> ABI table bytes.

Failure cases listed before implementation (after firmware R1-R13):
- R14 a source or archived manifest still carries safety, including an unindexed old archive
- R15 a changed source/archive is rejected by the real host validator or has mismatched can.tx
- R16 compiled AUTO/AMBIENT (NAV when present) tables differ from the host reference bytes
- R17 a rule plugin omits requires.rule_abi=1 or is incompatible with HOST_CAPS
- R18 setup still writes per-plugin safetyParam bits, loses auto-speed gates, or card drops its rules
- R19 an old release remains indexed, the minor version is not bumped, or published bytes differ from source

Run with the X-33 host checkout on PYTHONPATH; --output retains hashes and compiled tables.
"""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import tarfile
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def main():
  ap = argparse.ArgumentParser(description=__doc__)
  ap.add_argument('--output', type=Path, required=True)
  output = ap.parse_args().output.resolve()
  output.mkdir(parents=True, exist_ok=True)
  errors, hashes = [], {}
  with tempfile.TemporaryDirectory() as tmp:
    installed = Path(tmp) / 'installed'
    os.environ['XPILOT_PLUGINS_DIR'] = str(installed)
    runpy.run_path(str(ROOT / 'plugins/conftest.py'))
    from openpilot.tesla import manifest as mf, panda_rules as pr
    fixture = Path(mf.__file__).resolve().parents[2] / 'opendbc_repo/opendbc/safety/tests/xpilot_rules_helpers.py'
    # Evaluate the reference definitions without importing the unrelated C loader/cffi harness.
    definitions = [n for n in ast.parse(fixture.read_text()).body if isinstance(n, ast.Assign)
                   and isinstance(n.targets[0], ast.Name) and n.targets[0].id in ('AUTO', 'AMBIENT', 'NAV')]
    examples = {'rule': pr.rule, 'field': pr.field}
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(fixture), 'exec'), examples)
    references = {'auto_speed': examples['AUTO'], 'ambient_light': examples['AMBIENT']}
    if (ROOT / 'plugins/navassist').exists():
      references['navassist'] = examples['NAV']
    from build_index import archive
    index = json.loads((ROOT / 'index.json').read_text())['plugins']
    for path in sorted((ROOT / 'plugins').rglob('plugin.json')):
      if 'safety' in json.loads(path.read_text()):
        errors.append(f'R14 {path.relative_to(ROOT)}: safety')
    for path in sorted((ROOT / 'dist').glob('*.tar.gz')):
      with tarfile.open(path) as tar:
        for member in tar.getmembers():
          if member.name.endswith('/plugin.json') and 'safety' in json.load(tar.extractfile(member)):
            errors.append(f'R14 {path.relative_to(ROOT)}: safety')
    for pid, reference in references.items():
      source = ROOT / 'plugins' / pid
      entries = [e for e in index if e['id'] == pid]
      if len(entries) != 1 or entries[0]['version'] != '1.1.0':
        errors.append(f'R19 {pid}: expected one 1.1.0 release')
      for entry in entries:
        path = ROOT / 'dist' / entry['url'].split('/dist/', 1)[1]
        data = path.read_bytes()
        hashes[str(path.relative_to(ROOT))] = hashlib.sha256(data).hexdigest()
        if hashes[str(path.relative_to(ROOT))] != entry['sha256'] or data != archive(source):
          errors.append(f'R19 {pid}: archive/index/source mismatch')
        with tarfile.open(path) as tar:
          tar.extractall(installed, filter='data')
      for path in (source / 'plugin.json', installed / pid / 'plugin.json'):
        hashes[str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else f'installed/{pid}/plugin.json'] = hashlib.sha256(path.read_bytes()).hexdigest()
        raw = json.loads(path.read_text())
        if raw.get('requires', {}).get('rule_abi') != 1:
          errors.append(f'R17 {pid}: requires.rule_abi=1 missing')
        if pr.encode_rules(raw.get('rules', [])) != pr.table([reference]):
          errors.append(f'R16 {pid}: reference rules missing or different')
        try:
          m = mf.parse(path)
        except ValueError as e:
          errors.append(f'R15 {pid} {path.parent.parent.name}: {e}')
          continue
        ok, reason = mf.compatible(m, mf.HOST_CAPS)
        if m.requires.get('rule_abi') != mf.RULE_ABI or mf.RULE_ABI != 1 or not ok:
          errors.append(f'R17 {pid}: {m.requires} {reason}')
        actual, expected = pr.encode_rules(m.rules), pr.table([reference])
        if actual != expected:
          errors.append(f'R16 {pid}: table differs')
        (output / f'{pid}.bin').write_bytes(actual)
        (output / f'{pid}-reference.bin').write_bytes(expected)
      if 'safetyParam' in (source / 'plugin.py').read_text():
        errors.append(f'R18 {pid}: setup writes safetyParam')
    if not errors:
      from opendbc.car import structs
      from opendbc.car.tesla.carcontroller import get_safety_CP
      from opendbc.sunnypilot.car.tesla.values import TeslaFlagsSP
      from openpilot.tesla import plugins
      from openpilot.tesla.plugin_params import ParamsView
      classes = [p for p in plugins.PLUGINS if p.NAME in references]
      assert {p.NAME for p in classes} == set(references), ('R18', plugins.LOAD_ERRORS)
      core = SimpleNamespace(put=lambda key, value, **kw: values.update({key: value}))
      params = ParamsView(core, plugins.INSTALLED_SPECS)
      for cls in classes:
        params.put_bool(cls.ENABLE_PARAM, True)
      faults = []
      for longitudinal in (False, True):
        for vehicle_bus in (False, True):
          values = {}
          cp = get_safety_CP()
          cp.openpilotLongitudinalControl = longitudinal
          cp_sp = structs.CarParamsSP(flags=TeslaFlagsSP.HAS_VEHICLE_BUS if vehicle_bus else 0, safetyParam=1)
          with patch.object(plugins, 'PLUGINS', classes), patch.object(plugins, 'record_fault', lambda *a: faults.append(str(a))):
            card = plugins.CarPlugins(cp, params)
            card.setup(cp_sp)
          assert not faults and len(card.plugins) == len(classes), ('R18', faults)
          assert cp_sp.safetyParam == 1, 'R18 safetyParam changed'
          auto = next(p for p in card.plugins if p.NAME == 'auto_speed')
          assert auto.controller.configured == (longitudinal and vehicle_bus), 'R18 auto-speed gate'
          expected = pr.param(pr.table([references[p.NAME] for p in card.plugins]))
          assert values['XpilotPandaRules'] == expected, 'R18 card table differs'
          (output / 'card-param.bin').write_bytes(expected)
  host = Path(mf.__file__).resolve().parents[2]
  for path in (host / 'openpilot/tesla/manifest.py', host / 'openpilot/tesla/panda_rules.py',
               host / 'opendbc_repo/opendbc/safety/tests/xpilot_rules_helpers.py'):
    hashes[str(path.relative_to(host))] = hashlib.sha256(path.read_bytes()).hexdigest()
  result = {'errors': errors, 'host_commit': subprocess.check_output(['git', '-C', str(host), 'rev-parse', 'HEAD'], text=True).strip(),
            'plugin_commit': subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip(),
            'input_sha256': hashes, 'output_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.glob('*.bin')}}
  (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
  print(json.dumps(result, indent=2))
  return bool(errors)


if __name__ == '__main__':
  raise SystemExit(main())
