"""ARS408 receiver and calibrated motion input through declared host/Panda APIs."""
from openpilot.tesla.plugin import Plugin


class ARS408(Plugin):
  SERVICES = ('deviceMotion', 'extrinsicsCalibration')

  def __init__(self, CP, params):
    super().__init__(CP, params)
    from openpilot.selfdrive.locationd.helpers import PoseCalibrator
    from .transmitter import ARS408Transmitter
    self._calibrator = PoseCalibrator()
    self._motion_cache = {}
    self._next_motion_ns = 0
    self.motion_transmitter = ARS408Transmitter(
      None, enabled=True, mount_inverted=params.get_bool('ExtARS408Inverted'))

  def card_context(self, context, now_ns):
    from .motion import radar_yaw_rate_context
    # Clear first: a malformed context must never preserve an older good sample.
    self.motion_transmitter.update_yaw_rate(None, 0)
    self.motion_transmitter.update_yaw_rate(
      *radar_yaw_rate_context(context, now_ns, self._calibrator, self._motion_cache))

  def control_sends(self, CS, CC, now_ns):
    if now_ns < self._next_motion_ns:
      return []
    frames = self.motion_transmitter.update(0, CS, now_ns)
    if frames:
      self._next_motion_ns = now_ns + 50_000_000
    return frames

  @classmethod
  def radar_interface(cls, CP, CP_SP, params):
    if CP.brand != 'tesla':
      return None
    from .interface import ARS408RadarInterface
    from .motion_health import MotionAwareRadar
    radar = ARS408RadarInterface(CP, CP_SP, mount_inverted=params.get_bool('ExtARS408Inverted'))
    # Selecting this external receiver is independent of factory radar availability.
    # Do not mutate CP/CP_SP, their flags, car port code or the fallback receiver.
    radar.radar_off_can = False
    return MotionAwareRadar(radar)
