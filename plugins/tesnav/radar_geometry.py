"""Model-corridor target association; independent of radar model or driver."""
import math

def lane_leads(radar, model, *, left_neighbor=True, right_neighbor=True, all_targets=False):
  """Nearest measured target per model corridor; None list means unknown geometry.

  Radar y is left-positive, model y is right-positive. Interpolate each model
  boundary at target range, without extrapolating beyond the visible corridor.
  """
  if len(model.laneLines) != 4:
    return None
  # An absent outer lane must not veto evaluation of the other side. Keep
  # both ego boundaries, plus only the outer boundaries of existing neighbors.
  lanes = [1] + ([0] if left_neighbor else []) + ([2] if right_neighbor else [])
  indices = sorted({i for lane in lanes for i in (lane, lane+1)})
  lines = model.laneLines
  for index in indices:
    line = lines[index]
    if len(line.x) < 2 or len(line.x) != len(line.y):
      return None
    if not all(math.isfinite(v) for v in (*line.x, *line.y)):
      return None
    if any(line.x[i] <= line.x[i-1] for i in range(1, len(line.x))):
      return None
  leads = [[], [], []]
  for point in radar.points:
    d, y, v = float(point.dRel), -float(point.yRel), float(point.vRel)
    if not all(math.isfinite(value) for value in (d, y, v)):
      return None
    if d <= 0 or d > 100:
      continue
    # Same radar-to-model origin convention as controls/radard.py.
    model_x = d + 1.52
    # Beyond the model's ego corridor there is no supported lane assignment.
    # Do not let an out-of-horizon roadside track invalidate an observed side.
    if any(not lines[index].x[0] <= model_x <= lines[index].x[-1] for index in (1, 2)):
      continue
    bounds = {}
    for index in indices:
      line = lines[index]
      if not line.x[0] <= model_x <= line.x[len(line.x)-1]:
        bounds[index] = None
        continue
      for i in range(1, len(line.x)):
        if model_x <= line.x[i]:
          ratio = (model_x - line.x[i-1]) / (line.x[i] - line.x[i-1])
          bounds[index] = line.y[i-1] + ratio * (line.y[i] - line.y[i-1])
          break
    # These boundaries only associate radar tracks with a corridor. Crossing
    # permission comes from the C3 visual/0x399 gate, not a lane-width rule.
    if any(b is not None and math.isclose(y, b, rel_tol=0., abs_tol=1e-6) for b in bounds.values()):
      return None  # A target exactly on a boundary has no lane assignment.
    for lane in lanes:
      if bounds[lane] is None or bounds[lane+1] is None:
        if (lane == 0 and y < bounds[1]) or (lane == 2 and y > bounds[2]):
          return None  # The selected side has a target beyond its visible outer line.
        continue
      if bounds[lane] < y < bounds[lane+1]:
        if not point.deprecated.measured:
          return None
        leads[lane].append(point)
  return leads if all_targets else [min(targets, key=lambda point: point.dRel, default=None) for targets in leads]

def side_lead_unsafe(lead, speed_mps):
  """Adjacent forward target check from CarrotPilot SideState, using normalized relative velocity."""
  if lead is None:
    return False
  distance = lead.dRel
  relative_speed = lead.vRel
  if not all(math.isfinite(value) for value in (distance, relative_speed, speed_mps)) or speed_mps < 0.:
    return True
  if distance <= 5.:
    return True
  min_gap = min(12., max(6., speed_mps * .30))
  closing_speed = max(0., -relative_speed)
  if closing_speed > .5:
    ttc_limit = 2. + min(speed_mps, 15.) / 15. if speed_mps < 15. else 3. + min(speed_mps - 15., 15.) / 30.
    if distance < max(min_gap + 6., speed_mps * 1.2) and distance / closing_speed < ttc_limit:
      return True
    if distance + relative_speed * 1.5 < min_gap:
      return True
  # Apply the existing speed-dependent envelope to every adjacent target,
  # including one pulling away. A positive vRel is not proof of space to merge.
  merge_gap = max(min_gap + 6., speed_mps * 1.2)
  return min(distance, distance + relative_speed * 1.5) < merge_gap
