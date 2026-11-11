"""Optional turn-speed ceiling. Override/loss after use latches off for this drive."""
import math
import time


class SpeedPolicy:
  def __init__(self):
    self.applied, self.latched = False, False

  def update(self, nav, v_ego, v_cruise, *, active, override=False):
    if self.applied and (override or not active or nav is None):
      self.latched = True
    if self.latched or override or not active or nav is None:
      return None
    distance = float(nav.maneuverDistance)
    if not all(math.isfinite(x) for x in (distance, v_ego, v_cruise)) or not 0 <= distance <= 2000:
      return None
    if nav.maneuverType not in ('turn', 'roundabout'):
      return None
    modifier = nav.maneuverModifier
    turn_speed = (6. if nav.maneuverType == 'roundabout' else 2.5 if modifier == 'uturn'
                  else 8. if modifier.startswith('slight ') else 3.5 if modifier.startswith('sharp ')
                  else 5. if modifier in ('left', 'right') else None)
    if turn_speed is None:
      return None
    target = math.sqrt(turn_speed**2 + 2 * 1.2 * max(0., distance - 10.))
    if target >= v_cruise or target > v_ego:
      return None
    self.applied = True
    return target


class NavigationSpeed:
  def __init__(self, params):
    self.params, self.policy = params, SpeedPolicy()

  def update(self, sm, v_ego, v_cruise):
    started = bool(sm['deviceState'].started)
    if not started:
      self.policy = SpeedPolicy()
      return None
    enabled = self.params.get_bool('ExtTesNavSlowdown')
    healthy = all(sm.alive[s] and sm.valid[s] for s in ('navInstruction', 'carState', 'carControl', 'deviceState'))
    age = time.monotonic_ns() - sm.logMonoTime['navInstruction']
    nav = sm['navInstruction'] if enabled and healthy and 0 <= age <= 300_000_000 else None
    cs, cc = sm['carState'], sm['carControl']
    return self.policy.update(nav, v_ego, v_cruise, active=bool(enabled and healthy and cc.longActive),
                              override=bool(cs.gasPressed or cs.brakePressed or cs.steeringPressed))
