"""Models tile while openpilot's bundled model is in use."""
from openpilot.tesla.ui.tile import Tile
from openpilot.tesla.ui.tiles import TileSpec


class OpModelsTile(Tile):
  SPEC = TileSpec("op_models", "Models", sizes=("small", "medium", "wide"), show="offroad", order=4)
  TRANSLATE_TITLE = True

  def state(self, sm):
    return None

  def draw(self, rect, size, state):
    self.draw_value(rect, "openpilot", sub="bundled model")
