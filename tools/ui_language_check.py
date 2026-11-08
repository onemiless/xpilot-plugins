#!/usr/bin/env python3
"""Verify tile state strings use language packs while data remains intact.

Failures: English-only status/label, or speed or road data changed.
Tests the actual plugin state methods with synthetic messages.
"""
import ast
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
DICTIONARY = {'no lead':'未检测到前车', 'No map data':'暂无地图数据', 'Limit {}':'限速 {}',
              'SET {}':'设定 {}', 'offline':'离线', 'wifi':'无线网络'}
tr = lambda text: DICTIONARY.get(text, text)


def method(plugin, cls, name, globals_):
  path = ROOT / 'plugins' / plugin / 'ui.py'
  module = ast.parse(path.read_text(encoding='utf-8'))
  node = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == cls)
  fn = next(n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == name)
  code = ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[]))
  exec(compile(code, str(path), 'exec'), globals_)
  return globals_[name]


class State:
  def __init__(self, entries, alive):
    self.entries, self.alive = entries, alive
  def __getitem__(self, key):
    return self.entries[key]


def main():
  lead = method('lead_card', 'LeadCardTile', 'state', {'tr': tr})
  sm = State({'radarState': NS(leadOne=NS(present=False))}, {'radarState': False})
  assert lead(None, sm) == ('-', '', '未检测到前车'), 'lead status'
  cv = NS(MS_TO_KPH=3.6, MS_TO_MPH=2.236936, KPH_TO_MPH=0.621371)
  nav = method('map_tile', 'MapTile', 'state', {'tr': tr, 'CV': cv, 'ui_state': NS(is_metric=True)})
  assert nav(None, State({}, {'liveMapDataSP': False, 'navInstruction': False})) == ('暂无地图数据',)
  mapdata = NS(speedLimitValid=True, speedLimit=10, roadName='Example Road')
  assert nav(None, State({'liveMapDataSP': mapdata}, {'liveMapDataSP': True, 'navInstruction': False})) == ('限速 36', 'Example Road')
  speed = method('speed_card', 'SpeedCardTile', 'state', {'tr': tr, 'CV': cv, 'SET_SPEED_NA': 255, 'ui_state': NS(is_metric=True)})
  assert speed(None, State({'carState': NS(vEgoCluster=10, vCruiseCluster=80)}, {})) == ('36', 'km/h', '设定 80')
  print('Tile translation states, numeric values and road name: OK')


if __name__ == '__main__':
  main()
