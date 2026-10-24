"""Visible lane topology from modelV2 lane lines (no image classifier): count and ego index only.

Marking types stay unknown: the plugin never claims solid/dashed from geometry. Tesla's own 0x39B/0x238 verdicts
(oem.lanes) supply the boundary veto when present."""
from __future__ import annotations

import math

PROB_FLOOR = 0.20
SAMPLE_X_M = 10.0
STALE_NS = 500_000_000


def _xy(point) -> tuple[float, float]:
  try:
    return float(point.x), float(point.y)
  except (AttributeError, TypeError):
    return float(point[0]), float(point[1])


def _y_at_x(lane, x_m: float) -> float | None:
  """Model lane points are (x, y) in metres, y left-positive; linear interpolation."""
  try:
    points = [_xy(p) for p in lane]
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
  except (TypeError, IndexError, ValueError):
    return None
  if len(xs) != len(ys) or len(xs) < 2:
    return None
  for i in range(len(xs) - 1):
    x0, x1 = xs[i], xs[i + 1]
    if (x0 - x_m) * (x1 - x_m) <= 0 and x1 != x0:
      t = (x_m - x0) / (x1 - x0)
      return ys[i] + t * (ys[i + 1] - ys[i])
  return None


def from_model(model, now_ns: int, model_mono_ns: int) -> dict:
  """visibleLaneCount / egoLaneIndexFromLeft / lanesLeft/right; valid=false when the model is stale or unreadable."""
  out = {"valid": False, "stale": True, "visibleLaneCount": 0, "egoLaneIndexFromLeft": -1,
         "lanesLeftOfEgo": 0, "lanesRightOfEgo": 0, "boundaryCount": 0, "frameId": -1, "ageMs": 0.0}
  if model is None:
    return out
  age_ns = max(0, now_ns - int(model_mono_ns))
  out.update(stale=age_ns > STALE_NS, ageMs=age_ns / 1e6)
  try:
    probs = [float(p) for p in model.laneLineProbs]
    lanes = list(model.laneLines)
    frame_id = int(model.frameId)
  except (AttributeError, TypeError, ValueError):
    return out
  if len(probs) != len(lanes) or not probs:
    return out
  out["frameId"] = frame_id
  ys = []
  for lane, prob in zip(lanes, probs):
    if prob < PROB_FLOOR:
      continue
    y = _y_at_x(lane, SAMPLE_X_M)
    if y is None or not math.isfinite(y):
      continue
    ys.append(y)
  ys.sort()
  out["boundaryCount"] = len(ys)
  out["visibleLaneCount"] = max(0, len(ys) - 1)
  # Lane lines are ordered left to right; the ego lane sits between the lines around y=0.
  ego = 0
  for y in ys:
    if y < 0:
      ego += 1
  out["egoLaneIndexFromLeft"] = ego if ys else -1
  out["lanesLeftOfEgo"] = ego
  out["lanesRightOfEgo"] = max(0, len(ys) - 1 - ego)
  out["valid"] = bool(not out["stale"] and len(ys) >= 2)
  return out
