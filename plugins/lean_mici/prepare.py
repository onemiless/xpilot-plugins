"""Installation never waits for the phone or downloads a model."""
import pickle


def ready():
  # Check host ABI in the isolated preparation probe, not in manager discovery.
  from openpilot.selfdrive.modeld.modeld import run_with_model_source
  from openpilot.tesla.model_source import ModelSource
  from openpilot.common.hardware import HARDWARE
  from openpilot.common.transformations.camera import DEVICE_CAMERAS
  from openpilot.selfdrive.modeld.helpers import MODELS_DIR, load_oob, modeld_pkl_path
  device = HARDWARE.get_device_type()
  sizes = {cam.narrow_road.size for (d, _), cam in DEVICE_CAMERAS.items() if d == device}
  if not sizes:
    raise ValueError(f'no camera configuration for {device}')
  jits = load_oob(modeld_pkl_path(False), False)
  if not {'input_specs', 'output_specs', 'run', 'metadata'} <= jits.keys():
    raise ValueError('stock model incomplete')
  for width, height in sizes:
    with (MODELS_DIR / f'driving_warp_{width}x{height}_tinygrad.pkl').open('rb') as f:
      if 'run' not in pickle.load(f):
        raise ValueError('stock camera warp incomplete')
  from .camera import library
  library()
  return True
