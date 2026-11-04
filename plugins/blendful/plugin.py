"""Blendful (sunnypilot #2073, alpha) as a lat_correction: upstream adds Blendful's curvature to the model action in
modeld; here the same algorithm (blendful.py, unchanged) reads modelV2 in controlsd and adds it to SP's desired curvature."""
import time

import numpy as np

from openpilot.tesla import plugins
from openpilot.tesla.plugin import Plugin

from .blendful import Blendful

STALE_S = 0.2  # no call this long (lateral was off): start fresh instead of resuming an old correction


def model_output(m):
  """Rebuild the parts of the raw model output Blendful reads from a modelV2 message; None if the shapes are off."""
  if len(m.laneLines) != 4 or len(m.laneLineStds) != 4 or len(m.laneLineProbs) != 4 or len(m.position.x) != 33:
    return None
  lines, stds = np.zeros((1, 4, 33, 2)), np.ones((1, 4, 33, 2))
  probs, plan = np.zeros((1, 8)), np.zeros((1, 33, 3))
  for i, line in enumerate(m.laneLines):
    if len(line.y) != 33:
      return None
    lines[0, i, :, 0] = line.y
  # ponytail: modelV2 carries each line's std at x=0 only, so it stands in for every distance; publish the full stds if this matters
  stds[0, :, :, 0] = np.array(m.laneLineStds)[:, None]
  probs[0, 1::2] = m.laneLineProbs
  plan[0, :, 0], plan[0, :, 1] = m.position.x, m.position.y
  return {'lane_lines': lines, 'lane_lines_stds': stds, 'lane_lines_prob': probs, 'plan': plan}


class Correction:
  def __init__(self):
    self.bf, self.k, self.last = Blendful(), 0.0, 0.0

  def update(self, sm, v_ego: float, curvature: float):
    now = time.monotonic()
    if now - self.last > STALE_S:
      self.bf, self.k = Blendful(), 0.0
    self.last = now
    if sm.updated['modelV2']:  # 20 Hz like upstream; the correction holds between model frames
      m = sm['modelV2']
      out = model_output(m)
      turning = sm.valid.get('modelDataV2SP', False) and str(sm['modelDataV2SP'].laneTurnDirection) != 'none'
      active = out is not None and str(m.meta.laneChangeState) == 'off' and not turning
      self.k = self.bf.update(out, v_ego, active, curvature)
      plugins.send_msg(BlendfulPlugin, "blendful.state", {"trust": self.bf.trust, "l_prob": self.bf.l_prob, "r_prob": self.bf.r_prob,
                                                         "lane_width": self.bf.lane_width.x}, valid_for_ms=300)
    return curvature + self.k


class BlendfulPlugin(Plugin):
  NAME = "blendful"
  SERVICES = ("modelDataV2SP",)

  @classmethod
  def lat_correction(cls, CP, params):
    return Correction()
