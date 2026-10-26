"""External compute bridge over real localhost sockets.

- E1 lines from a client that has not sent a valid hello are published
- E2 a wrong token keeps the connection open
- E3 an authenticated client's messages are not published, or lose kind/payload/validForMs/source
- E4 an oversize line is buffered without limit instead of dropping the client
- E5 malformed JSON or a non-object line crashes the bridge
- E6 outbound messages reach unauthenticated clients, or miss authenticated ones
- E7 more than max_clients connections are accepted
- E8 a client flooding faster than max_rate_hz gets every message through
- E9 a client disconnecting mid-stream crashes the bridge
- E10 is_fresh accepts an expired message, or rejects one with validForMs 0 (no expiry)
- E11 TCP -> pluginMsg loses peer fields/TTL, publishes undeclared kinds, or echoes back to TCP
- E12 pluginMsg -> TCP loses fields, forwards unrelated/expired data, or creates a second publisher
- E14 concurrent TCP inbound and outbound publishers displace one another or echo/repeat messages
- E13 source manifests and built index/archive disagree on the eight default tiles
"""
import json
import multiprocessing
import os
import select
from pathlib import Path
import platform
import shutil
import socket
import sys
import time

from ..provider import is_fresh
from ..tcp import TcpProvider

TOKEN = "s3cret"


def connect(port: int) -> socket.socket:
  s = socket.create_connection(("127.0.0.1", port), timeout=2)
  s.settimeout(0.5)
  return s


def line(s: socket.socket, obj) -> None:
  s.sendall(obj if isinstance(obj, bytes) else json.dumps(obj).encode() + b"\n")


def drain(p: TcpProvider, seconds: float = 0.3) -> list[dict]:
  out, end = [], time.monotonic() + seconds
  while time.monotonic() < end:
    out += p.poll(0.05)
  return out


def recv_lines(s: socket.socket) -> list[dict]:
  data = b""
  try:
    while chunk := s.recv(65536):
      data += chunk
  except (TimeoutError, socket.timeout):
    pass
  return [json.loads(x) for x in data.splitlines() if x.strip()]


def closed(s: socket.socket) -> bool:
  try:
    return s.recv(1) == b""
  except (TimeoutError, socket.timeout):
    return False
  except OSError:
    return True


def test_bridge_e1_to_e9():
  # One sequence: each step relies on the connections and state left by the previous ones.
  errors = []
  p = TcpProvider(TOKEN, host="127.0.0.1", port=0, max_clients=3, max_line=1024, max_rate_hz=20)
  try:
    anon = connect(p.port)
    line(anon, {"kind": "x", "payload": 1})
    if drain(p):
      errors.append("E1 unauthenticated message published")

    bad = connect(p.port)
    line(bad, {"hello": "wrong", "name": "bad"})
    drain(p)
    if not closed(bad):
      errors.append("E2 wrong token left open")

    good = connect(p.port)
    line(good, {"hello": TOKEN, "name": "laptop"})
    drain(p)
    if recv_lines(good) != [{"ok": True}]:
      errors.append("E3 no hello ack")
    line(good, {"kind": "llm.reply", "payload": {"text": "hi"}, "validForMs": 500, "seq": 7})
    got = drain(p)
    want = {"source": "laptop", "kind": "llm.reply", "payload": json.dumps({"text": "hi"}), "validForMs": 500, "seq": 7}
    if len(got) != 1 or any(got[0].get(k) != v for k, v in want.items()):
      errors.append(f"E3 got {got}")

    line(good, b"{not json}\n")
    line(good, b"[1, 2]\n")
    try:
      if drain(p):
        errors.append("E5 malformed line published")
    except Exception as e:
      errors.append(f"E5 bridge raised {e!r}")

    p.send({"kind": "llm.ask", "payload": json.dumps({"q": "why"})})
    if [m.get("kind") for m in recv_lines(good)] != ["llm.ask"]:
      errors.append("E6 authenticated client missed outbound")
    if recv_lines(anon):
      errors.append("E6 unauthenticated client got outbound")

    extra = [connect(p.port) for _ in range(3)]
    drain(p)
    if not any(closed(s) for s in extra):
      errors.append("E7 more than max_clients accepted")

    for i in range(100):
      line(good, {"kind": "flood", "payload": i})
    n = len(drain(p, 0.5))
    if n >= 100:
      errors.append(f"E8 flood not limited ({n}/100)")

    big = connect(p.port) if not closed(anon) else None
    line(good, b"x" * 5000)
    drain(p)
    if not closed(good):
      errors.append("E4 oversize line did not drop the client")

    anon.close()
    try:
      drain(p)
    except Exception as e:
      errors.append(f"E9 disconnect raised {e!r}")
    if big:
      big.close()
  finally:
    p.close()
  assert errors == []


def test_is_fresh_e10():
  now = 10_000_000_000
  assert not is_fresh(now - 600_000_000, 500, now)
  assert is_fresh(now - 400_000_000, 500, now)
  assert is_fresh(0, 0, now)


def bridge_sender(connection):
  from types import SimpleNamespace
  from openpilot.tesla import plugins
  sender = SimpleNamespace(NAME="sender", CLAIMS=("process:senderd", "msg:external.out", "msg:other"))
  while (command := connection.recv()) is not None:
    kind, wire, ttl = command
    connection.send(plugins.send_msg(sender, kind, wire, ttl))


def test_pluginmsg_bridge_e11_e12_e14(monkeypatch, tmp_path):
  import threading
  from types import SimpleNamespace
  from openpilot.cereal import messaging
  from openpilot.tesla import plugins, plugin_msgd
  from .. import externald
  from ..plugin import ExternalCompute

  manifest = json.loads(Path(__file__).parents[1].joinpath("plugin.json").read_text())
  monkeypatch.setattr(ExternalCompute, "NAME", manifest["id"])
  monkeypatch.setattr(ExternalCompute, "CLAIMS", tuple(manifest["claims"]))
  monkeypatch.setattr(plugins, "view", lambda params: params)
  sender = SimpleNamespace(NAME="sender", CLAIMS=("process:senderd", "msg:external.out", "msg:other"))
  monkeypatch.setattr(plugins, "active", lambda params: [ExternalCompute, sender])
  errors = []

  class StopBridge(BaseException):
    pass

  for inbound, outbound in ((True, False), (False, True), (True, True)):
    prefix = f"x59-{tmp_path.parent.name}-{inbound}-{outbound}"
    monkeypatch.setenv("OPENPILOT_PREFIX", prefix)
    ipc = Path("/tmp" if platform.system() == "Darwin" else "/dev/shm") / f"msgq_{prefix}"
    ipc.mkdir()
    provider = TcpProvider(TOKEN, host="127.0.0.1", port=0)
    monkeypatch.setattr(externald, "TcpProvider", lambda *a, **k: provider)
    class Params:
      def get(self, key, **kwargs):
        return {"XpilotExternalToken": TOKEN, "XpilotExternalPort": provider.port}.get(key)
    monkeypatch.setattr(externald, "Params", Params)
    monkeypatch.setattr(sys.modules["openpilot.common.params"], "Params", Params)
    stop = threading.Event()
    poll = provider.poll
    def bounded_poll(timeout):
      if stop.is_set():
        raise StopBridge()
      return poll(timeout)
    monkeypatch.setattr(provider, "poll", bounded_poll)
    def run():
      try:
        externald.main()
      except StopBridge:
        pass
      except Exception as exc:
        errors.append(repr(exc))
    broker = plugin_msgd.Broker()
    def relay():
      try:
        while not stop.is_set():
          broker.update()
          if select.select([broker.sock], [], [], .01)[0]:
            broker.receive()
      except Exception as exc:
        errors.append(repr(exc))
    parent, child = multiprocessing.get_context("spawn").Pipe()
    worker = multiprocessing.get_context("spawn").Process(target=bridge_sender, args=(child,))
    worker.start()
    manager = messaging.PubMaster(["managerState", "deviceState"])
    reader = plugins.PluginMsgs()
    relay_thread = threading.Thread(target=relay, daemon=True)
    relay_thread.start()
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    peer = connect(provider.port)
    def send(kind, wire, ttl=1000):
      parent.send((kind, wire, ttl))
      assert parent.poll(5) and parent.recv(), errors
    try:
      for _ in range(10):
        state = messaging.new_message("managerState", valid=True)
        state.managerState.processes = [{"name": "externald", "pid": os.getpid(), "running": True},
                                        {"name": "senderd", "pid": worker.pid, "running": True}]
        manager.send("managerState", state)
        manager.send("deviceState", messaging.new_message("deviceState", valid=True))
        time.sleep(.02)
      reader.fresh("_ready")
      line(peer, {"hello": TOKEN, "name": "laptop"})
      assert recv_lines(peer) == [{"ok": True}], errors
      wire = {"source": "laptop", "kind": "llm.reply", "seq": 7,
              "payload": json.dumps({"text": "hi"}), "validForMs": 1000}
      if outbound:
        send("other", wire)
        expired = messaging.new_message("pluginMsg", logMonoTime=time.monotonic_ns() - 2_000_000_000)
        expired.pluginMsg = {"source": "sender", "kind": "external.out", "payload": json.dumps(wire), "validForMs": 1}
        broker.pm.send("pluginMsg", expired)
        time.sleep(.05)
        assert recv_lines(peer) == [], "unrelated or expired message forwarded"
      for sample in (0, 1, 2, 2, 3):
        current = {**wire, "seq": sample}
        if inbound:
          line(peer, {**current, "payload": {"text": "hi"}})
        if outbound:
          send("external.out", current)
          assert recv_lines(peer) == [{**current, "source": "sender"}], errors
        if inbound:
          end, got = time.monotonic() + 2, {}
          while got != {"external_compute": current} and time.monotonic() < end:
            got = reader.fresh("external.in")
            time.sleep(.01)
          assert got == {"external_compute": current}, errors
      assert recv_lines(peer) == [], "inbound echoed or outbound repeated"
      assert not reader.fresh("external.in", time.monotonic_ns() + 2_000_000_000)
    finally:
      peer.close()
      stop.set()
      thread.join(2)
      relay_thread.join(2)
      parent.send(None)
      worker.join(5)
      if worker.is_alive():
        worker.kill()
        worker.join()
      parent.close()
      child.close()
      provider.close()
      broker.sock.close()
      broker.lock.close()
      del manager, reader, broker
      shutil.rmtree(ipc)
      shutil.rmtree(Path(plugin_msgd.socket_path()).parent)
    assert not thread.is_alive() and not relay_thread.is_alive() and worker.exitcode == 0 and errors == [], errors


def test_default_catalog_e13():
  import importlib.util
  import io
  import tarfile

  root = Path(__file__).resolve().parents[3]
  spec = importlib.util.spec_from_file_location("build_index", root / "tools/build_index.py")
  builder = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(builder)
  files = builder.build()
  entries = json.loads(files[root / "index.json"])["plugins"]
  expected = {"speed_card", "lead_card", "speed_limit_card", "car_tile", "map_tile", "clock_tile", "ip_tile", "blindspot_edges"}
  assert {e["id"] for e in entries if e.get("default") is True} == expected
  for entry in entries:
    source = json.loads((root / "plugins" / entry["id"] / "plugin.json").read_text())
    assert entry.get("default", False) is source.get("default", False)
    data = files[root / "dist" / f"{entry['id']}-{entry['version']}.tar.gz"]
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
      assert json.load(archive.extractfile(f"{entry['id']}/plugin.json")) == source
