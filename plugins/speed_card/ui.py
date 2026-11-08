"""Speed tile: ego speed as the car's cluster shows it, and the set speed."""
from openpilot.common.constants import CV
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.multilang import tr
from openpilot.tesla.ui.tile import Tile
from openpilot.tesla.ui.tiles import TileSpec

SET_SPEED_NA = 255  # vCruiseCluster (km/h) when cruise is not set


class SpeedCardTile(Tile):
  SPEC = TileSpec("speed_card", "Speed", sizes=("small", "medium", "wide"), show="always", hz=10, order=10)

  def state(self, sm):
    cs = sm['carState']
    metric = ui_state.is_metric
    v_cruise = cs.vCruiseCluster
    return (f"{max(0.0, cs.vEgoCluster) * (CV.MS_TO_KPH if metric else CV.MS_TO_MPH):.0f}", "km/h" if metric else "mph",
            tr("SET {}").format(f"{v_cruise * (1 if metric else CV.KPH_TO_MPH):.0f}" if 0 < v_cruise < SET_SPEED_NA else "-"))

  def draw(self, rect, size, state):
    self.draw_value(rect, *state)
