from unittest.mock import mock_open, patch

from ..network import gateway

ROUTE = """Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT
wlan0\t0004220A\t00000000\t0001\t0\t0\t0\t00FFFFFF\t0\t0\t0
wlan0\t00000000\t3B04220A\t0003\t0\t0\t600\t00000000\t0\t0\t0
"""


def test_gateway():
  with patch('builtins.open', mock_open(read_data=ROUTE)):
    assert gateway('wlan0') == '10.34.4.59'
    assert gateway('eth0') is None
