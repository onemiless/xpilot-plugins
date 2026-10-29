"""Car status tile: cluster speed, gear, blinkers and blind spots (validates carState-only tiles)."""
from openpilot.common.constants import CV
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.tesla.ui.tile import Tile
from openpilot.tesla.ui.tiles import TileSpec


class CarTile(Tile):
  SPEC = TileSpec("car", "Car", sizes=("small", "medium", "wide"), show="always", hz=10, order=50)
  TRANSLATE_TITLE = True

  def state(self, sm):
    cs = sm['carState']
    speed = cs.vEgoCluster * (CV.MS_TO_KPH if ui_state.is_metric else CV.MS_TO_MPH)
    blink = ("<" if cs.leftBlinker else " ") + (">" if cs.rightBlinker else " ")
    spots = ("L" if cs.leftBlindspot else "-") + ("R" if cs.rightBlindspot else "-")
    return f"{speed:.0f} {'km/h' if ui_state.is_metric else 'mph'}", f"{str(cs.gearShifter)[:1].upper()}  {blink}  {spots}"

  def draw(self, rect, size, state):
    self.draw_lines(rect, list(state))
