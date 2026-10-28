# Tesla Mixed Control

Angle-based cooperative steering from dzid26: light driver torque adjusts the
SP steering target, with speed-dependent effort, a 0.5 Nm deadzone, holding
torque compensation, a resume ramp and saturation anti-windup. It runs only
while lateral control is active and clears its override when inactive.

**SP must own steering. Stock Autosteer must not steer at the same time.**
AP ownership and handoff (path 2) are not implemented. This plugin has only
`role:lateral.controller` and shared `event.filter` claims; CAN encoding, driver override gates, final angle
limits and Panda checks remain in the existing host/car port. It has no CAN
claim, safety profile, longitudinal behavior or UI tile. Host checks do not
establish vehicle stability or safe concurrent Autosteer operation.

## Enable

Install **Tesla Mixed Control** from the plugin store while parked. Turn on
its plugin switch (`XpTeslaMixedControl`, BOOL, default `0`), then restart
the driving processes. The setting is latched for the session. Setup clears
`CP_SP.flags.COOP_STEERING` so the native port uses angle mode; it does not
write the saved `TeslaCoopSteering` parameter. Disable the plugin and restart
to restore the native parameter's meaning. There are no tuning settings.

## Stock Autosteer setting (1.1.0)

The shared `event_filter(ctx, CS, names, names_sp)` removes only the
`invalidLkasSetting` event. The host selects active plugins once at startup;
with `XpTeslaMixedControl` off the filter is not installed. The filter does
not reread the switch each frame. CarState keeps the actual DAS_settings
signal. With the switch off at startup or the plugin absent, SP again blocks
engagement when stock Autosteer is enabled in car settings. Switch changes
take effect after restarting the driving processes.
This permits the setting; AP ownership and handoff remain unimplemented.

Version 1.1.0 requires the host `event_filter` hook. System
`1b52d017f4` rejects its unknown `event.filter` claim, so the new plugin does
not install or run there. Deploy both in one offroad session, **system first,
plugin second**, then restart before driving. The E2E checks the actual
DAS_settings CAN -> CarState -> SP events -> filter -> SP engagement chain
with the plugin off, on and absent, saving `event-filter.json`.

## Timing and failure behavior

The source is `onemiless/opendbc` commit
`65cc8fdbcb6aaaf0533ba296c9e6fe5422a696e8`,
`opendbc/sunnypilot/car/tesla/coop_steering.py`, with the ordering from
`opendbc/car/tesla/carcontroller.py`. The unused jerk/acceleration experiment
from `701700fab` is excluded. `tests/reference.py` is the exact O source.

`LatControlAngle(CP, CP_SP, CI, DT_CTRL)` computes the baseline at 100 Hz
using controlsd's learned VM. Every two active calls, the adapter first runs
the original baseline limiter and then one complete 20 ms cooperative step.
It holds the resulting angle on the intervening 10 ms call. Both limiters
and the torque mapping use a separate `VehicleModel(get_safety_CP())`;
the learned VM never supplies safety limits. The 300 deg/s^2 resume ramp retains its original units. Since 1.2.0 the final angle limit follows the car
port's `CarControllerParams.ANGLE_LIMITS` (xpilot raised it from 5 to 8 degrees per 20 ms update, owner 2026-10-28;
it only binds below ~15-19 km/h, where the lateral-jerk limit is looser); driver-override slew gains keep the 5
degree scale (`OVERRIDE_MAX_ANGLE_RATE`). `tests/reference.py` carries the same two-line deviation from O.

The Tesla car port still limits and encodes the output at 50 Hz. For paired
inputs, its additional limiter is idempotent: the unquantized output matches
O within `1e-10` degrees (the fixture currently measures zero difference).
The real CarControl/carOutput path uses float32 (tolerance `3e-5` degrees
up to 360 degrees); CAN encoding is checked within 0.05 degrees.
The hook and card have independent phases; either phase sees the held target,
with up to one 10 ms call of sampling delay. There is no timestamp or car TX
acknowledgment in this hook. Missed calls do not trigger catch-up integration;
missed/reordered messages and real process scheduling are not proven by the
host fixture. The car port always applies its own current-speed limiter.

All four sensor inputs and the baseline angle must be finite on **every**
call, including held calls. Invalid input raises; other exceptions propagate
to the real host guard, which switches permanently to the concurrently
running SP controller for the rest of that process. The first fallback
**target can step by the existing override**: this plugin cannot change the
host's SP return value after a fault. A non-finite speed can also make SP's
baseline non-finite; controlsd's existing final actuator sanitation replaces
that with zero (this one-line sanitation is reproduced in the fixture).
The first transmitted angle does not
skip the car port's persistent VM rate limiter; the E2E checks this for both
send phases with a nonzero override. This is bounded slew, not exact target
continuity. A guaranteed continuous target would require a host transition
interface; that is not part of path 1. Vehicle fallback comfort remains
unverified. The native COOP flag stays cleared until restart after a fault.

## Repeatable host checks

Use Python 3.12 with the xpilot test dependencies and build its `msgq_repo`
extension as described in the repository CI. From this plugin repository:

```sh
export X=/path/to/xpilot
export PYTHONPATH="$X:$X/msgq_repo:$X/opendbc_repo"
python3 -m pytest -q plugins/tesla_mixed_control/tests
python3 plugins/tesla_mixed_control/tests/e2e.py --output /tmp/tesla-mixed-evidence
python3 tools/build_index.py
python3 tools/build_index.py --check
python3 "$X/tools/sp_tesla_e2e/store_e2e.py" --repo .
cd /tmp/tesla-mixed-evidence && shasum -a 256 -c SHA256SUMS
```

The E2E exercises actual discovery, ParamsView, setup, `LatControlAngle`,
the controlsd host wrapper, Tesla CarController and decoded steering CAN.
Only input sensor data, parameter storage and fault recording are synthetic.
It writes `inputs/sequence.json`, `params.json`, `source.json`,
`frames.jsonl`, `result.json`, `commands.txt` and `SHA256SUMS`.
The 50 Hz O oracle and 100 Hz plugin receive the same paired samples;
the algorithm float tolerance is `1e-10` degrees, CarControl/carOutput float32
tolerance `3e-5` degrees, and CAN quantization tolerance 0.05 degrees.
Holding-torque drift allows `1e-6` degrees for float32 sensor quantization.
It covers standstill, deadzone/sign, speed mapping, holding torque,
saturation/anti-windup, inactive/resume, disabled bit identity, COOP gating,
learned/fixed VM separation and permanent exception/non-finite fallback.

Not verified: live controlsd/card scheduling, stale/dropped/reordered input,
full daemon execution, physical CAN/Panda or firmware, vehicle dynamics and
closed-course comfort. This is a synthetic host sequence, not a vehicle replay.

## License and attribution

This project uses software from Haibin Wen and SUNNYPILOT LLC and is licensed under a custom license requiring permission for use.

This software is licensed under a custom license requiring permission for use.
The source's complete license is retained in [LICENSE.md](LICENSE.md).
