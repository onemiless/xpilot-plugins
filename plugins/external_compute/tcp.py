"""TCP provider: external compute connects to the device over any IP link (USB/Ethernet cable, WiFi, hotspot)
and exchanges JSON lines.

  peer -> device  {"hello": "<XpilotExternalToken>", "name": "laptop"}      device replies {"ok": true}
  peer -> device  {"kind": "vision.lanes", "payload": {...}, "validForMs": 200, "seq": 1}   -> pluginMsg external.in envelope
  device -> peer  {"source": ..., "kind": ..., "seq": ..., "payload": "<json>", "validForMs": ...}  <- pluginMsg external.out envelope

A connection is closed on a wrong or missing hello, a line over max_line bytes, no hello within HELLO_TIMEOUT_S,
or a full send buffer. Lines over max_rate_hz are dropped. Plain TCP: use it on links you trust.
"""
import hmac
import json
import selectors
import socket
import time

from .provider import Provider

HELLO_TIMEOUT_S = 5.0


class _Peer:
  def __init__(self, sock: socket.socket, rate_hz: float):
    self.sock, self.buf, self.name = sock, b"", None
    self.opened = self.refill = time.monotonic()
    self.tokens = rate_hz


class TcpProvider(Provider):
  NAME = "tcp"

  def __init__(self, token: str, host: str = "0.0.0.0", port: int = 8770, max_clients: int = 4,
               max_line: int = 65536, max_rate_hz: float = 50.0):
    self.token, self.max_clients, self.max_line, self.rate = token, max_clients, max_line, max_rate_hz
    self.server = socket.create_server((host, port), reuse_port=False)
    self.server.setblocking(False)
    self.port = self.server.getsockname()[1]
    self.sel = selectors.DefaultSelector()
    self.sel.register(self.server, selectors.EVENT_READ)
    self.peers: dict[socket.socket, _Peer] = {}

  def _drop(self, peer: _Peer) -> None:
    self.sel.unregister(peer.sock)
    self.peers.pop(peer.sock, None)
    peer.sock.close()

  def _write(self, peer: _Peer, obj: dict) -> None:
    try:
      peer.sock.sendall(json.dumps(obj).encode() + b"\n")
    except OSError:  # includes BlockingIOError: a peer that cannot keep up is dropped
      self._drop(peer)

  def _accept(self) -> None:
    sock, _ = self.server.accept()
    if len(self.peers) >= self.max_clients:
      sock.close()
      return
    sock.setblocking(False)
    self.peers[sock] = _Peer(sock, self.rate)
    self.sel.register(sock, selectors.EVENT_READ)

  def _line(self, peer: _Peer, raw: bytes) -> dict | None:
    try:
      obj = json.loads(raw)
    except ValueError:
      obj = None
    if peer.name is None:
      ok = isinstance(obj, dict) and isinstance(obj.get("hello"), str) and hmac.compare_digest(obj["hello"], self.token)
      if not ok:
        self._drop(peer)
        return None
      peer.name = str(obj.get("name") or "peer")[:64]
      self._write(peer, {"ok": True})
      return None
    if not isinstance(obj, dict) or not isinstance(obj.get("kind"), str):
      return None
    now = time.monotonic()
    peer.tokens = min(self.rate, peer.tokens + (now - peer.refill) * self.rate)
    peer.refill = now
    if peer.tokens < 1.0:
      return None
    peer.tokens -= 1.0
    try:
      return {"source": peer.name, "kind": obj["kind"][:64], "payload": json.dumps(obj.get("payload")),
              "validForMs": max(0, int(obj.get("validForMs", 0))), "seq": max(0, int(obj.get("seq", 0))) & 0xFFFFFFFF}
    except (TypeError, ValueError):
      return None

  def _read(self, peer: _Peer) -> list[dict]:
    try:
      data = peer.sock.recv(65536)
    except (BlockingIOError, InterruptedError):
      return []
    except OSError:
      data = b""
    if not data:
      self._drop(peer)
      return []
    peer.buf += data
    out, oversize = [], False
    while b"\n" in peer.buf and peer.sock in self.peers:
      raw, peer.buf = peer.buf.split(b"\n", 1)
      if len(raw) > self.max_line:
        oversize = True
        break
      if raw.strip() and (msg := self._line(peer, raw)) is not None:
        out.append(msg)
    if peer.sock in self.peers and (oversize or len(peer.buf) > self.max_line):
      self._drop(peer)  # an oversize line, with or without its newline
    return out

  def poll(self, timeout: float) -> list[dict]:
    out = []
    for key, _ in self.sel.select(timeout):
      if key.fileobj is self.server:
        self._accept()
      elif (peer := self.peers.get(key.fileobj)) is not None:
        out += self._read(peer)
    now = time.monotonic()
    for peer in [p for p in self.peers.values() if p.name is None and now - p.opened > HELLO_TIMEOUT_S]:
      self._drop(peer)
    return out

  def send(self, msg: dict) -> None:
    for peer in [p for p in self.peers.values() if p.name is not None]:
      self._write(peer, msg)

  def close(self) -> None:
    for peer in list(self.peers.values()):
      self._drop(peer)
    self.sel.close()
    self.server.close()
