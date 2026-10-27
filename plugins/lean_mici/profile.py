"""L@617e00670a123da12809a65796985aae05710a08 common-model layout.
v4 retains these 2066 common-model heads; navigation is not consumed.
"""
import numpy as np
from openpilot.selfdrive.modeld.parse_model_outputs import Parser
from openpilot.selfdrive.modeld.constants import ModelConstants, Plan
from openpilot.selfdrive.controls.lib.drive_helpers import MAX_CURVATURE

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


NEAR = np.array(ModelConstants.T_IDXS) <= 2.


def control_values(heads):
  plan = heads['plan'][0, NEAR]
  speed = np.linalg.norm(plan[:, Plan.VELOCITY], axis=1)
  curvature = plan[:, Plan.ORIENTATION_RATE][:, 2] / np.maximum(speed, 1.)
  action = heads['action'][0, :2] if 'action' in heads else np.array([curvature[0] * max(1., speed[0])**2, plan[0, Plan.ACCELERATION][0]])
  return {'speed': speed, 'curvature': curvature, 'action': action,
          'acceleration': plan[:, Plan.ACCELERATION][:, 0], 'lead_probability': heads['lead_prob'][0],
          'lead_distance': float(heads['lead'][0, 0, 0, 0])}


def reject_reason(local, remote):
  r, l = control_values(remote), control_values(local)
  if np.linalg.norm(remote['plan'][0, 0, Plan.POSITION]) > 5:
    return 'physical_position'
  # S1 gives admit only heads; use the local first-plan speed as the v_ego proxy.
  if abs(r['action'][0]) > min(6., MAX_CURVATURE * max(1., l['speed'][0])**2) or not -8 <= r['action'][1] <= 5:
    return 'physical_action'
  if np.max(r['speed']) > 80 or np.min(remote['plan'][0, NEAR, Plan.VELOCITY][:, 0]) < -2:
    return 'physical_speed'
  if np.min(r['acceleration']) < -8 or np.max(r['acceleration']) > 5 or np.max(np.abs(remote['plan'][0, NEAR, Plan.ACCELERATION])) > 10:
    return 'physical_acceleration'
  if np.max(np.abs(r['curvature'])) > MAX_CURVATURE or np.max(np.abs(r['curvature']) * r['speed']**2) > 6:
    return 'physical_curvature'
  if np.linalg.norm(remote['pose'][0, :3]) > 80 or np.max(np.abs(remote['pose'][0, 3:])) > 2:
    return 'physical_pose'
  if np.min(r['lead_probability']) < 0 or np.max(r['lead_probability']) > 1:
    return 'physical_lead_probability'
  if r['lead_probability'][0] > .5 and not 0 <= r['lead_distance'] <= 300:
    return 'physical_lead_distance'
  return ''


def admit(local, remote):
  return not reject_reason(local, remote)
