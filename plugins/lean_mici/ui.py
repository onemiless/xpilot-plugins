"""Phone navigation, displayed only while fresh."""
import pyray as rl

from openpilot.system.ui.lib.application import FontWeight, TextAlignmentVertical
from openpilot.system.ui.widgets.label import gui_label
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
    if state[3] and state[1] in (26, 27):
      instruction += f' (exit {state[3]})'
    if state[2] in names:
      instruction += f', then {names[state[2]].lower()}'
    distance = '-' if state[4] == 0xffffffff else (f'{round(state[4] / 10) * 10} m' if state[4] < 1000 else f'{state[4] / 1000:.1f} km')
    eta = '' if state[6] == 0xffffffff else f' · {(state[6] + 59) // 60} min remaining'
    return (state[1], distance, instruction + eta)

  def draw(self, rect, size, state):
    if len(state) == 1:
      self.draw_lines(rect, list(state), size=36)
      return
    maneuver, distance, detail = state
    left = maneuver in (3, 6, 13, 14, 15, 16, 19, 21, 24)
    right = maneuver in (2, 5, 9, 10, 11, 12, 18, 20, 23)
    flip = -1 if left else 1
    shape = [(-flip*9, 14), (-flip*9, -4), (-flip*2, -9), (flip*9, -9), (flip*3, -15), (flip*9, -9), (flip*3, -3)] if left or right else [(0, 14), (0, -12), (-6, -5), (0, -12), (6, -5)]
    points = [rl.Vector2(rect.x + 31 + px, rect.y + rect.height / 2 + py) for px, py in shape]
    for a, b in zip(points[:-1], points[1:], strict=True):
      rl.draw_line_ex(a, b, 5, rl.WHITE)
    for point in points:
      rl.draw_circle_v(point, 2.5, rl.WHITE)
    gui_label(rl.Rectangle(rect.x + 60, rect.y, rect.width - 60, rect.height / 2), distance, 36, rl.WHITE, FontWeight.BOLD,
              alignment_vertical=TextAlignmentVertical.TOP)
    gui_label(rl.Rectangle(rect.x + 60, rect.y + rect.height / 2, rect.width - 60, rect.height / 2), detail, 22, rl.WHITE, FontWeight.MEDIUM,
              alignment_vertical=TextAlignmentVertical.TOP)
