import math
import time
from openpilot.tesla.plugin import Plugin
from .prepare import ready


def onroad(started, params, CP):
  return started


class CurveTarget:
  def __init__(self):
    from openpilot.tesla import plugins
    self.msgs = plugins.PluginMsgs()

  def update(self, sm, v_ego, v_cruise):
    try:
      nav = self.msgs.latest('lean_mici.navigation', 'lean_mici')
      now = time.clock_gettime_ns(getattr(time, 'CLOCK_BOOTTIME', time.CLOCK_MONOTONIC))
      sample, expiry, eof = nav['sample_time_ns'], nav['valid_until_ns'], nav['frame_eof_ns']
      points = nav['target_points']
      if (type(nav['health']) is not int or nav['health'] & 11 != 3 or
          type(sample) is not int or type(expiry) is not int or type(eof) is not int or
          not 0 < sample <= eof <= now < expiry <= sample + 2_000_000_000 or
          not isinstance(points, list) or not 1 <= len(points) <= 8 or
          not math.isfinite(v_ego) or not math.isfinite(v_cruise) or v_ego < 0):
        return None
      elapsed = (now - sample) / 1e9
      distances = [p['distance_m'] for p in points]
      speeds = [p['velocity'] for p in points]
      if (any(type(x) not in (int, float) or not math.isfinite(x) for x in distances + speeds) or
          any(not 0 <= d <= 600 for d in distances) or distances != sorted(distances) or any(v <= 0 for v in speeds)):
        return None
      A_DECEL = 1.0  # Tune this deceleration as route experience becomes available.
      target = min(math.sqrt(v * v + 2 * A_DECEL * max(0, d - v_ego * elapsed)) for d, v in zip(distances, speeds))
      return target if target < v_cruise else None
    except Exception:
      return None


class LeanMici(Plugin):
  @classmethod
  def long_target(cls, CP, params):
    return CurveTarget()

  @classmethod
  def prepare_install(cls):
    return ready()

  @classmethod
  def processes(cls):
    from openpilot.system.manager.process import PythonProcess
    return [PythonProcess('lean_modeld', f'{__package__}.runner', onroad)]
