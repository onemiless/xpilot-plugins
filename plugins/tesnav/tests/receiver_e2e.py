#!/usr/bin/env python3
"""UDP navigation receiver acceptance.
Isolated Linux E2E: real UDP -> admission -> ACK -> cereal -> expiry/exit.

Failure cases: wrong ACK contract, malformed packets kill worker, message coercion
fails, stale routes remain valid, worker exit leaks socket. Never uses port 4213,
live Params, vehicle CAN, real phone coordinates or the live msgq namespace.
Run with XPILOT_NAV_API pointing at the reviewed host navigation.py.
"""
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import types


os.environ['OPENPILOT_PREFIX'] = f'tesnav-isolated-{os.getpid()}'
Path('/dev/shm', 'msgq_' + os.environ['OPENPILOT_PREFIX']).mkdir()
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
# Only the host API is needed from the candidate; existing C3 cereal stays unchanged.
host = Path(os.environ['XPILOT_NAV_API']).parent
import openpilot
openpilot.__path__.insert(0, str(host.parent))
namespace = types.ModuleType('openpilot.tesla')
namespace.__path__ = [str(host)]
sys.modules['openpilot.tesla'] = namespace
from openpilot.cereal import messaging
from openpilot.tesla.navigation import NavigationIngress
from tesnav import service
from tesnav.tests.test_navigation import payload


class Parked:
  def get_bool(self, _key):
    return True


def main():
  # The fixture uses a real local RFC1918 interface; loopback is deliberately rejected.
  with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
    route.connect(('192.168.1.179', 9))
    host_ip = route.getsockname()[0]
  transport = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  transport.bind((host_ip, 0))
  transport.settimeout(.05)
  port = transport.getsockname()[1]
  ingress = NavigationIngress('fixture', authorize=lambda: True, transport=transport)
  stop = threading.Event()
  receive = ingress.receive

  def bounded_receive():
    if stop.is_set():
      raise SystemExit()
    return receive()

  ingress.receive = bounded_receive
  service.TesNav.navigation_ingress = classmethod(lambda cls: ingress)
  service.Params = Parked
  service.host_api.parked = lambda _params: True
  service.host_api.data_dir = lambda _plugin: Path(tempfile.gettempdir())
  original_store = service.NavigationStore
  with tempfile.TemporaryDirectory(prefix='tesnav-e2e-') as tmp:
    service.NavigationStore = lambda _path: original_store(Path(tmp) / 'checkpoint.json')
    sm = messaging.SubMaster(['navInstruction'])
    failures = []

    def worker():
      try:
        service.main()
      except SystemExit:
        pass
      except BaseException as error:
        failures.append(repr(error))

    thread = threading.Thread(target=worker)
    thread.start()
    try:
      with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
        client.bind((host_ip, 0))
        client.settimeout(1.)
        time.sleep(.1)
        client.sendto(b'[]', (host_ip, port))
        client.sendto(b'{bad', (host_ip, port))
        value = payload()
        wall = time.time_ns() // 1_000_000
        value['sourceWallTimeMs'] = wall
        value['location']['observedAtMs'] = wall
        value['guidance']['observedAtMs'] = wall
        value['lanes']['observedAtMs'] = wall
        client.sendto(json.dumps(value).encode(), (host_ip, port))
        ack = json.loads(client.recvfrom(2048)[0])
        assert ack == {'messageType': 'navassist_udp_ack', 'schemaVersion': 3,
                       'sessionId': 'fixture-a', 'sequence': 1}, ack
        deadline = time.monotonic() + 1.
        while time.monotonic() < deadline:
          sm.update(100)
          if sm.valid['navInstruction'] and sm['navInstruction'].maneuverDistance == 80:
            break
        else:
          raise AssertionError('No real cereal navigation instruction')
        assert sm['navInstruction'].lanes[0].active
        deadline = time.monotonic() + 2.
        while time.monotonic() < deadline:
          sm.update(100)
          if sm.updated['navInstruction'] and not sm.valid['navInstruction']:
            break
        else:
          raise AssertionError('Expired route remained valid')
        assert not failures and thread.is_alive(), failures
    finally:
      stop.set()
      thread.join(2.)
    assert not thread.is_alive() and not failures, failures
    assert transport.fileno() == -1
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as rebound:
      rebound.bind((host_ip, port))
  print('PASS real UDP/ACK/cereal/expiry/worker-exit/rebind; isolated namespace')


if __name__ == '__main__':
  main()
