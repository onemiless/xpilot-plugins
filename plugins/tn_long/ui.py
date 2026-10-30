"""Onroad HUD: current TN right-scroll follow level and applied t_follow."""
from __future__ import annotations

import pyray as rl

from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.tesla import plugins
from openpilot.tesla.ui.plugin_ui import HudOverlay
from openpilot.tesla.ui.tile import Tile
from openpilot.tesla.ui.tiles import TileSpec


class TnFollowHud(HudOverlay):
  def __init__(self):
    super().__init__()
    self._params = plugins.view(ui_state.params)
    self._level = -1
    self._applied_ms = -1
    self._next_read = 0.0
    self._font = gui_app.font(FontWeight.BOLD)

  def update(self) -> None:
    now = rl.get_time()
    if now < self._next_read:
      return
    self._next_read = now + 0.20
    try:
      self._level = int(self._params.get("ExtTnScrollLevel", return_default=True))
      self._applied_ms = int(self._params.get("ExtTnFollowApplied", return_default=True))
    except (TypeError, ValueError):
      self._level = -1
      self._applied_ms = -1

  def _render(self, rect: rl.Rectangle) -> None:
    if not 0 <= self._level < 7:
      return
    if self._applied_ms >= 0:
      text = f"TN {self._level + 1}/7  {self._applied_ms / 1000.0:.2f}s"
    else:
      text = f"TN {self._level + 1}/7  ramping"
    pad_x, pad_y, height = 18, 10, 46
    size = min(30, max(22, rect.height / 22))
    width = 14 + size * 0.62 * len(text)
    box = rl.Rectangle(rect.x + 24, rect.y + 24, width + pad_x * 2, height)
    rl.draw_rectangle_rec(box, rl.Color(0, 0, 0, 165))
    rl.draw_text_ex(self._font, text, rl.Vector2(box.x + pad_x, box.y + pad_y), size, 1, rl.WHITE)


class TnFollowTile(Tile):
  SPEC = TileSpec("tn_follow", "TN Follow", sizes=("small", "medium"), show="always", hz=2, order=60)

  def __init__(self):
    self._params = plugins.view(ui_state.params)
    self._level = -1
    self._applied_ms = -1

  def state(self, sm):
    try:
      self._level = int(self._params.get("ExtTnScrollLevel", return_default=True))
      self._applied_ms = int(self._params.get("ExtTnFollowApplied", return_default=True))
    except (TypeError, ValueError):
      self._level, self._applied_ms = -1, -1
    if not 0 <= self._level < 7:
      return "-", "", "scroll"
    if self._applied_ms < 0:
      return f"{self._level + 1}/7", "", "ramping"
    return f"{self._level + 1}/7", "", f"{self._applied_ms / 1000.0:.2f}s"

  def draw(self, rect, size, state):
    self.draw_value(rect, *state)
