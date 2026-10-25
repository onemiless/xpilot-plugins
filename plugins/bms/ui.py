"""Battery tile: charge, pack voltage and current, and power (negative while charging or regenerating)."""
from openpilot.tesla.ui.tile import Tile
from openpilot.tesla.ui.tiles import TileSpec


class BmsTile(Tile):
  SPEC = TileSpec("bms", "Battery", sizes=("small", "medium", "wide"), services=("carStateSP",), show="always", hz=2, order=90)

  def state(self, sm):
    b = sm["carStateSP"].teslaBattery
    if not b.available:
      return None
    return f"{b.soc:.0f}", f"{b.voltage:.0f} V  {b.current:.0f} A  {b.voltage * b.current / 1000:.1f} kW"

  def draw(self, rect, size, state):
    self.draw_value(rect, *((state[0], "%", state[1]) if state else ("--",)))
