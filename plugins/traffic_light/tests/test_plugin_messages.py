"""Message-path E2E, failure cases before implementation:
- CAN observation/BMS still writes retired cereal fields or loses raw bytes in JSON.
- card floods the shared 100 Hz source budget; stale CAN is renewed indefinitely.
- Target/diagnostics bypass the broker, change final-plan behavior, or lack claims/finite TTL.
- Wrong sources, expired target/diagnostics/BMS, or broker loss keep control/display data alive.

Real AF_UNIX -> Broker -> cereal -> PluginMsgs -> controller/filter/tiles; manager/Params
are fixtures. Broker authentication/restart/concurrent-process coverage is the system E2E.
"""
import json
import os
from pathlib import Path
import shutil
import select
import sys
import tempfile
import time
from types import SimpleNamespace as ns
import uuid

from opendbc.can import CANPacker
from openpilot.cereal import messaging
from openpilot.tesla import manifest, plugins, plugin_msgd
from ..plugin import TrafficLight
from ..radar_state import TrafficRadarSource
from ..controller import TrafficControlConfig, TrafficControlMode
from ..final_plan_arbitrator import FinalPlanArbitrator
from .test_final_plan_arbitrator import base_plan, fake_sm, plan_output
from bms.plugin import Bms, HV_STATUS, SOC_STATUS
from bms.tests.test_bms import HV, SOC


def test_can_broker_controller_filter_tiles(monkeypatch, tmp_path):
  prefix = 'x58-' + uuid.uuid4().hex[:10]
  ipc = Path('/tmp' if sys.platform == 'darwin' else '/dev/shm') / ('msgq_' + prefix)
  ipc.mkdir()
  monkeypatch.setenv('OPENPILOT_PREFIX', prefix)
  socket_dir = tempfile.TemporaryDirectory(prefix='x58-', dir='/tmp')
  monkeypatch.setattr(plugin_msgd, 'socket_path', lambda: socket_dir.name + '/socket')
  monkeypatch.setitem(sys.modules, 'openpilot.common.params', ns(Params=lambda: ns(get=lambda *a: None)))
  classes = (TrafficLight, Bms)
  for cls, name in zip(classes, ('traffic_light', 'bms'), strict=True):
    m = manifest.parse(Path(__file__).parents[2] / name / 'plugin.json')
    monkeypatch.setattr(cls, 'NAME', name)
    monkeypatch.setattr(cls, 'CLAIMS', m.claims)
  broker = plugin_msgd.Broker()
  broker.pids = {os.getpid(): 'card'}
  broker.publishers = {cls.NAME: cls for cls in classes}
  broker.drive = 1
  broker.sm.alive['managerState'] = True
  reader = plugins.PluginMsgs()
  from ..ui import TrafficControlRenderer
  from bms.ui import BmsTile
  tile, battery = TrafficControlRenderer(), BmsTile()
  reader.fresh('_ready')
  CP = ns(carFingerprint='x', longitudinalActuatorDelay=.2)
  traffic, bms = TrafficLight(CP, None), Bms(CP, None)

  def receive(kind, source):
    broker.receive()
    deadline = time.monotonic() + 2
    while (payload := reader.latest(kind, source)) is None:
      assert time.monotonic() < deadline, (kind, source)
      time.sleep(.002)
    return payload

  try:
    now = time.monotonic_ns()
    frame = CANPacker('tesla_modely_hw4_perception').make_can_msg('APP_trafficControl', 2, {
      'APP_tcControlType': 3, 'APP_tcControlDistance': 42, 'APP_tcControlLightState': 1})
    traffic.observe_can([(now, [frame])], now)
    traffic.publish_state(object())
    observation = receive('traffic.observation', 'traffic_light')
    assert observation['rawPayload'] == frame[1].hex()
    assert observation['frameMonoTime'] == now
    # Repeated card hooks must not exhaust traffic_light's budget shared with two other processes.
    for _ in range(100):
      traffic.publish_state(object())
    assert broker.seq['traffic_light'] == 1
    assert not select.select([broker.sock], [], [], 0)[0]
    bms.observe_can([(now, [(HV_STATUS, HV, 1), (SOC_STATUS, SOC, 1)])], now)
    bms.publish_state(object())
    state = receive('bms.state', 'bms')
    assert state['available'] and state['soc'] == 76.5
    assert battery.state(None)[0] == '76'

    arbitrator = FinalPlanArbitrator(CP)
    arbitrator.msgs.fresh("_ready")
    source = TrafficRadarSource(TrafficControlConfig(mode=TrafficControlMode.stopGo))
    sm = fake_sm(v_ego=8)
    target = source.update(sm, now, reader.latest('traffic.observation', 'traffic_light'))
    assert target.valid
    assert plugins.send_msg(TrafficLight, 'traffic.target', {'valid': target.valid, **target.trafficRadarState.to_dict()}, valid_for_ms=350)
    receive('traffic.target', 'traffic_light')
    assert arbitrator._traffic(sm, now) is not None
    assert arbitrator._traffic(sm, now - 1) is None
    assert arbitrator._traffic(sm, now + 351_000_000) is None
    assert not source.update(sm, now, None).valid

    # Exercise the real plan-filter publish sink and final diagnostic publisher.
    sent = []
    sink = arbitrator.publisher(ns(send=lambda s, m: sent.append(s)), sm, now)
    plan = messaging.new_message('longitudinalPlan')
    for key, value in vars(base_plan()).items():
      setattr(plan.longitudinalPlan, key, value)
    sink.send('longitudinalPlan', plan)
    sink.send('longitudinalPlanSP', messaging.new_message('longitudinalPlanSP'))
    diag = receive('traffic.plan', 'traffic_light')
    assert sent == ['longitudinalPlan', 'longitudinalPlanSP']
    assert diag['finalATarget'] == plan.longitudinalPlan.aTarget
    assert tile.state(None)[0].visible
    assert all(0 < ttl <= 2000 for _, ttl, _ in reader.last.values())
    assert {'msg:traffic.observation', 'msg:traffic.target', 'msg:traffic.plan'} <= set(TrafficLight.CLAIMS)
    assert 'msg:bms.state' in Bms.CLAIMS

    # Poison another source: only the declared producer is consumed.
    stamp = time.monotonic_ns()
    arbitrator.msgs.last[('other', 'traffic.target')] = (stamp, 0, json.dumps({'valid': True}))
    arbitrator.msgs.last.pop(('traffic_light', 'traffic.target'), None)
    assert arbitrator._traffic(sm, stamp) is None
    for consumer in (reader, arbitrator.msgs, tile.msgs, battery.msgs):
      consumer.fresh('_drain')
      consumer.last = {k: (t - 3_000_000_000, ttl, p) for k, (t, ttl, p) in consumer.last.items()}
    assert not tile.state(None)[0].visible
    assert battery.state(None) is None
    before = base_plan()
    after = base_plan()
    arbitrator.apply(after, sm, time.monotonic_ns())
    assert plan_output(after) == plan_output(before)
    broker.sock.close()
    broker.path.unlink()
    assert not plugins.send_msg(TrafficLight, 'traffic.plan', {}, valid_for_ms=350)
    print(json.dumps({'X-58': 'pass', 'transport': 'AF_UNIX/broker/cereal', 'sources': sorted(broker.seq),
                      'kinds': sorted({k for _, k in reader.last}), 'device': False}, sort_keys=True))
  finally:
    broker.sock.close()
    broker.lock.close()
    if plugins._msg_socket is not None:
      plugins._msg_socket.close()
    plugins._msg_socket = None
    socket_dir.cleanup()
    shutil.rmtree(ipc)
