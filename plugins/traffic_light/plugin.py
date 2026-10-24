"""Traffic light plugin: reads Tesla's own traffic-control perception (APP_trafficControl), runs
trafficcontrold, and lets plannerd stop for red lights through the final plan arbitrator (tighten only).
Params: TeslaTrafficSignalControlEnabled, TeslaTrafficStopReference, TeslaTrafficControlMaxSpeed."""
import time

from openpilot.tesla.plugin import Plugin
from .final_plan_arbitrator import create_final_plan_arbitrator
from .tesla_observer import TeslaTrafficControlObserver, publish_tesla_traffic_control


def _onroad(started, params, CP) -> bool:
  return started


class TrafficLight(Plugin):
  SERVICES = ("trafficRadarState",)

  def __init__(self, CP, params):
    super().__init__(CP, params)
    self.observer = TeslaTrafficControlObserver()

  def observe_can(self, can_list, now_ns: int) -> list:
    self.observer.update(can_list, now_ns)
    return []

  def publish_state(self, CS_SP) -> None:
    publish_tesla_traffic_control(CS_SP, self.observer.snapshot(time.monotonic_ns()))

  @classmethod
  def processes(cls) -> list:
    from openpilot.system.manager.process import PythonProcess
    return [PythonProcess("trafficcontrold", f"{__package__}.trafficcontrold", _onroad)]

  @classmethod
  def plan_filter(cls, CP, params):
    return create_final_plan_arbitrator(CP, params)
# auto-merge test
