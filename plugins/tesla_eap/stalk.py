"""Tesla SCCM left-stalk frame (0x249, vehicle bus); copy of nav_lane_change/stalk.py (from onemiless dev-sp-nav-prebuild create_sccm_left_stalk).
Cloned from the car's own idle frame: counter = template + 1, turn bits 16..18, CRC8-openSAFETY ^ per-counter magic."""
ADDRESS = 0x249
LENGTH = 4
IDLE, RIGHT, LEFT = 0, 2, 6  # observed on the Model 3/Y 4-byte frame (dev-sp)
MAGIC = (0x9B, 0xE8, 0x2A, 0xD3, 0xD3, 0x83, 0x4C, 0x5E, 0x3F, 0x5E, 0xE2, 0x28, 0x3A, 0x13, 0xAF, 0xCE)


def crc8_opensafety(data: bytes) -> int:
  crc = 0
  for value in data:
    crc ^= value
    for _ in range(8):
      crc = ((crc << 1) ^ 0x2F) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
  return crc


def is_idle_template(data: bytes) -> bool:
  return len(data) == LENGTH and data[2] & 0x07 == IDLE


def stalk_frame(template: bytes, turn: int) -> bytes:
  if not is_idle_template(template) or turn not in (RIGHT, LEFT):
    raise ValueError("needs an idle 4-byte 0x249 template and RIGHT/LEFT")
  data = bytearray(template)
  counter = (template[1] + 1) & 0x0F
  data[1] = (template[1] & 0xF0) | counter
  data[2] = (template[2] & 0xF8) | turn
  data[0] = crc8_opensafety(bytes((data[1] & 0xF0, data[2], data[3], 0))) ^ MAGIC[counter]
  return bytes(data)
