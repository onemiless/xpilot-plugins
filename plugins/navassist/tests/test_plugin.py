"""The panda plugin stays inactive unless the plugin sets its declared safetyParam bit at car init."""
from types import SimpleNamespace

import pytest

pytest.importorskip("openpilot.tesla.plugin")

from opendbc.sunnypilot.car.tesla.values import TeslaFlagsSP  # noqa: E402

from navassist.plugin import NavAssist  # noqa: E402

BIT = 0x8000


def _plugin():
  plugin = NavAssist.__new__(NavAssist)
  plugin.MANIFEST = SimpleNamespace(safety={"param_bit": "0x8000"})
  return plugin


def test_setup_sets_bit_with_vehicle_bus():
  cp_sp = SimpleNamespace(flags=TeslaFlagsSP.HAS_VEHICLE_BUS, safetyParam=0)
  _plugin().setup(cp_sp)
  assert cp_sp.safetyParam == BIT


def test_setup_without_vehicle_bus_leaves_bit_clear():
  cp_sp = SimpleNamespace(flags=0, safetyParam=0)
  _plugin().setup(cp_sp)
  assert cp_sp.safetyParam == 0
