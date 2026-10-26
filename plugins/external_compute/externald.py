#!/usr/bin/env python3
"""Plugin-owned TCP bridge: external.in envelopes to consumers; fresh external.out envelopes to TCP."""
import json
import secrets
import time

from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.tesla import plugins

from .plugin import ExternalCompute
from .provider import is_fresh
from .tcp import TcpProvider


def main() -> None:
  params = plugins.view(Params())
  token = params.get("XpilotExternalToken")
  if not token:
    token = secrets.token_hex(8)
    params.put("XpilotExternalToken", token)
  provider = TcpProvider(token, port=int(params.get("XpilotExternalPort", return_default=True)))
  out_sock = messaging.sub_sock("pluginMsg", conflate=False)
  try:
    while True:
      for data in provider.poll(0.02):
        plugins.send_msg(ExternalCompute, "external.in", data, data["validForMs"])
      for event in messaging.drain_sock(out_sock):
        msg = event.pluginMsg
        if msg.kind == "external.out" and is_fresh(event.logMonoTime, msg.validForMs, time.monotonic_ns()):
          provider.send({**json.loads(msg.payload), "source": msg.source})
  finally:
    provider.close()


if __name__ == "__main__":
  main()
