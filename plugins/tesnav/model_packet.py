"""Offline map/instruction assembly using plugin-local navigation input types.

No listener, IPC publisher, Params or inference. The renderer and complete steps
are supplied offline; this adapter never fabricates either from current guidance.
"""
import numpy as np

from .model_inputs import MapFrame, NavigationFrame, NavigationIdentity
from .model_navigation import navigation_input


def build_model_packet(nav, routes, rendered, steps, *, steps_identity, steps_source_ns,
                       complete, traveled, wall_ms, now_ns):
  """Return (MapFrame, NavigationFrame), or (None, None) to revoke both inputs.

Map source_ns is the original fix time in the same monotonic clock as now_ns.
steps_source_ns is the original complete-step observation time in that clock.
The caller must bind both to the actual route producer, not stamp cached data
with the current receive time. This function does not grant host authorization.
"""
  if type(now_ns) is not int or now_ns <= 0:
    return None, None
  route = routes.current(nav, wall_ms, now_ns / 1e9)
  if route is None or route.coordinate_system != 'wgs84':
    return None, None
  identity = NavigationIdentity('tesnav', route.session_id, route.route_revision)
  if (steps_identity != identity or not isinstance(rendered, MapFrame)
      or rendered.identity != identity or rendered.valid is not True
      or rendered.coordinate_system != 'wgs84' or type(rendered.source_ns) is not int
      or rendered.source_ns <= 0 or not 0 <= now_ns-rendered.source_ns <= 500_000_000):
    return None, None
  encoded = navigation_input(steps, traveled, complete=complete, source_ns=steps_source_ns, now_ns=now_ns)
  if not encoded.valid:
    return None, None
  if not isinstance(rendered.pixels, np.ndarray) or rendered.pixels.dtype != np.uint8 or rendered.pixels.shape != (256, 256):
    return None, None
  image = MapFrame(identity, rendered.source_ns, rendered.pixels.copy())
  instruction = NavigationFrame(identity, steps_source_ns, np.array(encoded.vector, dtype=np.float32))
  return image, instruction
