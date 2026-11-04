"""Map tile: speed limit, road name and next maneuver from the map interface
(liveMapDataSP from any map provider, navInstruction from any navigation provider)."""
from openpilot.common.constants import CV
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.multilang import tr
from openpilot.tesla.ui.tile import Tile
from openpilot.tesla.ui.tiles import TileSpec


class MapTile(Tile):
  SPEC = TileSpec("map", "Map", sizes=("small", "medium", "wide"), services=("liveMapDataSP", "navInstruction"), show="always", hz=2, order=60)

  def state(self, sm):
    lines = []
    if sm.alive['liveMapDataSP']:
      m = sm['liveMapDataSP']
      factor = CV.MS_TO_KPH if ui_state.is_metric else CV.MS_TO_MPH
      if m.speedLimitValid:
        lines.append(tr("Limit {}").format(f"{m.speedLimit * factor:.0f}"))
      if m.roadName:
        lines.append(m.roadName)
    if sm.alive['navInstruction'] and sm['navInstruction'].maneuverPrimaryText:
      nav = sm['navInstruction']
      lines.append(f"{nav.maneuverDistance:.0f} m {nav.maneuverPrimaryText}")
    return tuple(lines) or (tr("No map data"),)

  def draw(self, rect, size, state):
    self.draw_lines(rect, list(state), size=36)
