"""Failure cases:
- T1 a non-mux-0 or short 0x3FD frame is cloned
- T2 the clone changes anything but byte 4 bits 6/7, or sends when the car already has TLSSC on
- T4 the manifest requires another plugin (TLSSC must work standalone) or loses the max-speed setting
- T5 the manifest rule is not mux-0 only, forces other bits, or copies bits 38/39 from the stock frame
"""
import json
from pathlib import Path

from ..logic import is_template, tlssc_frame

STOCK = bytes((0x08, 0x11, 0x22, 0x33, 0x05, 0x55, 0x66, 0x77))  # mux 0, TLSSC off


def test_template_only_mux0():
  assert is_template(STOCK)
  assert not is_template(bytes((0x01,)) + STOCK[1:])
  assert not is_template(STOCK[:7])


def test_clone_sets_only_tlssc_bits():
  out = tlssc_frame(STOCK)
  assert out[4] == 0xC5 and out[:4] == STOCK[:4] and out[5:] == STOCK[5:]
  assert tlssc_frame(out) is None
  assert tlssc_frame(bytes((0x02,)) + STOCK[1:]) is None


def test_standalone_manifest():
  manifest = json.loads((Path(__file__).parents[1] / "plugin.json").read_text())
  assert "plugins" not in manifest["requires"] and [s["key"] for s in manifest["settings"]] == ["XpTeslaTlsscMax"]
  assert list(manifest["params"]) == ["XpTeslaTlssc", "XpTeslaTlsscMax"]


def test_rule_matches_logic():
  rule = json.loads((Path(__file__).parents[1] / "plugin.json").read_text())["rules"][0]
  assert (rule["tx_address"], rule["tx_bus"], rule["predicate_mask"][0], rule["predicate_value"][0]) == (0x3FD, 1, 7, 0)
  assert rule["constant_mask"] == rule["constant_value"] == [0, 0, 0, 0, 0xC0, 0, 0, 0]
  assert rule["copy_mask"][4] == 0x3F and all(m == 0xFF for i, m in enumerate(rule["copy_mask"]) if i != 4)
