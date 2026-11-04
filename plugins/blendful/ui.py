"""Blendful lane-center line (sunnypilot #2073 BlendfulRenderer): cyan, drawn on the road while Blendful trusts the lines."""
import numpy as np
import pyray as rl

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.lib.shader_polygon import draw_polygon
from openpilot.tesla import plugins
from openpilot.tesla.ui.plugin_ui import RoadOverlay

from .blendful import LANE_WIDTH_CLIP

COLOR = (0, 200, 255)
HALF_WIDTH = 0.15  # m


def lane_center(left, right, stds, st):
  """Variance-weighted center of the two ego lane lines (Nx3, y right), each pulled half a lane width inward."""
  if st["l_prob"] * st["r_prob"] > 0.5:
    hw = np.clip(right[:, 1] - left[:, 1], *LANE_WIDTH_CLIP) / 2
  else:
    hw = st["lane_width"] / 2
  l_var, r_var = stds[1] ** 2 / max(st["l_prob"], 1e-4), stds[2] ** 2 / max(st["r_prob"], 1e-4)
  center = left.copy()
  center[:, 1] = (r_var * (left[:, 1] + hw) + l_var * (right[:, 1] - hw)) / (l_var + r_var)
  return center


class BlendfulLine(RoadOverlay):
  def __init__(self):
    self.msgs, self.st, self.stds = plugins.PluginMsgs(), None, None
    self.alpha = FirstOrderFilter(0.0, 0.3, 1 / gui_app.target_fps)

  def update(self, sm):
    self.st, self.stds = self.msgs.latest("blendful.state", "blendful"), list(sm['modelV2'].laneLineStds)
    self.alpha.update(1.0 if self.st and self.st["trust"] > 0 else 0.0)

  def draw(self, road):
    if not self.st or self.st["trust"] <= 0.05 or len(self.stds) != 4:
      return
    lines = road.lane_points()
    if len(lines[1]) == 0 or len(lines[1]) != len(lines[2]):
      return
    pts = road.polygon(lane_center(lines[1], lines[2], self.stds, self.st), HALF_WIDTH)
    if pts.size:
      a = int(np.clip(self.st["trust"], 0.2, 1.0) * 255 * self.alpha.x)
      draw_polygon(road.rect, pts, rl.Color(*COLOR, a))
