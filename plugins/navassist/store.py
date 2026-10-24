from __future__ import annotations

from collections import deque
from collections.abc import Callable
import json
import os
from pathlib import Path
import threading
import time
from typing import Any

from .wire import (
  AcceptedSnapshot, APP_KEY_ID_PATTERN, CHECKPOINT_VERSION, MAX_SESSION_ID_LENGTH, MAX_SOURCE_AGE_MS,
  MAX_SOURCE_FUTURE_SKEW_MS, NavAssistProtocolError, SESSION_ID_PATTERN, SOURCE_DELIVERY_GRACE_MS, _reject,
  _reject_duplicate_keys, parse_snapshot,
)


class NavAssistStore:
  """Thread-safe freshness and replay boundary for one active phone session."""

  def __init__(self, *, clock_ns: Callable[[], int] = time.monotonic_ns,
               wall_clock_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
               remembered_sessions: int = 32, checkpoint_path: str | Path | None = None):
    if remembered_sessions <= 0:
      raise ValueError("remembered_sessions must be positive")
    self._clock_ns = clock_ns
    self._wall_clock_ms = wall_clock_ms
    self._checkpoint_path = Path(checkpoint_path) if checkpoint_path is not None else None
    self._lock = threading.Lock()
    self._current: AcceptedSnapshot | None = None
    self._retired_sessions: deque[str] = deque(maxlen=remembered_sessions)
    self._app_key_id: str | None = None
    self._active_session_id: str | None = None
    self._last_sequence = 0
    self._last_route_revision = 0
    self._load_checkpoint()

  @staticmethod
  def _valid_checkpoint_session(value: Any) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= MAX_SESSION_ID_LENGTH
            and SESSION_ID_PATTERN.fullmatch(value) is not None)

  @staticmethod
  def _valid_checkpoint_counter(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 2**63 - 1

  def _load_checkpoint(self) -> None:
    path = self._checkpoint_path
    if path is None or not path.exists():
      return
    try:
      raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys)
      legacy_keys = {"version", "tokenId", "activeSessionId", "sequence", "routeRevision", "retiredSessions"}
      if isinstance(raw, dict) and set(raw) == legacy_keys and raw.get("version") == 1:
        return
      expected_keys = {"version", "appKeyId", "activeSessionId", "sequence", "routeRevision", "retiredSessions"}
      if not isinstance(raw, dict) or set(raw) != expected_keys or raw["version"] != CHECKPOINT_VERSION:
        raise ValueError("unsupported replay checkpoint")
      app_key_id = raw["appKeyId"]
      active = raw["activeSessionId"]
      retired = raw["retiredSessions"]
      if (not isinstance(app_key_id, str) or APP_KEY_ID_PATTERN.fullmatch(app_key_id) is None
          or not self._valid_checkpoint_session(active) or not self._valid_checkpoint_counter(raw["sequence"])
          or not self._valid_checkpoint_counter(raw["routeRevision"]) or not isinstance(retired, list)
          or len(retired) > self._retired_sessions.maxlen
          or any(not self._valid_checkpoint_session(session) for session in retired)
          or len(set(retired)) != len(retired) or active in retired):
        raise ValueError("invalid replay checkpoint")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, NavAssistProtocolError, ValueError) as error:
      raise RuntimeError("NavAssist replay checkpoint is unreadable") from error
    self._app_key_id = app_key_id
    self._active_session_id = active
    self._last_sequence = raw["sequence"]
    self._last_route_revision = raw["routeRevision"]
    self._retired_sessions.extend(retired)

  def _save_checkpoint(self, app_key_id: str, active_session_id: str, sequence: int, route_revision: int,
                       retired_sessions: deque[str]) -> None:
    path = self._checkpoint_path
    if path is None:
      return
    payload = json.dumps({
      "version": CHECKPOINT_VERSION,
      "appKeyId": app_key_id,
      "activeSessionId": active_session_id,
      "sequence": sequence,
      "routeRevision": route_revision,
      "retiredSessions": list(retired_sessions),
    }, separators=(",", ":"), sort_keys=True).encode()
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
      fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
      with os.fdopen(fd, "wb") as checkpoint:
        checkpoint.write(payload)
        checkpoint.flush()
        os.fsync(checkpoint.fileno())
      os.replace(temporary, path)
    except OSError as error:
      try:
        temporary.unlink(missing_ok=True)
      except OSError:
        pass
      _reject("replay", f"replay checkpoint unavailable: {type(error).__name__}")

  def accept(self, body: bytes, app_key_id: str) -> AcceptedSnapshot:
    if not isinstance(app_key_id, str) or APP_KEY_ID_PATTERN.fullmatch(app_key_id) is None:
      _reject("authentication", "missing or malformed app key id")
    snapshot = parse_snapshot(body)
    source_age_ms = self._wall_clock_ms() - snapshot.source_wall_time_ms
    maximum_age_ms = min(MAX_SOURCE_AGE_MS, snapshot.valid_for_ms + SOURCE_DELIVERY_GRACE_MS)
    if source_age_ms < -MAX_SOURCE_FUTURE_SKEW_MS or source_age_ms > maximum_age_ms:
      _reject("replay", "sourceWallTimeMs is outside the receiver freshness window")
    now_ns = self._clock_ns()
    accepted = AcceptedSnapshot(snapshot, now_ns, now_ns + snapshot.valid_for_ms * 1_000_000)
    with self._lock:
      if self._app_key_id is not None and self._app_key_id != app_key_id:
        if self._current is not None and not self._current.is_stale(now_ns) and self._current.snapshot.route_active:
          _reject("replay", "another paired app owns the active navigation session")
        self._active_session_id = None
        self._last_sequence = 0
        self._last_route_revision = 0
        self._retired_sessions.clear()
      retired_sessions = deque(self._retired_sessions, maxlen=self._retired_sessions.maxlen)
      if self._active_session_id is not None:
        if snapshot.session_id == self._active_session_id:
          if snapshot.sequence <= self._last_sequence:
            _reject("replay", "sequence must increase within a session")
          if snapshot.route_revision < self._last_route_revision:
            _reject("replay", "routeRevision must not decrease within a session")
        else:
          if snapshot.session_id in retired_sessions:
            _reject("replay", "retired session cannot become active again")
          retired_sessions.append(self._active_session_id)
      self._save_checkpoint(app_key_id, snapshot.session_id, snapshot.sequence, snapshot.route_revision, retired_sessions)
      self._app_key_id = app_key_id
      self._active_session_id = snapshot.session_id
      self._last_sequence = snapshot.sequence
      self._last_route_revision = snapshot.route_revision
      self._retired_sessions = retired_sessions
      self._current = accepted
    return accepted

  def reset(self) -> None:
    with self._lock:
      self._current = None
      self._app_key_id = None
      self._active_session_id = None
      self._last_sequence = 0
      self._last_route_revision = 0
      self._retired_sessions.clear()
      if self._checkpoint_path is not None:
        try:
          self._checkpoint_path.unlink(missing_ok=True)
        except OSError as error:
          raise RuntimeError("NavAssist replay checkpoint could not be reset") from error

  def current(self, now_ns: int | None = None) -> AcceptedSnapshot | None:
    with self._lock:
      return self._current

  def is_stale(self, now_ns: int | None = None) -> bool:
    current = self.current()
    if current is None:
      return True
    return current.is_stale(self._clock_ns() if now_ns is None else now_ns)
