"""Battery (BMS) plugin: decodes the BMS frames on the vehicle bus in card and publishes them as pluginMsg bms.state
for the tile in ui.py. Reads only; needs the vehicle-bus harness (nothing arrives without it)."""
import time

from opendbc.car.tesla.values import CANBUS
from openpilot.tesla import plugins
from openpilot.tesla.plugin import Plugin

# Layouts match opendbc tesla_modely_hw4_perception.dbc (BO_ 306 BMS_hvBusStatus, BO_ 658 BMS_socStatus). packCurrent is
# negated as in the community Model 3 DBC so discharging reads positive. ponytail: sign and bus (vehicle) come from the community
# Model 3 DBC and are not yet seen on a car; switch to a CANParser on that DBC once more BMS signals are needed
HV_STATUS, SOC_STATUS = 0x132, 0x292
STALE_NS = 2_000_000_000


def bits(data: bytes, start: int, size: int, signed: bool = False) -> int:
  v = (int.from_bytes(data, "little") >> start) & ((1 << size) - 1)
  return v - (1 << size) if signed and v >> (size - 1) else v


def decode(frames: dict, now_ns: int) -> tuple[float, float, float] | None:
  """(soc %, pack V, pack A) from the latest {address: (mono_time, data)}, or None when a frame is missing or stale."""
  if any(a not in frames or not 0 <= now_ns - frames[a][0] <= STALE_NS for a in (HV_STATUS, SOC_STATUS)):
    return None
  hv, soc = frames[HV_STATUS][1], frames[SOC_STATUS][1]
  return bits(soc, 10, 10) * 0.1, bits(hv, 0, 16) * 0.01, bits(hv, 16, 16, signed=True) * -0.1


class Bms(Plugin):

  def __init__(self, CP, params):
    super().__init__(CP, params)
    self.frames: dict[int, tuple[int, bytes]] = {}
    self.last_publish_ns = 0

  def observe_can(self, can_list, now_ns: int) -> list:
    for mono_time, frames in can_list:
      for address, data, source in frames:
        if source == CANBUS.vehicle and address in (HV_STATUS, SOC_STATUS):
          self.frames[address] = (mono_time, data)
    return []

  def publish_state(self, CS_SP) -> None:
    now_ns = time.monotonic_ns()
    if now_ns - self.last_publish_ns < 500_000_000:
      return
    self.last_publish_ns = now_ns
    values = decode(self.frames, now_ns)
    payload = {"available": values is not None}
    ttl_ms = 2000
    if values is not None:
      payload.update(zip(("soc", "voltage", "current"), values, strict=True))
      ttl_ms = max(1, (STALE_NS - (now_ns - min(t for t, _ in self.frames.values()))) // 1_000_000)
    plugins.send_msg(self, "bms.state", payload, valid_for_ms=ttl_ms)
