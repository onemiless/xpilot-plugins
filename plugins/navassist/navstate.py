"""The navigation payload published on pluginMsg kind "nav.state" (JSON for other plugins and the HUD)."""
from __future__ import annotations

MAX_PHONE_LOCATION_AGE_MS = 1_000
MAX_PHONE_LANE_GUIDANCE_AGE_MS = 2_000
MAX_PHONE_LOCATION_ACCURACY_M = 25.0

MANEUVERS = frozenset((
  "none", "straight", "slight_left", "slight_right", "turn_left", "turn_right", "sharp_left", "sharp_right",
  "u_turn_left", "u_turn_right", "keep_left", "keep_right", "merge_left", "merge_right", "exit_left",
  "exit_right", "ramp_left", "ramp_right", "roundabout", "destination", "unknown",
))


def empty_state(reason: str = "noData") -> dict:
  return {"valid": False, "stale": True, "reason": reason, "maneuver": "none", "distanceM": 0.0,
          "nextManeuver": "none", "nextDistanceM": 0.0, "lanes": [], "laneTotalCount": -1,
          "sessionId": "", "routeRevision": 0, "maneuverEventId": 0, "sequence": 0,
          "sourceWallTimeMs": 0, "ageMs": 0.0, "advisorySpeedMps": 0.0, "routeActive": False,
          "source": "", "mode": "idle", "navRouteActive": False}


def _phone_observations_valid(snapshot) -> bool:
  location_age_ms = snapshot.source_wall_time_ms - snapshot.location_observed_at_ms
  return bool(
    snapshot.coordinate_system != "unknown"
    and snapshot.accuracy_m <= MAX_PHONE_LOCATION_ACCURACY_M
    and 0 <= location_age_ms <= MAX_PHONE_LOCATION_AGE_MS
  )


def build(current, now_ns: int, *, oem_nav_route_active: bool = False) -> dict:
  """Control-valid only for a fresh, matched realtime Android/iOS route with guidance."""
  if current is None:
    return empty_state()
  snapshot = current.snapshot
  stale = current.is_stale(now_ns)
  lane_guidance_age_ms = snapshot.source_wall_time_ms - snapshot.lane_guidance_observed_at_ms
  lane_valid = bool(snapshot.lane_guidance_present and 0 <= lane_guidance_age_ms <= MAX_PHONE_LANE_GUIDANCE_AGE_MS)
  control_source_valid = snapshot.source_platform in ("android", "ios") and snapshot.navigation_mode == "realtime"
  phone_observations_valid = _phone_observations_valid(snapshot)
  valid = bool(not stale and control_source_valid and snapshot.route_active and snapshot.route_matched
               and snapshot.location_present and snapshot.guidance_present and phone_observations_valid)
  if stale:
    reason = "stale"
  elif not control_source_valid or not snapshot.route_active:
    reason = "noData"
  elif not snapshot.route_matched:
    reason = "routeUnmatched"
  elif not snapshot.location_present or not snapshot.guidance_present:
    reason = "noData"
  elif not phone_observations_valid:
    reason = "phoneLocalization"
  else:
    reason = "none"
  lanes = []
  if lane_valid:
    for lane in snapshot.lanes:
      lanes.append({"index": lane.index, "allowedActions": lane.allowed_actions,
                    "recommendedActions": lane.recommended_actions, "recommended": lane.recommended})
  return {
    "valid": valid, "stale": stale, "reason": reason,
    "maneuver": snapshot.maneuver, "distanceM": snapshot.maneuver_distance_m,
    "nextManeuver": snapshot.next_maneuver, "nextDistanceM": snapshot.next_maneuver_distance_m,
    "lanes": lanes, "laneTotalCount": snapshot.lane_total_count,
    "sessionId": snapshot.session_id, "routeRevision": snapshot.route_revision,
    "maneuverEventId": snapshot.maneuver_event_id, "sequence": snapshot.sequence,
    "sourceWallTimeMs": snapshot.source_wall_time_ms, "ageMs": current.age_ms(now_ns),
    "advisorySpeedMps": snapshot.advisory_speed_mps or 0.0, "routeActive": snapshot.route_active,
    "source": snapshot.source_platform, "mode": snapshot.navigation_mode,
    "navRouteActive": oem_nav_route_active,
  }
