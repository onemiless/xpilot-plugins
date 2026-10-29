"""Speed limit tile: the limit sunnypilot resolved (longitudinalPlanSP.speedLimit.resolver)."""
from openpilot.common.constants import CV
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.tesla.ui.tile import Tile
from openpilot.tesla.ui.tiles import TileSpec


class SpeedLimitCardTile(Tile):
  SPEC = TileSpec("speed_limit_card", "Speed Limit", sizes=("small", "medium", "wide"), show="always", hz=2, order=20)
  TRANSLATE_TITLE = True

  def state(self, sm):
    limit = sm['longitudinalPlanSP'].speedLimit.resolver.speedLimit if sm.alive['longitudinalPlanSP'] else 0.0
    if limit <= 0:
      return "-", ""
    metric = ui_state.is_metric
    return f"{limit * (CV.MS_TO_KPH if metric else CV.MS_TO_MPH):.0f}", "km/h" if metric else "mph"

  def draw(self, rect, size, state):
    self.draw_value(rect, *state)
