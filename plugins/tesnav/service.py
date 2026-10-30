"""Single UDP worker and navInstruction publisher; no pluginMsg or CAN writes."""
import time

from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.tesla import host_api

from .plugin import TesNav
from .protocol import parse_snapshot
from .state import NavigationStore, instruction


def publish(pm, value):
  message = messaging.new_message('navInstruction', valid=value is not None)
  if value:
    for key, item in value.items():
      setattr(message.navInstruction, key, item)
  pm.send('navInstruction', message)


def main():
  # Fail on corrupt replay state instead of silently accepting an old session.
  store = NavigationStore(host_api.data_dir(TesNav) / 'udp-replay.json')
  pm = messaging.PubMaster(['navInstruction'])
  params = Params()
  next_publish = 0.
  try:
    with TesNav.navigation_ingress() as ingress:
      while True:
        received = ingress.receive()
        mono, wall = time.monotonic(), time.time_ns() // 1_000_000
        if received:
          body, peer = received
          try:
            # Pair a new source/session only while parked, including after restart.
            candidate = parse_snapshot(body)
            if (peer != store.owner or not store.latest or
                candidate.session_id != store.current_key) and not host_api.parked(params):
              raise ValueError('Park to establish the navigation session')
            ack = store.accept(body, peer, wall, mono)
            ingress.ack(ack)
          except (ValueError, UnicodeDecodeError, RecursionError):
            pass  # Never log phone coordinates or payloads.
        if mono >= next_publish:
          publish(pm, instruction(store.current(wall, mono), wall))
          next_publish = mono + .05
        time.sleep(max(0., next_publish - time.monotonic()))
  finally:
    publish(pm, None)


if __name__ == '__main__':
  main()
