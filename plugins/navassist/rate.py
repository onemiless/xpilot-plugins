"""Per-source request rate limiter shared by the UDP and HTTP receivers."""
from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable
import threading
import time


class ClientRateLimiter:
  def __init__(self, *, max_requests: int = 20, window_s: float = 1.0,
               clock: Callable[[], float] = time.monotonic):
    self.max_requests = max_requests
    self.window_s = window_s
    self._clock = clock
    self._events: dict[str, deque[float]] = defaultdict(deque)
    self._lock = threading.Lock()

  def allow(self, client: str) -> bool:
    now = self._clock()
    with self._lock:
      events = self._events[client]
      while events and now - events[0] >= self.window_s:
        events.popleft()
      if len(events) >= self.max_requests:
        return False
      events.append(now)
      return True
