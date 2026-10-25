"""IP tile: the address of the default route and the network type."""
import socket

from openpilot.tesla.ui.tile import Tile
from openpilot.tesla.ui.tiles import TileSpec


def ip_address() -> str:
  """Source address the kernel picks for an outside route; connecting a UDP socket sends nothing."""
  try:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
      s.connect(("8.8.8.8", 53))
      return s.getsockname()[0]
  except OSError:
    return "offline"


class IpTile(Tile):
  SPEC = TileSpec("ip", "IP", sizes=("medium", "wide", "small"), show="always", hz=0.2, order=80)

  def state(self, sm):
    return ip_address(), str(sm["deviceState"].networkType).replace("NetworkType.", "")

  def draw(self, rect, size, state):
    self.draw_lines(rect, list(state))
