"""Navigation admission and presentation, independent of sockets and cereal."""
import json
import os
from pathlib import Path

from .protocol import parse_snapshot


class NavigationStore:
  def __init__(self, checkpoint=None):
    self.path = Path(checkpoint) if checkpoint else None
    self.owner, self.sessions, self.current_key = '', {}, ''
    self.latest, self.expires, self.wall = None, 0., 0
    if self.path and self.path.exists():
      if self.path.stat().st_size > 32768:
        raise ValueError('Invalid navigation checkpoint')
      state = json.loads(self.path.read_text())
      self.owner, self.sessions = state['owner'], state['sessions']
      self.current_key = state['current']
      if not isinstance(self.sessions, dict) or len(self.sessions) > 64:
        raise ValueError('Invalid navigation sessions')

  def accept(self, body, peer, wall_ms, mono):
    value = parse_snapshot(body)
    age = wall_ms - value.source_wall_time_ms
    if not -1000 <= age <= min(2000, value.valid_for_ms):
      raise ValueError('Navigation source clock expired')
    if self.owner and peer != self.owner:
      raise ValueError('Another phone owns navigation until the next device boot')
    key = value.session_id
    previous = self.sessions.get(key)
    if previous and (key != self.current_key or value.sequence <= previous[0] or value.route_revision < previous[1]):
      raise ValueError('Replayed or retired navigation session')
    if self.current_key and key != self.current_key and self.latest and mono <= self.expires:
      raise ValueError('Navigation session still leased')
    if key not in self.sessions and len(self.sessions) >= 64:
      raise ValueError('Navigation session limit reached; park and reset checkpoint')
    self.owner, self.current_key = peer, key
    self.sessions[key] = [value.sequence, value.route_revision]
    if self.path:
      self.path.parent.mkdir(parents=True, exist_ok=True)
      tmp = self.path.with_suffix('.tmp')
      tmp.write_text(json.dumps({'owner': self.owner, 'current': key, 'sessions': self.sessions}))
      os.replace(tmp, self.path)
    self.latest, self.wall = value, wall_ms
    self.expires = mono + max(0, value.valid_for_ms - max(0, age)) / 1000
    return {'messageType': 'navassist_udp_ack', 'schemaVersion': 3,
            'sessionId': key, 'sequence': value.sequence}

  def current(self, wall_ms, mono):
    if self.latest is None or mono > self.expires or wall_ms < self.wall:
      return None
    return self.latest


def clean(value):
  return ''.join(c for c in value if c.isprintable())[:256]


def instruction(value, wall_ms):
  if (value is None or not value.route_active or not value.route_matched or value.gps_weak
      or value.navigation_mode != 'realtime' or value.coordinate_system not in ('gcj02', 'wgs84')
      or not value.location_present or not value.guidance_present or not value.maneuver_distance_present
      or value.accuracy_m > 30 or value.maneuver in ('none', 'unknown')
      or not 0 <= wall_ms - value.location_observed_at_ms <= 2000
      or not 0 <= wall_ms - value.guidance_observed_at_ms <= 2000):
    return None
  maneuver = value.maneuver
  modifier = 'left' if maneuver.endswith('left') else 'right' if maneuver.endswith('right') else 'straight'
  if maneuver.startswith('slight_'):
    modifier = 'slight ' + modifier
  if maneuver.startswith('sharp_'):
    modifier = 'sharp ' + modifier
  if maneuver.startswith('u_turn_'):
    modifier = 'uturn'
  kind = ('arrive' if maneuver == 'destination' else 'roundabout' if maneuver == 'roundabout'
          else 'fork' if maneuver.startswith(('keep_', 'exit_', 'ramp_')) else 'merge' if maneuver.startswith('merge_')
          else 'continue' if maneuver == 'straight' else 'turn')
  lanes = []
  if value.lane_guidance_present and 0 <= wall_ms - value.lane_guidance_observed_at_ms <= 2000:
    for lane in sorted(value.lanes, key=lambda lane: lane.index):
      directions = [name for bit, name in ((1, 'straight'), (2, 'left'), (4, 'right')) if lane.allowed_actions & bit]
      active = lane.recommended and not lane.route_avoid
      chosen = [name for bit, name in ((1, 'straight'), (2, 'left'), (4, 'right')) if lane.recommended_actions & bit]
      lanes.append({'directions': directions, 'active': active,
                    'activeDirection': chosen[0] if len(chosen) == 1 else 'none'})
  return {'maneuverPrimaryText': clean(maneuver.replace('_', ' ')),
          'maneuverSecondaryText': clean(value.next_road or value.current_road),
          'maneuverDistance': value.maneuver_distance_m, 'maneuverType': kind, 'maneuverModifier': modifier,
          'lanes': lanes}
