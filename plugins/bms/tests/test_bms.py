"""P25: the bms plugin decodes 0x132/0x292 right (current sign, scales), drops stale and other-bus frames,
and publishes finite-lived bms.state data without writing cereal."""
import time
import types

from openpilot.tesla import plugins

from ..plugin import HV_STATUS, SOC_STATUS, Bms, decode

# 400.00 V, current raw -1234 * -0.1 = 123.4 A discharging; SOC raw 765 at bit 10 = 76.5 %
HV = (40000 | ((-1234 & 0xFFFF) << 16)).to_bytes(8, "little")
SOC = (765 << 10).to_bytes(8, "little")
NOW = 10_000_000_000


def test_decode():
  got = decode({HV_STATUS: (NOW, HV), SOC_STATUS: (NOW, SOC)}, NOW)
  assert got is not None
  assert all(abs(a - b) <= 1e-6 for a, b in zip(got, (76.5, 400.0, 123.4), strict=True)), got


def test_decode_rejects_stale_frame():
  assert decode({HV_STATUS: (NOW - 3_000_000_000, HV), SOC_STATUS: (NOW, SOC)}, NOW) is None


def test_observe_and_publish(monkeypatch):
  sent = []
  monkeypatch.setattr(plugins, "send_msg", lambda *args, **kw: sent.append((args, kw)))
  bms = Bms(types.SimpleNamespace(carFingerprint="x"), None)
  bms.observe_can([(NOW, [(HV_STATUS, HV, 0), (SOC_STATUS, SOC, 1)])], NOW)
  assert HV_STATUS not in bms.frames and SOC_STATUS in bms.frames, sorted(bms.frames)
  bms.publish_state(object())
  assert not sent[-1][0][2]['available']
  bms.frames = {HV_STATUS: (time.monotonic_ns(), HV), SOC_STATUS: (time.monotonic_ns(), SOC)}
  monkeypatch.setattr(time, 'monotonic_ns', lambda: NOW + 1_000_000_000)
  bms.frames = {HV_STATUS: (NOW, HV), SOC_STATUS: (NOW, SOC)}
  bms.last_publish_ns = 0
  bms.publish_state(object())
  assert sent[-1][0][2] == {'available': True, 'soc': 76.5, 'voltage': 400.0, 'current': 123.4}
  assert 0 < sent[-1][1]['valid_for_ms'] <= 1000
