#!/usr/bin/env python3
"""Installed archives -> discovery -> real BigScreenHost/Poll -> GPU screenshots.

Failure modes, before implementation: missing explicit show; traffic icon still
registered as HUD; tile visible parked/disabled or overlapping Driving; stale
lamp on a healthy plan without an updated pulse; lost red/amber/green, control
outline, override, flashing or invalid-plan clearing; cached flash never redraws.
Run with the system's Python dependencies and compiled msgq on PYTHONPATH:
  python3 plugins/traffic_light/tests/ui_host_e2e.py --system /path/to/xpilot --output /tmp/traffic-ui
"""
import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import types

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument("--system", type=Path, required=True)
ap.add_argument("--output", type=Path, required=True)
args = ap.parse_args()
repo = Path(__file__).resolve().parents[3]
args.output.mkdir(parents=True, exist_ok=True)
sys.path[:0] = [str(args.system), str(args.system / "opendbc_repo")]
from tools.sp_tesla_e2e import store_e2e

sys.argv = [sys.argv[0], "--repo", str(repo)]
assert store_e2e.main() == 0
from tools.sp_tesla_e2e.offroad_pages_check import FakeParams
from openpilot.tesla import plugins
from openpilot.tesla.plugin_params import ParamsView

params = FakeParams()
view = ParamsView(params, plugins.INSTALLED_SPECS)
for plugin in plugins.PLUGINS:
  if plugin.ENABLE_PARAM:
    view.put_bool(plugin.ENABLE_PARAM, plugin.NAME == "traffic_light")
os.environ.update(SCALE="1", SUNNYPILOT_UI="0")
sys.modules["openpilot.common.params"] = types.SimpleNamespace(Params=lambda: params)
sys.modules["openpilot.common.hardware"] = types.SimpleNamespace(
  PC=True, COMMA_HARDWARE=False, HARDWARE=types.SimpleNamespace(get_device_type=lambda: "pc"))
sys.modules["openpilot.sunnypilot.hardware.profile"] = types.SimpleNamespace(
  HardwareProfile=types.SimpleNamespace(C3XL="c3xl"), get_hardware_profile=lambda: "pc")
from openpilot.cereal import messaging

plan = types.SimpleNamespace()
latest = {"plan": plan}
plugins.PluginMsgs = lambda: types.SimpleNamespace(latest=lambda kind, source: vars(latest["plan"]) if latest["plan"] else None)
sm = types.SimpleNamespace(alive={"longitudinalPlanSP": True}, valid={"longitudinalPlanSP": True},
                           updated={"longitudinalPlanSP": False})
class SM(dict):
  alive, valid, updated = sm.alive, sm.valid, sm.updated
  def update(self, timeout): pass
sm = SM()
state = types.SimpleNamespace(started=True, engaged=False, status=0, sm=sm, params=params, is_metric=True,
                              add_offroad_transition_callback=lambda cb: None)
sys.modules["openpilot.selfdrive.ui.ui_state"] = types.SimpleNamespace(
  ui_state=state, UIStatus=types.SimpleNamespace(DISENGAGED=0), device=types.SimpleNamespace(awake=True))
import pyray as rl
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.widgets import Widget

class Page(Widget):
  def __init__(self, *args): super().__init__()
  def _render(self, rect): pass
for module, name in (("device", "DevicePage"), ("onboarding", "OnboardingPage")):
  sys.modules["openpilot.tesla.ui.pages." + module] = types.SimpleNamespace(**{name: Page})
from openpilot.tesla.ui import screen, tiles, plugin_ui
from openpilot.tesla.ui.tile import Tile

traffic = [tile for name, tile in plugin_ui.load(Tile) if name == "traffic_light"]
assert len(traffic) == 1
assert traffic[0].SPEC.show == "onroad" and traffic[0].SPEC.sizes == ("small",)
assert not [w for name, w in plugin_ui.load(plugin_ui.HudOverlay) if name == "traffic_light"]
ui = importlib.import_module("xpilot_ext.traffic_light.ui")
screen.messaging.SubMaster = lambda services: sm
rl.set_trace_log_level(rl.TraceLogLevel.LOG_WARNING)
rl.init_window(*tiles.SCREEN, "Traffic light tile host E2E")
font = rl.load_font_ex("/System/Library/Fonts/Supplemental/Arial.ttf", 100, None, 0)
gui_app._fonts = {weight: font for weight in FontWeight}
gui_app._patch_text_functions()
rl._orig_begin_scissor_mode = rl.begin_scissor_mode
target = rl.load_render_texture(*tiles.SCREEN)
host = screen.BigScreenHost()
entry = host._entries["traffic_light"]
phase = ui.TrafficControlPhase
cases = []
try:
  for name, light, control_phase, applied, override, healthy, mode, active_lamps, outline, frame in (
    ("red", 1, phase.braking, True, False, True, 4, ("red",), True, 0),
    ("amber", 3, phase.yellowStop, True, False, True, 4, ("amber",), True, 1),
    ("green", 2, phase.off, False, False, True, 4, ("green",), False, 2),
    ("flash-on", 4, phase.flashingGreenStop, True, False, True, 4, ("green",), True, 0),
    ("flash-off", 4, phase.flashingGreenStop, True, False, True, 4, (), True, gui_app.target_fps // 2),
    ("override", 1, phase.braking, True, True, True, 4, ("red",), False, 0),
    ("passed", 1, phase.passed, True, False, True, 4, (), False, 0),
    ("invalid", 1, phase.braking, True, False, False, 4, (), False, 0),
    ("mode-off", 1, phase.braking, True, False, True, 0, (), False, 0),
  ):
    plan.mode, plan.lightState, plan.phase = mode, light, int(control_phase)
    plan.rawDistance, plan.quality, plan.applied, plan.driverOverrideActive = 30.0, 0 if light == 4 else 2, applied, override
    latest["plan"] = plan if healthy else None
    gui_app._frame = frame
    entry.poll.next = float("-inf")
    driving = host.viewport()
    assert entry.failures == 0
    placed = tiles.tile_rect(host._placed["traffic_light"])
    assert not rl.check_collision_recs(rl.Rectangle(*placed), driving)
    rl.begin_texture_mode(target)
    rl.clear_background(rl.BLACK)
    rl.draw_rectangle_rec(driving, rl.Color(35, 45, 60, 255))
    host.render()
    rl.end_texture_mode()
    image = rl.load_image_from_texture(entry.texture.texture)
    colors = rl.load_image_colors(image)
    rgb = {(c.r, c.g, c.b) for c in colors[0:image.width * image.height]}
    rl.unload_image_colors(colors)
    rl.unload_image(image)
    for lamp, color in (("red", ui.RED), ("amber", ui.AMBER), ("green", ui.GREEN), ("outline", ui.CONTROL_OUTLINE)):
      assert ((color.r, color.g, color.b) in rgb) == (outline if lamp == "outline" else lamp in active_lamps), (name, lamp)
    image = rl.load_image_from_texture(target.texture)
    rl.image_flip_vertical(image)
    shot = args.output / (name + ".png")
    assert rl.export_image(image, str(shot))
    rl.unload_image(image)
    cases.append({"name": name, "tile_rect": placed, "view": [driving.x, driving.y, driving.width, driving.height],
                  "screenshot": shot.name, "sha256": hashlib.sha256(shot.read_bytes()).hexdigest()})
  latest["plan"] = None
  assert not entry.tile.state(sm)[0].visible
  state.started = False
  assert "traffic_light" not in host._place("park")
  view.put_bool("TeslaTrafficSignalControlEnabled", False)
  host._enabled._t = -1.0
  assert "traffic_light" not in host._place("drive")
  result = {"system_head": subprocess.check_output(["git", "-C", str(args.system), "rev-parse", "HEAD"], text=True).strip(),
            "cases": cases, "errors": []}
  (args.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
finally:
  for e in host._entries.values():
    if e.texture is not None:
      rl.unload_render_texture(e.texture)
  rl.unload_render_texture(target)
  rl.unload_font(font)
  rl.close_window()
print(json.dumps(result, indent=2))
