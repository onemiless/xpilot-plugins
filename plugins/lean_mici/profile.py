"""L@617e00670a123da12809a65796985aae05710a08 common-model layout.
Walnut V3 qualification is deliberately closed until same-input phone proof exists.
"""
import numpy as np
from openpilot.selfdrive.modeld.parse_model_outputs import Parser

SLICES = {'lane_lines': (0, 528), 'lane_lines_prob': (528, 536), 'road_edges': (536, 800),
          'meta': (800, 855), 'desire_pred': (855, 887), 'pose': (887, 899),
          'wide_from_device_euler': (899, 905), 'road_transform': (905, 917),
          'plan': (917, 1907), 'lead': (1907, 2051), 'lead_prob': (2051, 2054), 'desire_state': (2054, 2062)}


def decode(raw):
  if raw.shape != (2066,) or not np.isfinite(raw).all() or not np.any(raw):
    raise ValueError('expected finite nonzero 2066-float common-model output')
  heads = Parser().parse_outputs({name: raw[start:end][None].copy() for name, (start, end) in SLICES.items()})
  heads['action'] = raw[2062:2064][None].copy()
  heads['action_stds'] = np.exp(np.clip(raw[2064:2066][None], -np.inf, 11))
  heads['raw_pred'] = raw.copy()
  return heads


def admit(local, remote):
  # shortcut: Walnut V3 numeric/control qualification is missing, open only after same-input and device gates pass.
  return False
