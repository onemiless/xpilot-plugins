from ..diagnostics import ARS408Diagnostics, CONFIG_GRACE_REPORTS
from .test_diagnostics import radar_state


def test_cp_supported_distance_readback_is_not_wrong_config():
  for distance in (200, 224, 248, 250):
    diagnostics = ARS408Diagnostics(0)
    for now in range(1, CONFIG_GRACE_REPORTS + 1):
      diagnostics.observe_radar_state(radar_state(max_distance_m=distance), now)
    assert not diagnostics.snapshot(CONFIG_GRACE_REPORTS, parser_valid=True).errors.wrong_config


def test_invalid_distance_readback_remains_wrong_config():
  for distance in (0, 198, 201, 252):
    diagnostics = ARS408Diagnostics(0)
    for now in range(1, CONFIG_GRACE_REPORTS + 1):
      diagnostics.observe_radar_state(radar_state(max_distance_m=distance), now)
    assert diagnostics.snapshot(CONFIG_GRACE_REPORTS, parser_valid=True).errors.wrong_config
