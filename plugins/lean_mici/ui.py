"""Phone navigation, displayed only while fresh."""
from openpilot.tesla import plugins
from openpilot.tesla.ui.tile import Tile
from openpilot.tesla.ui.tiles import TileSpec


class NavigationTile(Tile):
  SPEC = TileSpec("lean_navigation", "Navigation", sizes=("small", "medium", "wide"), show="always", hz=2, order=61)

  def __init__(self):
    self.msgs = plugins.PluginMsgs()

  def state(self, sm):
    nav = self.msgs.latest('lean_mici.navigation', 'lean_mici')
    if nav is None:
      return ('-',)
    if not nav['health'] & 1:
      return ('Phone location unavailable',)
    if not nav['health'] & 2 or nav['state'] is None:
      return ('-',)
    state = nav['state']
    names = {1: 'Start', 2: 'Start right', 3: 'Start left', 4: 'Destination', 5: 'Destination right', 6: 'Destination left',
             7: 'Continue', 8: 'Continue', 9: 'Slight right', 10: 'Turn right', 11: 'Sharp right',
             12: 'U-turn right', 13: 'U-turn left', 14: 'Sharp left', 15: 'Turn left', 16: 'Slight left',
             17: 'Ramp straight', 18: 'Ramp right', 19: 'Ramp left', 20: 'Exit right', 21: 'Exit left',
             22: 'Keep straight', 23: 'Keep right', 24: 'Keep left', 25: 'Merge', 26: 'Roundabout', 27: 'Exit roundabout'}
    instruction = names.get(state[1], '-' if state[1] == 255 else f'Maneuver {state[1]}')
    distance = '' if state[4] == 0xffffffff else f" · {state[4]} m"
    eta = () if state[6] == 0xffffffff else (f"{(state[6] + 59) // 60} min remaining",)
    return (instruction + distance, *eta)

  def draw(self, rect, size, state):
    self.draw_lines(rect, list(state), size=36)
