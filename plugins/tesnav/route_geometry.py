"""Offline route-relative map pose, adapted from sunnypilot reimplement-nav.

Source f7f9a3efae3a6552068201bd9e4e4dbe66aae577, navigation/route.py.
Copyright (c) 2018, Comma.ai, Inc. See THIRD_PARTY_NAVIGATION_LICENSE.txt.
This is local planar geometry, not a lane association or driving permission.
"""
import numpy as np

METERS_PER_DEGREE = 2 * np.pi * 6371008.8 / 360
PROJECTION_WINDOW = (-30., 100.)
HEADING_HALF_WINDOW = 50.


def local_meters(points, origin):
  scale = np.array([METERS_PER_DEGREE * np.cos(np.radians(origin[1])), METERS_PER_DEGREE])
  return (np.asarray(points, dtype=np.float64) - origin) * scale


def nearest_segment(starts, ends, point):
  delta = ends - starts
  fraction = np.clip(((point - starts) * delta).sum(axis=1) / np.maximum((delta * delta).sum(axis=1), 1e-12), 0, 1)
  distances = np.linalg.norm(starts + fraction[:, None] * delta - point, axis=1)
  index = int(distances.argmin())
  return index, float(fraction[index]), float(distances[index])


def cumulative_distance(route):
  (lon1, lat1), (lon2, lat2) = route[:-1].T, route[1:].T
  steps = np.hypot((lon2-lon1) * np.cos(np.radians((lat1+lat2)/2)), lat2-lat1) * METERS_PER_DEGREE
  return np.r_[0., np.cumsum(steps)]


class RouteTracker:
  """One admitted WGS84 route, input ordered (longitude, latitude).

Create a new instance on identity changes or after any route invalidation.
max_offset_m is an offline rejection threshold, not a validated lane/safety
margin. Self-intersections and parallel roads still require source matching.
"""
  def __init__(self, route, *, coordinate_system, max_offset_m=30.):
    if coordinate_system != 'wgs84':
      raise ValueError('Map geometry requires explicitly converted WGS84')
    points = np.array(route, dtype=np.float64, copy=True)
    if (points.ndim != 2 or points.shape[1] != 2 or not 2 <= len(points) <= 20000
        or not np.isfinite(points).all() or np.any(np.abs(points[:, 0]) > 180)
        or np.any(np.abs(points[:, 1]) > 85.05112878)):
      raise ValueError('Invalid longitude/latitude route')
    if np.any(np.abs(np.diff(points[:, 0])) > 180):
      raise ValueError('Antimeridian routes need a different projection')
    points = points[np.r_[True, np.any(np.diff(points, axis=0) != 0, axis=1)]]
    if len(points) < 2 or not np.isfinite(max_offset_m) or not 0 < max_offset_m <= 100:
      raise ValueError('Degenerate route or invalid offset threshold')
    self.route = points
    self.path = local_meters(points, points[0])
    self.distance = cumulative_distance(points)
    self.progress = -np.inf
    self.max_offset_m = max_offset_m

  def project(self, longitude, latitude):
    if (not np.isfinite([longitude, latitude]).all() or abs(longitude) > 180 or abs(latitude) > 85.05112878):
      raise ValueError('Invalid fix')
    if np.isneginf(self.progress):
      near = np.arange(len(self.path)-1)
    else:
      near = np.flatnonzero((self.distance[1:] >= self.progress + PROJECTION_WINDOW[0]) &
                           (self.distance[:-1] <= self.progress + PROJECTION_WINDOW[1]))
    if not len(near):
      raise ValueError('No route segment in the progress window')
    point = local_meters([longitude, latitude], self.route[0])
    index, fraction, offset = nearest_segment(self.path[near], self.path[near+1], point)
    segment = near[index]
    candidate = self.distance[segment] + fraction * (self.distance[segment+1]-self.distance[segment])
    if offset > self.max_offset_m:
      raise ValueError('Fix is outside the route corridor')
    # The reference filters segments by overlap. A long segment can still
    # project far outside that window, so reject rather than teleport progress.
    if np.isfinite(self.progress) and not self.progress-30 <= candidate <= self.progress+100:
      raise ValueError('Fix exceeds the progress window')
    self.progress = max(self.progress, candidate)
    return self.progress

  def pose(self, progress):
    if not np.isfinite(progress):
      raise ValueError('Invalid progress')
    ends = np.clip([progress-HEADING_HALF_WINDOW, progress, progress+HEADING_HALF_WINDOW], 0, self.distance[-1])
    (lon0, lon, lon1), (lat0, lat, lat1) = (np.interp(ends, self.distance, self.route[:, axis]) for axis in (0, 1))
    bearing = np.degrees(np.arctan2((lon1-lon0) * np.cos(np.radians(lat0)), lat1-lat0)) % 360
    return float(lon), float(lat), float(bearing)

  def snap(self, longitude, latitude):
    return self.pose(self.project(longitude, latitude))
