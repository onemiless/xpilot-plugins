"""Navigation tile consumes only declared, fresh navigation messages."""
import time
from openpilot.system.ui.lib.multilang import tr
from openpilot.tesla.ui.tile import Tile
from openpilot.tesla.ui.tiles import TileSpec


class TesNavTile(Tile):
  SPEC = TileSpec('tesnav', 'TesNav Navigation', sizes=('small', 'medium', 'wide'),
                  services=('navInstruction',), show='always', hz=2, order=61)

  def state(self, sm):
    age = time.monotonic_ns() - sm.logMonoTime['navInstruction']
    if not sm.alive['navInstruction'] or not sm.valid['navInstruction'] or not 0 <= age <= 500_000_000:
      return (tr('Waiting for live phone navigation'),)
    nav = sm['navInstruction']
    recommended = [str(i + 1) for i, lane in enumerate(nav.lanes) if lane.active]
    lines = [f'{nav.maneuverDistance:.0f} m - {tr(nav.maneuverPrimaryText)}', nav.maneuverSecondaryText]
    if recommended:
      lines.append(tr('Recommended lanes: {lanes}').format(lanes=', '.join(recommended)))
    return tuple(line for line in lines if line)

  def draw(self, rect, size, state):
    self.draw_lines(rect, list(state), size=36)
