from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from openpilot.tesla import plugins

import pyray as rl

from .controller import TrafficControlPhase
from openpilot.system.ui.lib.application import gui_app
from openpilot.tesla.ui.tile import Tile
from openpilot.tesla.ui.tiles import TileSpec


RED = rl.Color(255, 72, 72, 255)
AMBER = rl.Color(255, 190, 50, 255)
GREEN = rl.Color(53, 220, 118, 255)
LAMP_OFF = rl.Color(65, 69, 76, 220)
BORDER = rl.Color(255, 255, 255, 38)
TRAFFIC_CARD_WIDTH = 128.0
TRAFFIC_CARD_HEIGHT = 256.0
TRAFFIC_LIGHT_HOUSING_WIDTH = 104.0
TRAFFIC_LIGHT_HOUSING_HEIGHT = 232.0
TRAFFIC_LIGHT_RADIUS = 20.0
CONTROL_OUTLINE = rl.Color(64, 156, 255, 255)


def traffic_control_highlighted(state: TrafficSignalDisplayState) -> bool:
  # Blue means control is applied in the current plan.
  return bool(state.visible and state.control_active and not state.driver_override_active
              and state.phase not in (int(TrafficControlPhase.off), int(TrafficControlPhase.passed)))


@dataclass(frozen=True)
class TrafficSignalDisplayState:
  visible: bool = False
  has_signal: bool = False
  control_active: bool = False
  driver_override_active: bool = False
  light_state: int = 0
  phase: int = int(TrafficControlPhase.off)
  flashing: bool = False

  @classmethod
  def from_plan(cls, target, *, valid: bool = True) -> TrafficSignalDisplayState:
    if not valid:
      return cls()
    phase = int(target.phase)
    mode = int(target.mode)
    light = int(target.lightState)
    raw_distance = float(target.rawDistance)
    # Explicit DBC OFF (4) has no ordinary RED/GREEN quality. Only a confirmed
    # flash STOP may animate it as the dark half of a flashing-green signal.
    confirmed_off_flash = phase == int(TrafficControlPhase.flashingGreenStop) and light == 4
    has_signal = bool(
      (int(target.quality) > 0 or confirmed_off_flash)
      and 0.0 <= raw_distance <= 200.0
      and (0 <= light <= 3 or confirmed_off_flash)
      and phase != int(TrafficControlPhase.passed)
    )
    return cls(
      visible=mode == 4,
      has_signal=has_signal,
      control_active=bool(target.applied),
      driver_override_active=bool(target.driverOverrideActive),
      light_state=light,
      phase=phase,
      flashing=has_signal and phase == int(TrafficControlPhase.flashingGreenStop),
    )


class TrafficControlRenderer(Tile):
  """Icon-only traffic signal driven by the already-published final plan."""

  SPEC = TileSpec("traffic_light", "Traffic Light", sizes=("small",), show="onroad", hz=10, order=40)
  TRANSLATE_TITLE = True

  def __init__(self):
    self.msgs = plugins.PluginMsgs()

  def state(self, sm):
    payload = self.msgs.latest("traffic.plan", "traffic_light")
    state = (TrafficSignalDisplayState.from_plan(SimpleNamespace(**payload))
             if payload is not None else TrafficSignalDisplayState())
    # Include the flash half-cycle in the cache key so an unchanged plan still animates.
    return state, state.flashing and int(gui_app.frame / max(1, gui_app.target_fps // 2)) % 2 == 0

  def draw(self, rect: rl.Rectangle, size: str, value) -> None:
    self.face(rect)
    state, flash_on = value
    if not state.visible:
      return

    scale = min((rect.width - 32) / TRAFFIC_CARD_WIDTH,
                (rect.height - self.title_size(rect) - 42) / TRAFFIC_CARD_HEIGHT)
    icon = rl.Rectangle(rect.x + (rect.width - TRAFFIC_CARD_WIDTH * scale) / 2, rect.y + 14,
                        TRAFFIC_CARD_WIDTH * scale, TRAFFIC_CARD_HEIGHT * scale)
    housing = rl.Rectangle(icon.x + 12 * scale, icon.y + 12 * scale,
                           TRAFFIC_LIGHT_HOUSING_WIDTH * scale, TRAFFIC_LIGHT_HOUSING_HEIGHT * scale)
    highlighted = traffic_control_highlighted(state)
    if highlighted:
      rl.draw_rectangle_rounded(icon, 0.6, 24, rl.Color(CONTROL_OUTLINE.r, CONTROL_OUTLINE.g, CONTROL_OUTLINE.b, 38))
    rl.draw_rectangle_rounded(housing, 0.6, 24, rl.Color(12, 15, 19, 210))
    rl.draw_rectangle_rounded_lines_ex(housing, 0.6, 24, (5.0 if highlighted else 2.0) * scale,
                                       CONTROL_OUTLINE if highlighted else BORDER)

    for index, (color, light) in enumerate(zip((RED, AMBER, GREEN), (1, 3, 2), strict=True)):
      center = rl.Vector2(housing.x + housing.width / 2, housing.y + housing.height * (2 * index + 1) / 6)
      active = state.has_signal and state.light_state == light
      if state.has_signal and state.flashing and light == 2:
        active = flash_on
      if active:
        rl.draw_circle_v(center, (TRAFFIC_LIGHT_RADIUS + 6) * scale, rl.Color(color.r, color.g, color.b, 35))
      rl.draw_circle_v(center, TRAFFIC_LIGHT_RADIUS * scale, color if active else LAMP_OFF)
