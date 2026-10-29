"""Clock tile: local time and date."""
import time

from openpilot.tesla.ui.tile import Tile
from openpilot.tesla.ui.tiles import TileSpec


class ClockTile(Tile):
  SPEC = TileSpec("clock", "Clock", sizes=("small", "medium", "wide"), show="always", hz=1, order=70)
  TRANSLATE_TITLE = True

  def state(self, sm):
    return time.strftime("%H:%M"), time.strftime("%a %d %b")  # ponytail: device local time zone, add a zone setting if it shows UTC

  def draw(self, rect, size, state):
    self.draw_value(rect, state[0], sub=state[1])
