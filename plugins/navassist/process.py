"""navassistd: phone navigation ingress, lane topology from modelV2, and pluginMsg publication.

One publisher for pluginMsg kinds nav.state / lanes.topology / lanes.intent / oem.lanes. The lane-change policy,
the speed ceiling and the HUD are consumers in other processes."""
from __future__ import annotations

import time

from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.common.realtime import Ratekeeper
from openpilot.common.swaglog import cloudlog
from openpilot.tesla import plugins

from .blocker import LaneChangeBlocker
from .intent import LaneIntentCoordinator, TurnSignalCoordinator, build_intent
from .navstate import build as build_nav_state
from .oem import OemLaneSignals
from .phone import PhoneBlindspotAssist
from .settings import read as read_settings
from .store import NavAssistStore
from .topology import from_model
from .udp import NavAssistUDPServer

PUBLISH_HZ = 20
REPLAY_CHECKPOINT_PATH = "/dev/shm/navassist_replay_state.json"


class _Publisher:
  """Minimal plugin identity for plugins.send_msg in this process (the host sets these on the class in-process)."""
  NAME = "navassist"
  CLAIMS = ("msg:nav.state", "msg:lanes.topology", "msg:lanes.intent", "msg:oem.lanes")


def _can_list(sm) -> list:
  """Convert messaging can events to the card can_list shape: [(mono_time_ns, [(address, data, bus), ...]), ...]."""
  if not sm.seen.get("can", False):
    return []
  out = []
  for event in sm["can"]:
    frames = []
    for frame in event.can:
      frames.append((int(frame.address), bytes(frame.dat), int(frame.src)))
    out.append((int(event.logMonoTime), frames))
  return out


def main() -> None:
  params = plugins.view(Params())
  settings = read_settings(params)
  store = NavAssistStore(checkpoint_path=REPLAY_CHECKPOINT_PATH)
  udp = NavAssistUDPServer(("0.0.0.0", 4213), store)
  import threading
  threading.Thread(target=udp.serve_forever, name="navassist-udp", daemon=True).start()
  cloudlog.warning("navassistd UDP receiver online on port 4213")

  pm = messaging.PubMaster(["pluginMsg"])
  sm = messaging.SubMaster(["can", "modelV2", "carState", "carControl"], poll="modelV2")
  oem = OemLaneSignals()
  blocker = LaneChangeBlocker()
  turns = TurnSignalCoordinator()
  lanes = LaneIntentCoordinator(max_changes=settings.max_lane_changes)
  phone = PhoneBlindspotAssist()
  phone_t = 0.0
  phone_states = None
  rk = Ratekeeper(PUBLISH_HZ)
  next_oem = 0
  next_topology = 0
  next_intent = 0
  try:
    while True:
      sm.update(0)
      now_ns = time.monotonic_ns()
      if sm.seen.get("can", False):
        oem.update(_can_list(sm), now_ns)
      model = sm["modelV2"] if sm.seen.get("modelV2", False) else None
      model_ns = sm.logMonoTime["modelV2"] if sm.seen.get("modelV2", False) else 0
      topology = from_model(model, now_ns, model_ns)
      current = store.current(now_ns)
      nav = build_nav_state(current, now_ns, oem_nav_route_active=oem.nav_route_active)

      if now_ns >= next_oem:
        next_oem = now_ns + 200_000_000
        plugins.send_msg(pm, _Publisher, "oem.lanes", oem.payload(now_ns), 1500)
      if now_ns >= next_topology:
        next_topology = now_ns + 100_000_000
        plugins.send_msg(pm, _Publisher, "lanes.topology", topology, 700)
      if now_ns >= next_intent:
        next_intent = now_ns + 100_000_000
        now = time.monotonic()
        if now - phone_t >= 0.1:
          phone_t = now
          phone_states = phone.read(now_ns)
        left_phone = bool(phone_states and phone.enabled and phone_states[0].blocked)
        right_phone = bool(phone_states and phone.enabled and phone_states[1].blocked)
        left_blocked, right_blocked, _reasons = blocker.update(
          oem.payload(now_ns), topology, phone_left=left_phone, phone_right=right_phone)
        cs = sm["carState"] if sm.seen.get("carState", False) else None
        cc = sm["carControl"] if sm.seen.get("carControl", False) else None
        v_ego = float(cs.vEgo) if cs is not None else 0.0
        intent = build_intent(nav, topology, settings=settings, v_ego=v_ego, now_ns=now_ns,
                              left_blocked=left_blocked, right_blocked=right_blocked,
                              blindspot_left=bool(cs.leftBlindspot) if cs is not None else False,
                              blindspot_right=bool(cs.rightBlindspot) if cs is not None else False,
                              lateral_active=bool(getattr(cc, "latActive", False)),
                              turns=turns, lanes=lanes)
        plugins.send_msg(pm, _Publisher, "lanes.intent", intent, 1200)
      plugins.send_msg(pm, _Publisher, "nav.state", nav, 1500)
      rk.keep_time()
  finally:
    udp.shutdown()
    udp.server_close()


class _Runtime:
  """Lazy reference used by plugin.py; avoids importing messaging at plugin discovery."""
  @staticmethod
  def start() -> None:
    main()


if __name__ == "__main__":
  main()
