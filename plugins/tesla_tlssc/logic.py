"""Pure TLSSC logic (no openpilot imports). t2can v3.29.1 vehicle_logic.h injectTLSSC: UI_autopilotControl 0x3FD,
mux 0 (byte 0 bits 0..2), TLSSC enable = bits 38 and 39 (byte 4 bits 6, 7); no counter or checksum."""
ADDRESS = 0x3FD
LENGTH = 8
TLSSC_BITS = 0xC0  # byte 4
KPH = 3.6


def is_template(data: bytes) -> bool:
  return len(data) == LENGTH and data[0] & 0x07 == 0


def tlssc_frame(template: bytes) -> bytes | None:
  """The stock mux-0 frame with TLSSC on, or None when the car already has it on (nothing to send)."""
  if not is_template(template) or template[4] & TLSSC_BITS == TLSSC_BITS:
    return None
  data = bytearray(template)
  data[4] |= TLSSC_BITS
  return bytes(data)

