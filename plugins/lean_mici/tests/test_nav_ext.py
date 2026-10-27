"""NavExt ROAD_ATTR and model blinker predictions from a v4 REPLY."""
import math
import struct

import numpy as np

from ..wire import HEADER, nav_ext


def reply(present=0b0011, edge=0b1001, meta_logit=0.):
  data = bytearray(8480)
  HEADER.pack_into(data, 0, b'BGM1', 4, 0x11, 0, 1, 8440)
  out = np.ones(2066, '<f4')
  out[800:855] = meta_logit
  out[800 + 33] = 2.  # left blinker now
  out[800 + 38] = -2.  # right blinker at +2 s
  data[24:24 + 2066 * 4] = out.tobytes()
  struct.pack_into('<BB', data, 8304, present, 0b0111)
  struct.pack_into('<BBBBIIIHBBH', data, 8308, 2, 15, 10, 0, 300, 5000, 600, 20, 3, 255, 0xFFFF)
  struct.pack_into('<BBBBHHBBHHH', data, 8332, 50, 1, 80, 3, 420, 30, edge, 2, 0, 15, 0)
  return bytes(data)


def test_road_attributes():
  road = nav_ext(reply())['road']
  assert (road['speed_limit_kph'], road['speed_limit_ahead_kph'], road['speed_limit_ahead_m'], road['lane_count']) == (50, 80, 420, 2)
  assert road['ramp'] and road['roundabout'] and not road['tunnel'] and road['road_class'] == 3
  assert nav_ext(reply(present=0b0001))['road'] is None


def test_blinker_predictions():
  blinker = nav_ext(reply())['blinker']
  assert len(blinker['left']) == len(blinker['right']) == 6
  assert math.isclose(blinker['left'][0], round(1 / (1 + math.exp(-2)), 3))
  assert math.isclose(blinker['right'][1], round(1 / (1 + math.exp(2)), 3))
  assert math.isclose(blinker['left'][1], .5)
  assert 'blinker' not in nav_ext(reply(meta_logit=float('nan')))
