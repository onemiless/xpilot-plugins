"""Strict, pure v3 snapshot parser; source provenance is in README.md."""
import json
import math
import re
from typing import Any
from .wire import *  # frozen wire constants and dataclasses

def _reject(reason: str, message: str) -> None:
  raise NavAssistProtocolError(reason, message)


def _object(value: Any, field: str, allowed_keys: frozenset[str]) -> dict[str, Any]:
  if not isinstance(value, dict):
    _reject("malformed", f"{field} must be an object")
  unknown = set(value) - allowed_keys
  if unknown:
    _reject("malformed", f"{field} contains unknown fields: {sorted(unknown)}")
  return value


def _required(obj: dict[str, Any], field: str) -> Any:
  if field not in obj:
    _reject("malformed", f"missing required field {field}")
  return obj[field]


def _boolean(obj: dict[str, Any], field: str) -> bool:
  value = _required(obj, field)
  if not isinstance(value, bool):
    _reject("malformed", f"{field} must be boolean")
  return value


def _optional_boolean(obj: dict[str, Any], field: str, default: bool = False) -> bool:
  if field not in obj:
    return default
  return _boolean(obj, field)


def _integer(obj: dict[str, Any], field: str, minimum: int, maximum: int, *, default: int | None = None) -> int:
  value = obj.get(field, default)
  if value is None or isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
    _reject("malformed", f"{field} must be an integer in [{minimum}, {maximum}]")
  return value


def _optional_integer(obj: dict[str, Any], field: str, minimum: int, maximum: int, *, default: int = -1) -> int:
  if field not in obj:
    return default
  return _integer(obj, field, minimum, maximum)


def _number(obj: dict[str, Any], field: str, minimum: float, maximum: float, *, default: float | None = None) -> float:
  value = obj.get(field, default)
  if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
    _reject("malformed", f"{field} must be numeric")
  result = float(value)
  if not math.isfinite(result) or not minimum <= result <= maximum:
    _reject("malformed", f"{field} must be finite and in [{minimum}, {maximum}]")
  return result


def _string(obj: dict[str, Any], field: str, *, default: str | None = None, max_length: int = 128) -> str:
  value = obj.get(field, default)
  if value is None or not isinstance(value, str) or len(value) > max_length:
    _reject("malformed", f"{field} must be a string no longer than {max_length}")
  return value


def _enum(obj: dict[str, Any], field: str, values: frozenset[str], *, default: str | None = None) -> str:
  value = _string(obj, field, default=default)
  if value not in values:
    _reject("malformed", f"{field} has unsupported value {value!r}")
  return value


def _lane_action_mask(value: Any, field: str) -> int:
  if not isinstance(value, list) or len(value) > 16:
    _reject("malformed", f"{field} must be an action list")
  mask = 0
  seen: set[str] = set()
  for item in value:
    if not isinstance(item, str) or item not in LANE_ACTION_BITS:
      _reject("malformed", f"{field} contains unsupported action {item!r}")
    if item in seen:
      _reject("malformed", f"{field} contains duplicate action {item!r}")
    seen.add(item)
    mask |= LANE_ACTION_BITS[item]
  return mask


def _documented_road_type(obj: dict[str, Any]) -> int:
  if "roadType" not in obj:
    return -1
  value = _integer(obj, "roadType", 1, 255)
  if value not in ROAD_TYPE_VALUES:
    _reject("malformed", f"roadType has unsupported value {value}")
  return value


def _optional_road_name(obj: dict[str, Any], field: str) -> str:
  if field not in obj:
    return ""
  value = _string(obj, field, max_length=MAX_ROAD_NAME_LENGTH)
  if not value:
    _reject("malformed", f"{field} must not be empty")
  return value


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
  result: dict[str, Any] = {}
  for key, value in pairs:
    if key in result:
      _reject("malformed", f"duplicate JSON field {key}")
    result[key] = value
  return result


def parse_snapshot(body: bytes) -> NavAssistSnapshot:
  if not body or len(body) > 8192:
    _reject("malformed", f"body must contain 1..{8192} bytes")
  try:
    raw = json.loads(
      body.decode("utf-8"),
      parse_constant=lambda value: _reject("malformed", f"invalid number {value}"),
      object_pairs_hook=_reject_duplicate_keys,
    )
  except (UnicodeDecodeError, json.JSONDecodeError) as error:
    _reject("malformed", f"invalid JSON: {error}")
  top = _object(raw, "snapshot", TOP_LEVEL_KEYS)
  speech_id = _string(top, "laneChangeSpeechCompletedId", max_length=32) if "laneChangeSpeechCompletedId" in top else ""
  if speech_id and re.fullmatch(r"[0-9a-f]{32}", speech_id) is None:
    _reject("malformed", "invalid laneChangeSpeechCompletedId")

  if _integer(top, "schemaVersion", 0, 1_000) != SCHEMA_VERSION:
    _reject("malformed", "unsupported schemaVersion")
  if _string(top, "messageType") != MESSAGE_TYPE:
    _reject("malformed", "unsupported messageType")
  session_id = _string(top, "sessionId", max_length=MAX_SESSION_ID_LENGTH)
  if not session_id or SESSION_ID_PATTERN.fullmatch(session_id) is None:
    _reject("malformed", "sessionId has invalid characters")

  location_present = isinstance(top.get("location"), dict)
  guidance_present = isinstance(top.get("guidance"), dict)
  lane_guidance_present = isinstance(top.get("lanes"), dict)
  location = _object(top.get("location", {}), "location", LOCATION_KEYS)
  guidance = _object(top.get("guidance", {}), "guidance", GUIDANCE_KEYS)
  lane_block = _object(top.get("lanes", {}), "lanes", LANES_KEYS)
  lane_items = _required(lane_block, "items") if lane_guidance_present else []
  if not isinstance(lane_items, list) or len(lane_items) > MAX_LANES:
    _reject("malformed", f"lanes.items must contain no more than {MAX_LANES} lanes")

  lanes: list[LaneGuidance] = []
  seen_indexes: set[int] = set()
  for position, raw_lane in enumerate(lane_items):
    lane = _object(raw_lane, f"lanes.items[{position}]", LANE_ITEM_KEYS)
    index = _integer(lane, "index", 0, 31)
    if index in seen_indexes:
      _reject("malformed", f"duplicate lane index {index}")
    seen_indexes.add(index)
    lanes.append(LaneGuidance(
      index=index,
      allowed_actions=_lane_action_mask(_required(lane, "allowedActions"), f"lanes.items[{position}].allowedActions"),
      recommended_actions=_lane_action_mask(
        _required(lane, "recommendedActions"), f"lanes.items[{position}].recommendedActions",
      ),
      recommended=_boolean(lane, "recommended"),
      route_avoid=_optional_boolean(lane, "routeAvoid"),
    ))

  advisory_speed = guidance.get("advisorySpeedMps")
  if advisory_speed is not None:
    advisory_speed = _number(guidance, "advisorySpeedMps", 0.0, 60.0)
    if advisory_speed <= 0.0:
      _reject("malformed", "advisorySpeedMps must be greater than zero")

  latitude = _number(location, "latitude", -90.0, 90.0) if location_present else 0.0
  longitude = _number(location, "longitude", -180.0, 180.0) if location_present else 0.0
  accuracy_m = _number(location, "accuracyM", 0.0, 200.0) if location_present else 200.0
  bearing_deg = _number(location, "bearingDeg", 0.0, 360.0) if location_present else 0.0
  speed_kph = _number(location, "speedKph", 0.0, 300.0) if location_present else 0.0
  location_observed_at_ms = _integer(location, "observedAtMs", 0, 2**63 - 1) if location_present else 0
  guidance_observed_at_ms = _integer(guidance, "observedAtMs", 0, 2**63 - 1) if guidance_present else 0
  maneuver = _enum(guidance, "maneuver", MANEUVER_VALUES) if guidance_present else "none"
  turn_signal_hold = _optional_boolean(guidance, "turnSignalHold")
  turn_signal_countdown_s = _optional_integer(guidance, "turnSignalCountdownS", 0, 3_600)
  if turn_signal_hold and turn_signal_countdown_s < 0:
    _reject("malformed", "turnSignalHold requires turnSignalCountdownS")
  lane_observed_at_ms = _integer(lane_block, "observedAtMs", 0, 2**63 - 1) if lane_guidance_present else 0

  return NavAssistSnapshot(
    session_id=session_id,
    lane_change_speech_completed_id=speech_id,
    sequence=_integer(top, "sequence", 1, 2**63 - 1),
    route_revision=_integer(top, "routeRevision", 0, 2**63 - 1),
    maneuver_event_id=_integer(top, "maneuverEventId", 0, 2**63 - 1),
    source_platform=_enum(top, "sourcePlatform", SOURCE_VALUES),
    source_wall_time_ms=_integer(top, "sourceWallTimeMs", 0, 2**63 - 1),
    valid_for_ms=_integer(top, "validForMs", MIN_VALID_FOR_MS, MAX_VALID_FOR_MS),
    navigation_mode=_enum(top, "navigationMode", MODE_VALUES),
    route_active=_boolean(top, "routeActive"),
    route_matched=_optional_boolean(top, "routeMatched"),
    gps_weak=_boolean(top, "gpsWeak"),
    coordinate_system=_enum(top, "coordinateSystem", COORDINATE_SYSTEM_VALUES),
    location_present=location_present,
    latitude=latitude,
    longitude=longitude,
    accuracy_m=accuracy_m,
    bearing_deg=bearing_deg,
    speed_kph=speed_kph,
    location_observed_at_ms=location_observed_at_ms,
    current_step_index=_optional_integer(location, "currentStepIndex", 0, 1_000_000),
    current_link_index=_optional_integer(location, "currentLinkIndex", 0, 1_000_000),
    current_point_index=_optional_integer(location, "currentPointIndex", 0, 10_000_000),
    guidance_present=guidance_present,
    maneuver=maneuver,
    guidance_observed_at_ms=guidance_observed_at_ms,
    maneuver_distance_m=float(_optional_integer(guidance, "maneuverDistanceM", 0, 100_000, default=0)),
    maneuver_distance_present=guidance_present and guidance.get("maneuverDistanceM") is not None,
    next_maneuver=_enum(guidance, "nextManeuver", MANEUVER_VALUES, default="none"),
    next_maneuver_distance_m=float(_optional_integer(guidance, "nextManeuverDistanceM", 0, 100_000, default=0)),
    advisory_speed_mps=advisory_speed,
    road_class=_optional_integer(guidance, "roadClass", 0, 10),
    road_type=_documented_road_type(guidance),
    current_road=_optional_road_name(guidance, "currentRoad"),
    next_road=_optional_road_name(guidance, "nextRoad"),
    parallel_road_status=_enum(guidance, "parallelRoadStatus", ROAD_LAYER_VALUES, default="unknown"),
    elevated_road_status=_enum(guidance, "elevatedRoadStatus", ROAD_LAYER_VALUES, default="unknown"),
    route_notice_type=_enum(guidance, "routeNoticeType", ROUTE_NOTICE_VALUES, default="none"),
    route_notice_distance_m=float(_optional_integer(guidance, "routeNoticeDistanceM", 0, 100_000, default=0)),
    route_notice_observed_at_ms=_optional_integer(guidance, "routeNoticeObservedAtMs", 0, 2**63 - 1, default=0),
    turn_signal_hold=turn_signal_hold,
    turn_signal_countdown_s=turn_signal_countdown_s,
    lane_guidance_present=lane_guidance_present,
    lane_guidance_observed_at_ms=lane_observed_at_ms,
    lanes=tuple(lanes),
  )


