"""Failure modes first:
- UDP/protocol: unknown fields, duplicate JSON keys, wrong schema, non-finite numbers, a lane index twice,
  a stale source wall clock, decreasing route revision, a replayed sequence, or a retired session becomes valid.
- OEM: a single solid/ALC frame never vetoes; two consecutive frames do; a stopped message stops vetoing; a short
  frame is ignored.
- Speed: a late maneuver is rejected for its whole lifetime; the ceiling never asks for a stop; release is bounded;
  stale navigation and driver override release it.
- Intent: a turn request needs a valid directional maneuver inside its lead window; a lane request needs a valid
  topology, no veto, no blind spot, lateral active and the per-event cap.
- Turn signal: only an idle 0x3E9 template with a valid checksum can be cloned; the request bits, reason, counter
  and checksum are rebuilt; no request means no frame."""
from __future__ import annotations

import json
import time

import pytest

from navassist.blocker import LaneChangeBlocker
from navassist.intent import LaneIntentCoordinator, TurnSignalCoordinator, lane_plan
from navassist.oem import ALC_AVAILABLE_ONLY_R, ALC_UNAVAILABLE_SOLID_LANE_LINE, OemLaneSignals
from navassist.settings import NavSettings
from navassist.speed import NavigationSpeedCeiling
from navassist.store import NavAssistStore
from navassist.turnsignal import TurnSignalController, build, checksum, decode, is_idle_template
from navassist.wire import NavAssistProtocolError, parse_snapshot


def snapshot_body(**overrides):
  body = {
    "schemaVersion": 3, "messageType": "navigation_snapshot", "sessionId": "session-1",
    "sequence": 1, "routeRevision": 1, "maneuverEventId": 7, "sourcePlatform": "android",
    "sourceWallTimeMs": int(time.time_ns() // 1_000_000), "validForMs": 500,
    "navigationMode": "realtime", "routeActive": True, "routeMatched": True, "gpsWeak": False,
    "coordinateSystem": "gcj02",
    "location": {"latitude": 31.2, "longitude": 121.4, "accuracyM": 5.0, "bearingDeg": 90.0,
                 "speedKph": 36.0, "observedAtMs": int(time.time_ns() // 1_000_000)},
    "guidance": {"maneuver": "turn_left", "maneuverDistanceM": 120, "nextManeuver": "turn_right",
                 "nextManeuverDistanceM": 400, "observedAtMs": int(time.time_ns() // 1_000_000)},
    "lanes": {"observedAtMs": int(time.time_ns() // 1_000_000), "totalCount": 3,
              "items": [{"index": 0, "allowedActions": ["LEFT"], "recommendedActions": ["LEFT"], "recommended": True}]},
  }
  body.update(overrides)
  return json.dumps(body, separators=(",", ":")).encode()


def test_round_trip_snapshot():
  snap = parse_snapshot(snapshot_body())
  assert snap.session_id == "session-1" and snap.maneuver == "turn_left"
  assert snap.lanes[0].recommended and snap.lane_total_count == 3


@pytest.mark.parametrize("body", [
  b'{"schemaVersion":3,"schemaVersion":3}',
  snapshot_body(schemaVersion=2),
  snapshot_body(coordinateSystem="mars"),
  snapshot_body(lanes={"observedAtMs": 1, "items": [{"index": 1, "allowedActions": [], "recommendedActions": [],
                                                      "recommended": False},
                                                    {"index": 1, "allowedActions": [], "recommendedActions": [],
                                                     "recommended": False}]}),
])
def test_parse_rejects(body):
  with pytest.raises(NavAssistProtocolError):
    parse_snapshot(body)


def test_store_sequence_and_ttl():
  now = [1_000_000_000]
  wall = [int(time.time_ns() // 1_000_000)]
  store = NavAssistStore(clock_ns=lambda: now[0], wall_clock_ms=lambda: wall[0])
  accepted = store.accept(snapshot_body(), "a" * 32)
  assert accepted.snapshot.sequence == 1 and not store.is_stale(now[0])
  with pytest.raises(NavAssistProtocolError):
    store.accept(snapshot_body(sequence=1), "a" * 32)
  store.accept(snapshot_body(sequence=2, routeRevision=2), "a" * 32)
  now[0] += 600_000_000
  assert store.is_stale(now[0])


def _oem(state, now):
  sig = OemLaneSignals()
  data = bytes(((state << 46) >> (8 * i)) & 0xFF for i in range(8))
  sig.update([(now, [(0x39B, data, 1)])], now)
  return sig


def test_oem_staleness_and_explicit_blocks():
  now = time.monotonic_ns()
  sig = _oem(ALC_UNAVAILABLE_SOLID_LANE_LINE, now)
  assert sig.left_explicit_block and sig.right_explicit_block
  stale = _oem(ALC_UNAVAILABLE_SOLID_LANE_LINE, now - 5_000_000_000)
  assert not stale.left_explicit_block  # message age, not memory, is the veto
  sig = _oem(ALC_AVAILABLE_ONLY_R, now)
  assert sig.left_availability_block and not sig.right_availability_block


def test_blocker_needs_two_frames():
  blocker = LaneChangeBlocker()
  oem = {"leftExplicitBlock": True}
  assert blocker.update(oem, {"valid": True})[0] is False
  assert blocker.update(oem, {"valid": True})[0] is True
  for _ in range(6):
    blocked = blocker.update({"leftExplicitBlock": False}, {"valid": True})
  assert blocked[0] is False


def test_speed_late_event_rejected_and_release_bounded():
  ceiling = NavigationSpeedCeiling(NavSettings(turn_speed_kph=18))
  nav = {"valid": True, "stale": False, "maneuver": "turn_left", "distanceM": 4.0,
         "sessionId": "s", "routeRevision": 1, "maneuverEventId": 9}
  assert ceiling.update(nav, v_ego=10.0, v_cruise=15.0, enabled=True, driver_override=False) is None
  # The rejected event never activates, even when its distance grows.
  nav["distanceM"] = 100.0
  assert ceiling.update(nav, v_ego=10.0, v_cruise=15.0, enabled=True, driver_override=False) is None


def test_speed_activates_and_releases():
  ceiling = NavigationSpeedCeiling(NavSettings(turn_speed_kph=18))
  nav = {"valid": True, "stale": False, "maneuver": "turn_left", "distanceM": 60.0,
         "sessionId": "s", "routeRevision": 1, "maneuverEventId": 3}
  assert ceiling.update(nav, v_ego=10.0, v_cruise=15.0, enabled=True, driver_override=False) == pytest.approx(5.0)
  nav["distanceM"] = 0.0
  assert ceiling.update(nav, v_ego=10.0, v_cruise=15.0, enabled=True, driver_override=False) == pytest.approx(5.0)
  released = ceiling.update(nav, v_ego=10.0, v_cruise=15.0, enabled=True, driver_override=True)
  assert released is not None and released > 5.0


def test_turn_coordinator_window():
  turns = TurnSignalCoordinator()
  now = time.monotonic_ns()
  nav = {"valid": True, "sessionId": "s", "routeRevision": 1, "maneuverEventId": 5,
         "maneuver": "turn_left", "distanceM": 80.0}
  out = turns.update(nav, speed_mps=10.0, now_ns=now)
  assert out["signalRequested"] and out["direction"] == "left"
  far = dict(nav, distanceM=500.0, maneuverEventId=6)
  turns.reset()
  assert not turns.update(far, speed_mps=10.0, now_ns=now)["signalRequested"]


def test_lane_plan_maps_only_matching_edge():
  settings = NavSettings(lane_change_enabled=True)
  nav = {"valid": True, "stale": False, "maneuver": "turn_left", "distanceM": 300.0, "maneuverEventId": 1,
         "sessionId": "s", "routeRevision": 1, "laneTotalCount": 2,
         "lanes": [{"index": 0, "recommended": True, "allowedActions": 0, "recommendedActions": 0}]}
  topo = {"valid": True, "visibleLaneCount": 2, "egoLaneIndexFromLeft": 1}
  plan = lane_plan(nav, topo, settings, v_ego=20.0)
  assert plan.direction == "left" and plan.target_lane == 0 and plan.edge_anchored
  topo["visibleLaneCount"] = 3
  plan = lane_plan(nav, topo, settings, v_ego=20.0)
  assert plan.reason in ("edgeFallback", "phoneEdge")


def test_intent_blocks_on_veto():
  lanes = LaneIntentCoordinator()
  nav = {"valid": True, "sessionId": "s", "routeRevision": 1, "maneuverEventId": 1, "maneuver": "turn_left",
         "distanceM": 300.0, "laneTotalCount": 3, "lanes": []}
  topo = {"valid": True, "visibleLaneCount": 3, "egoLaneIndexFromLeft": 2}
  plan = lane_plan(nav, topo, NavSettings(), v_ego=20.0)
  out = lanes.update(nav, plan, now_ns=time.monotonic_ns(), topology_valid=True, left_blocked=True,
                     right_blocked=False, blindspot_left=False, blindspot_right=False, lateral_active=True)
  assert not out["signalRequested"] and out["reason"] == "boundaryVeto"


def _template(turn=0, counter=0):
  data = bytearray(8)
  data[1] = turn
  data[2] = (8 << 1)
  data[6] = counter << 4
  data[7] = checksum(data)
  return bytes(data)


def test_turnsignal_encode():
  template = _template()
  assert is_idle_template(0x3E9, 1, template)
  frame = build(template, "left", 3)
  assert frame[1] == 1 and ((frame[2] >> 1) & 0xF) == 8 and (frame[6] >> 4) == 3
  assert frame[7] == checksum(frame) and decode(frame)["turn"] == 1
  assert build(template, "cancel", 4)[1] == 3
  with pytest.raises(ValueError):
    build(template + b"x", "left", 1)


def test_turnsignal_controller_sends_once_and_cancels():
  now = time.monotonic_ns()
  controller = TurnSignalController()
  controller.observe([(now, [(0x3E9, _template(), 1)])], now)
  intent = {"valid": True, "signalRequested": True, "direction": "left", "sessionId": "s",
            "routeRevision": 1, "requestId": 1}
  cs = type("CS", (), {"leftBlinker": False, "rightBlinker": False, "brakePressed": False})()
  cc = type("CC", (), {"latActive": True})()
  frames = controller.update(intent, cs, cc, now, enabled=True)
  assert len(frames) == 1 and frames[0][0] == 0x3E9 and frames[0][2] == 1
  cs.leftBlinker = True
  assert controller.update(intent, cs, cc, now + 200_000_000, enabled=True) == []
  intent["signalRequested"] = False
  cancel = controller.update(intent, cs, cc, now + 300_000_000, enabled=True)
  assert cancel and cancel[0][1][1] == 3


class _Params:
  def get_bool(self, key, block=False):
    return {"ExtNavAssistLaneChange": True, "ExtNavAssistTurnSignal": True}.get(key, False)

  def get(self, key, block=False, return_default=False):
    return {"ExtNavAssistLookahead": 1000, "ExtNavAssistExitLookahead": 2000,
            "ExtNavAssistSignalLead": 8, "ExtNavAssistTurnSpeed": 18,
            "ExtNavAssistMaxChanges": 5}.get(key)


class _Msgs:
  def __init__(self, data):
    self.data = data

  def fresh(self, kind):
    return {self.data[kind]["source"]: self.data[kind]} if kind in self.data else {}


def test_policy_oem_veto_and_nav_intent():
  from navassist.policy import NavLanePolicy
  policy = NavLanePolicy(_Params())
  policy._msgs = _Msgs({"oem.lanes": {"source": "navassist", "leftExplicitBlock": True}})
  policy._last_poll = -1.0
  assert policy.allow(None, 1, True) is False
  policy._msgs = _Msgs({"lanes.intent": {"source": "navassist", "signalRequested": True, "direction": "left",
                                              "spLaneChangeReady": True, "laneChangeAllowed": True}})
  policy._last_poll = -1.0
  assert policy.allow(None, 1, True) is True
  assert policy.allow(None, 2, True) is False
  policy._msgs = _Msgs({})
  policy._last_poll = -1.0
  assert policy.allow(None, 1, False) is False
  assert policy.allow(None, 1, True) is True
