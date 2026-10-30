"""Failure cases recorded before migration:
invalid imports, unsafe imported listeners, leaked control credentials,
duplicate cores/routes, crash blackholes, missing PID recovery, destructive cleanup,
onroad mutation and lost startup preference. Live cases are in parked_e2e.py.
"""
import unittest
from pathlib import Path

import importlib.util
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

spec = importlib.util.spec_from_file_location('starlink.formats', Path(__file__).resolve().parents[1] / 'formats.py')
formats = importlib.util.module_from_spec(spec)
spec.loader.exec_module(formats)


class ImportContract(unittest.TestCase):
  def test_normalize_keeps_control_local_and_uses_device_credential(self):
    raw = """proxies:
  - {name: example, type: ss, server: 192.0.2.10, port: 443, cipher: aes-128-gcm, password: fixture}
external-controller: 0.0.0.0:9090
allow-lan: true
listeners: [{port: 12345}]
"""
    result = formats.normalize(raw, 'fixture-not-a-real-credential')
    self.assertEqual(result['external-controller'], '127.0.0.1:17891')
    self.assertEqual(result['secret'], 'fixture-not-a-real-credential')
    self.assertFalse(result['allow-lan'])
    self.assertNotIn('listeners', result)
    self.assertFalse(result['tun']['auto-route'])
    self.assertEqual(result['tun']['device'], 'c3mh0')

  def test_unrecognized_or_empty_input_is_rejected(self):
    for raw in ('', '123', 'proxies: []', 'not-a-valid-node'):
      with self.assertRaises((ValueError, AssertionError, KeyError)):
        formats.normalize(raw, 'fixture-not-a-real-credential')


if __name__ == '__main__':
  unittest.main()
