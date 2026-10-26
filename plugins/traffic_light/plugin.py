"""Traffic light plugin: reads Tesla's own traffic-control perception (APP_trafficControl), runs
trafficcontrold, and lets plannerd stop for red lights through the final plan arbitrator (tighten only).
Params: TeslaTrafficSignalControlEnabled, TeslaTrafficStopReference, TeslaTrafficControlMaxSpeed."""
import time

from openpilot.tesla import plugins
from openpilot.tesla.plugin import Plugin
from .final_plan_arbitrator import create_final_plan_arbitrator
from .tesla_observer import TeslaTrafficControlObserver, traffic_control_payload


def _onroad(started, params, CP) -> bool:
  return started


class TrafficLight(Plugin):
  def __init__(self, CP, params):
    super().__init__(CP, params)
    self.observer = TeslaTrafficControlObserver()
    self.last_publish_ns = 0

  def observe_can(self, can_list, now_ns: int) -> list:
    self.observer.update(can_list, now_ns)
    return []

  def publish_state(self, CS_SP) -> None:
    now_ns = time.monotonic_ns()
    # The source budget is shared with trafficcontrold and plannerd.
    if now_ns - self.last_publish_ns >= 50_000_000:
      self.last_publish_ns = now_ns
      plugins.send_msg(self, "traffic.observation", traffic_control_payload(self.observer.snapshot(now_ns)), valid_for_ms=750)

  @classmethod
  def processes(cls) -> list:
    from openpilot.system.manager.process import PythonProcess
    return [PythonProcess("trafficcontrold", f"{__package__}.trafficcontrold", _onroad)]

  @classmethod
  def plan_filter(cls, CP, params):
    return create_final_plan_arbitrator(CP, params)
