"""TesNav v3 wire types; derived from the supplied NavAssist protocol snapshot."""
from dataclasses import dataclass
import re

SCHEMA_VERSION = 3
MESSAGE_TYPE = "navigation_snapshot"
MAX_BODY_BYTES = 64 * 1024
MAX_VALID_FOR_MS = 2_000
MIN_VALID_FOR_MS = 100
MAX_SOURCE_AGE_MS = 2_000
SOURCE_DELIVERY_GRACE_MS = 500
MAX_SOURCE_FUTURE_SKEW_MS = 1_000
MAX_LANES = 16
MAX_SESSION_ID_LENGTH = 64
MAX_ROAD_NAME_LENGTH = 256
CHECKPOINT_VERSION = 2
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]+$")
APP_KEY_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")

SOURCE_VALUES = frozenset(("android", "ios", "track"))
MODE_VALUES = frozenset(("idle", "route_planned", "realtime", "simulation", "arrived", "recalculating"))
COORDINATE_SYSTEM_VALUES = frozenset(("gcj02", "wgs84", "unknown"))
ROAD_LAYER_VALUES = frozenset(("unknown", "main", "side"))
ROUTE_NOTICE_VALUES = frozenset((
  "none", "restricted_area", "forbidden_area", "road_closed", "congestion", "dispatch", "route_changed",
  "gps_weak", "unknown",
))
MANEUVER_VALUES = frozenset((
  "none", "straight", "slight_left", "slight_right", "turn_left", "turn_right", "sharp_left", "sharp_right",
  "u_turn_left", "u_turn_right", "keep_left", "keep_right", "merge_left", "merge_right", "exit_left",
  "exit_right", "ramp_left", "ramp_right", "roundabout", "destination", "unknown",
))
ROAD_TYPE_VALUES = frozenset((1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 53, 56, 58))

LANE_ACTION_BITS = {
  "STRAIGHT": 1 << 0,
  "LEFT": 1 << 1,
  "RIGHT": 1 << 2,
  "U_TURN": 1 << 3,
  "LEFT_U_TURN": 1 << 3,
  "RIGHT_U_TURN": 1 << 4,
  "BUS": 1 << 5,
  "VARIABLE": 1 << 6,
  "DEDICATED": 1 << 7,
  "TIDAL": 1 << 8,
  "UNKNOWN": 1 << 15,
}

TOP_LEVEL_KEYS = frozenset((
  "schemaVersion", "messageType", "sessionId", "sequence", "routeRevision", "maneuverEventId", "sourcePlatform",
  "sourceWallTimeMs", "validForMs", "navigationMode", "routeActive", "routeMatched", "gpsWeak",
  "coordinateSystem", "location", "guidance", "lanes", "laneChangeSpeechCompletedId",
))
LOCATION_KEYS = frozenset((
  "latitude", "longitude", "accuracyM", "bearingDeg", "speedKph", "observedAtMs", "currentStepIndex",
  "currentLinkIndex", "currentPointIndex",
))
GUIDANCE_KEYS = frozenset((
  "maneuver", "maneuverDistanceM", "nextManeuver", "nextManeuverDistanceM", "currentRoad", "nextRoad",
  "roadClass", "roadType", "advisorySpeedMps", "parallelRoadStatus", "elevatedRoadStatus", "routeNoticeType",
  "routeNoticeDistanceM", "routeNoticeObservedAtMs", "observedAtMs", "turnSignalHold", "turnSignalCountdownS",
))
LANES_KEYS = frozenset(("observedAtMs", "items"))
LANE_ITEM_KEYS = frozenset(("index", "allowedActions", "recommendedActions", "recommended", "routeAvoid"))


class NavAssistProtocolError(ValueError):
  def __init__(self, reason: str, message: str):
    super().__init__(message)
    self.reason = reason


@dataclass(frozen=True)
class LaneGuidance:
  index: int
  allowed_actions: int
  recommended_actions: int
  recommended: bool
  route_avoid: bool


@dataclass(frozen=True)
class NavAssistSnapshot:
  session_id: str
  sequence: int
  route_revision: int
  maneuver_event_id: int
  source_platform: str
  source_wall_time_ms: int
  valid_for_ms: int
  navigation_mode: str
  route_active: bool
  route_matched: bool
  gps_weak: bool
  coordinate_system: str
  location_present: bool
  latitude: float
  longitude: float
  accuracy_m: float
  bearing_deg: float
  speed_kph: float
  location_observed_at_ms: int
  current_step_index: int
  current_link_index: int
  current_point_index: int
  guidance_present: bool
  maneuver: str
  guidance_observed_at_ms: int
  maneuver_distance_m: float
  next_maneuver: str
  next_maneuver_distance_m: float
  advisory_speed_mps: float | None
  road_class: int
  road_type: int
  current_road: str
  next_road: str
  parallel_road_status: str
  elevated_road_status: str
  route_notice_type: str
  route_notice_distance_m: float
  route_notice_observed_at_ms: int
  turn_signal_hold: bool
  turn_signal_countdown_s: int
  lane_guidance_present: bool
  lane_guidance_observed_at_ms: int
  lanes: tuple[LaneGuidance, ...]
  lane_change_speech_completed_id: str = ""
  maneuver_distance_present: bool = False


@dataclass(frozen=True)
class AcceptedSnapshot:
  snapshot: NavAssistSnapshot
  receive_mono_ns: int
  expires_mono_ns: int

  def age_ms(self, now_ns: int) -> float:
    return max(0.0, (now_ns - self.receive_mono_ns) / 1e6)

  def is_stale(self, now_ns: int) -> bool:
    return now_ns > self.expires_mono_ns


