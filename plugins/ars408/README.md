# ARS408 Radar Receiver

Migration order confirmed by the owner: radar was installed first; overtaking
was added later. Complete the radar integration independently before resuming
navigation/overtaking work. Neither feature is a prerequisite for this driver.

An independent `source:radar` plugin extracted from the user's working C3XL
commit `6db98169cfefd6e7e4bd1f3bdaaf70623f79750d`. No navigation imports, route
decisions, lane changes or overtaking policy live here. The DBC is bundled and
loaded by absolute package path, without changing opendbc or global DBC paths.

The receiver preserves sensor decode, upside-down coordinate conversion,
quality states, exact/partial cycle assembly, track association/grace, duplicate
suppression, truck-echo handling and diagnostics. It returns standard RadarData
through the existing host radar_interface hook. Enabling an external receiver
does not modify factory CP/CP_SP flags or the original fallback receiver.

## Motion input and firmware contract (0.2.0)

The receiver now supplies calibrated motion through the host control_sends hook
at 20 Hz: bus 1, sensor ID 0, two-byte speed 0x300 and yaw 0x301. It declares both
can.tx claims and requires card_context, radar_context, control_sends, rule ABI 1
and sensor_motion.v1 support. Install the compatible host and Panda firmware
before enabling it; older hosts reject the requirements and older firmware
rejects the type-2 rule upload. A manifest claim alone never permits CAN TX.

The closed firmware profile requires a fresh real 0x60A object-status frame,
checks the complete payload, and enforces bus, DLC, standard ID, speed/yaw bounds,
rate, relay and safety-mode gates. It allows sensor motion while driving control
is disengaged, but does not authorize steering, braking, radar configuration or
filter writes. See the [sensor motion contract](https://github.com/juyun86/xpilot/blob/xpilot/docs/design/sensor-motion-rule.md).

`motion.py` preserves calibrated deviceMotion extraction: 150 ms motion freshness,
2 s calibration freshness, sensor health, finite values, calibrated axes and
cache invalidation. The card_context hook invalidates the old sample before each
update. Invalid CAN speed or missing/stale calibrated motion stops both sends;
zero yaw and Tesla CarState.yawRate are not substitutes. The receiver separately
consumes declared radar_context and reports radarUnavailableTemporary when its
motion input is unavailable, with bounded empty error updates. Hook exceptions
latch the provider off through the host's original-receiver fallback.

Configuration/query/filter encoders in transmitter.py remain inactive. This
release expects the existing installation's sensor configuration (object and
quality output, sensor ID 0); it does not provision a factory-reset radar.
Disable/uninstall while parked and restart to release claims and omit its rules.
No private configuration, raw socket, background process or additional parameter
is introduced for transmission. Settings and subscriptions are fixed per drive.

Enable only while parked, with compatible firmware and the correct sensor configuration, then
restart. CAN bus is 1, sensor ID 0, expected object output/quality and a 200–250 m even maximum distance follow the CP-supported range. Mount inversion defaults true to preserve
this user's confirmed old installation; set it to match the actual installation,
never infer orientation from radar model. Settings are read at drive startup.

Only one source:radar provider wins. Other radar models get independent packages;
this plugin makes no compatibility claim for them. Radar errors and unknown or
unmeasured targets must not be taken as proof that adjacent lanes are clear.
The existing host falls back to its original receiver on exceptions; consumers
cannot infer the current source/coverage from the selected manifest alone.

## Provenance and validation

`source.json` lists exact source hashes and narrowly scoped port changes.
Existing decoder/cycle/diagnostic/tracker/truck-echo/interface/encoder tests were
ported before implementation; test fixture imports and DBC lookup are local.
The receiver, tracker and filter algorithms were not rewritten. The source's
old CP flag in the encoder constructor is retained only as legacy compatibility;
new callers can explicitly provide enabled=True/False. No navigation consumer
is allowed to use that model flag as its radar health test.

Tests validate code and synthetic frames. Actual new-plugin vehicle use and
full old-system feature equivalence require later integration acceptance.
