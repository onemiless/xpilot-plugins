"""sunnypilot AccelBoostSP (#2089, openpilot/sunnypilot/selfdrive/controls/lib/accel_boost.py) without its param polling:
the host's enable switch replaces AccelBoostSP. Copyright (c) 2021-, sunnypilot and contributors, MIT License."""
import numpy as np

DT_MDL = 0.05
ACCEL_BOOST_MIN_SPEED = 1.0
ACCEL_BOOST_MAX = (0.4, 0.25, 0.1)  # aggressive, standard, relaxed, m/s^2
ACCEL_BOOST_BUILD, ACCEL_BOOST_RELEASE = 0.2, 1.0  # m/s^3
BRAKE_PROB = (0.1, 0.3)  # the model's brake press in 4 s: it wants to slow
GAS_PROB = (0.6, 0.8)  # predicts a human gas press at this hypothesis at about 23%
CURVE_LAT_ACCEL = (1.5, 2.5)


def boost_max(personality) -> float:
  return ACCEL_BOOST_MAX[int(personality)]  # LongitudinalPersonality: aggressive 0, standard 1, relaxed 2


class AccelBoost:
  def __init__(self):
    self.boost = 0.0
    self.total_boost = 0.0

  def update(self, sm, output_a_target_e2e, output_a_target_mpc, a_cruise):
    model = sm['modelV2']
    brake_probs = model.meta.disengagePredictions.brakePressProbs
    gas_probs = model.meta.disengagePredictions.gasPressProbs
    if not sm['selfdriveState'].enabled or len(brake_probs) < 2 or len(gas_probs) < 2:
      self.boost = self.total_boost = 0.0
      return output_a_target_e2e

    want = max(float(np.interp(brake_probs[1], BRAKE_PROB, [1.0, 0.0])), float(np.interp(gas_probs[1], GAS_PROB, [0.0, 1.0])))
    want = 0.0 if model.action.shouldStop else want
    want *= float(np.interp(abs(model.action.desiredCurvature) * sm['carState'].vEgo ** 2, CURVE_LAT_ACCEL, [1.0, 0.0]))
    target = want * boost_max(sm['selfdriveState'].personality)
    self.boost = float(np.clip(target, self.boost - ACCEL_BOOST_RELEASE * DT_MDL, self.boost + ACCEL_BOOST_BUILD * DT_MDL))
    speed_scale = np.interp(sm['carState'].vEgo, [ACCEL_BOOST_MIN_SPEED, 2 * ACCEL_BOOST_MIN_SPEED], [0.0, 1.0])
    self.total_boost = float(self.boost * speed_scale * np.interp(output_a_target_e2e, [-0.7, -0.2, 5.0], [0.0, 1.0, 1.0], right=0.0))
    return output_a_target_e2e + self.total_boost
