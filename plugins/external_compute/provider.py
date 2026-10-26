"""Plugin-local external compute transport; externald wraps wire dicts in pluginMsg.

Wire fields: source (peer name), kind, seq, payload (JSON text), validForMs.
Add providers inside this plugin; no host hook is needed.
"""


class Provider:
  NAME = ""

  def poll(self, timeout: float) -> list[dict]:
    """Wait up to timeout seconds; return messages received from external compute."""
    return []

  def send(self, msg: dict) -> None:
    """Deliver one device message (from an external.out envelope) to external compute, if this provider wants it."""

  def close(self) -> None:
    pass


def is_fresh(log_mono_ns: int, valid_for_ms: int, now_ns: int) -> bool:
  """Consumers: True while a pluginMsg envelope is within its validForMs (0 = no expiry)."""
  return valid_for_ms == 0 or 0 <= now_ns - log_mono_ns <= valid_for_ms * 1_000_000
