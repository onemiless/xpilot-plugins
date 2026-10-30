# TesNav Navigation / 特会开导航

Version 0.4.0 candidate combines phone guidance, recommended-lane display and optional
turn slowdown in one plugin. It does not steer, change lanes, send CAN, control
indicators, stop at traffic lights or feed a navigation driving model.

## Code separation in 0.2.0

The working C3XL lane/turn/overtaking decision code has now been extracted into
this package, retaining cancellation, speech receipt, relative-lane consistency,
timing and cooldown behavior. The large coordinator is split across lane_types,
lane_state, lane_intent and turn_signal; efficiency_lane owns overtaking policy,
radar_geometry owns generic corridor association. Hardware decode/tracking/DBC
belong to the separate ars408 package. No navigation module imports that package
or tests ARS408_RADAR. The original user's old-system vehicle-use confirmation
remains valid; it is not a new-plugin deployment result.

radar_source replaces the model flag with an explicit source/generation,
complete-frame, adjacent-target capability and matching timestamp contract.
Missing source/coverage fails closed, including an otherwise fresh empty frame.
The contract is a pure decision input, not a claim that the host already publishes
all required source metadata. It must be bound to actual producer frames before
the new decision code can execute vehicle actions.

The new coordinators are tested decision APIs; service.py still publishes the
existing guidance messages only. The old physical lamp controller, modeld desire
bridge, vehicle feedback and radar motion/Panda TX have not yet been connected
to these APIs. Therefore this remains a code-separation candidate, not a feature-equivalent
installation. Automatic lane/indicator actions are not silently enabled.

## UDP compatibility (0.4.0 candidate)

The original TesNav app sends v3 snapshots using UDP. This candidate requires
`navigation_ingress`, `data_dir`, and `parked` from a compatible host; upstream
hosts lacking those capabilities reject the plugin. The host limits datagrams to
private IPv4 peers, port 4213 and 8192 bytes, and applies rate limits. A new
phone or session is accepted only while parked. Snapshot freshness, session,
sequence and replay checks still apply; the checkpoint is retained under the
host-provided plugin data directory. Only metadata, not coordinates, is stored.
An ACK confirms receipt, not that the peer is authenticated or that a vehicle
action has occurred. Use a trusted network; private IP filtering is not identity
verification. Only one source:navigation provider may be active.

The service publishes navInstruction and clears it when data expires or the
worker exits. It does not activate the separate map/model, lane or indicator
candidates. Phone interoperability, current-device installation, real model
execution and vehicle validation remain unverified.
## Optional slowdown

`ExtTesNavSlowdown` defaults off. It requires Tesla openpilot longitudinal support,
fresh navigation/car/control/device messages and active longitudinal control.
It only supplies a lower cruise ceiling through `long_target`; stock following,
MPC, collision checks and vehicle/Panda limits remain authoritative. It never
requests acceleration, a stop or a higher cruise set speed.

Initial turn speeds: slight 8, normal 5, sharp 3.5, U-turn 2.5, roundabout 6 m/s.
The approach ceiling is `sqrt(turn_speed² + 2*1.2*max(distance-10,0))`, only below
both current speed and cruise speed, within 2 km. These are experimental inherited
research values, not a road-validated speed policy. Pedal/steering override,
loss of route or longitudinal authority after use latches slowdown off until an
observed offroad state. Changing a toggle does not clear that latch.

## Source and checks

The strict wire parser/types derive from the supplied
`official-navigation-research-20261026` source snapshot's
`sunnypilot/navassist/protocol.py`, split into `wire.py` and `protocol.py` without
the old system's store/network/control integration. Remaining logic is plugin-local.
No full system diff, private keys, SDK keys, weights or recordings are shipped.

Failure cases are listed before implementation in `tests/test_navigation.py`.
Run `python -m pytest plugins/tesnav/tests` and the host navigation ingress check,
then the repository's archive/index and store checks. Static/host checks do not
establish actual phone operation, performance, vehicle execution or road safety.
