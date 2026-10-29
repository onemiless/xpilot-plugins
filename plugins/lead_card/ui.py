"""Lead tile: distance and relative speed of the lead car from radarState.leadOne."""
from openpilot.common.constants import CV
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.multilang import tr
from openpilot.tesla.ui.tile import Tile
from openpilot.tesla.ui.tiles import TileSpec


class LeadCardTile(Tile):
  SPEC = TileSpec("lead_card", "Lead", sizes=("small", "medium", "wide"), show="always", hz=5, order=30)
  TRANSLATE_TITLE = True

  def state(self, sm):
    lead = sm['radarState'].leadOne
    if not sm.alive['radarState'] or not lead.present:
      return "-", "", tr("no lead")
    metric = ui_state.is_metric
    return f"{lead.dRel:.0f}", "m", f"{lead.vRel * (CV.MS_TO_KPH if metric else CV.MS_TO_MPH):+.0f} {'km/h' if metric else 'mph'}"

  def draw(self, rect, size, state):
    self.draw_value(rect, *state)
