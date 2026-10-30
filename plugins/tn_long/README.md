# tn_long: TN longitudinal (TN-NoDEC)

Replaces SP's longitudinal plan, including lead following, with the TN-NoDEC planner: the legacy cruise-obstacle MPC
plus the TN acceleration controller (pace, lead plan, accel personality). Ported from xpilot dev-sp
(`openpilot/tesla/long/{legacy_mpc,tn_no_dec,tuning.py,registry.py}` at c1b8a76873^) with relative imports and
`Ext*` params.

- Hook: `long_planner` (`role:long.planner`). The host clips aTarget to ACCEL_MIN/MAX, keeps SP's MPC for FCW and
  falls back to SP's plan on bad output; brake disengage, stock AEB and panda limits are unchanged.
- Not included: TN's controlsd stopping policy (`longcontrol_policy.py`): there is no plugin hook for longitudinal
  control yet, so stops use SP's LongControl.
- Settings: Accel Personality on/off and profile (Eco/Normal/Sport). `ExtTnLongTuning` holds the runtime tuning
  config (absent: TN defaults); there is no tuning UI yet.

## Tesla right-scroll follow distance (7 levels)

The card plugin reads the vehicle-bus `STW_ACTN_RQ` frame (`0x238`), signal `DTR_Dist_Rq` (byte 1), and writes the
confirmed level to `ExtTnScrollLevel`. The planner applies the level to its TN runtime tuning; the other TN values
(comfort brake, stop distance, costs) are unchanged. DTR `255`/SNA is ignored, and a new level needs two consecutive
frames. The table is intentionally explicit:

| 档 | Tesla DTR | t_follow | 使用场景 |
|---|---|---|---|
| 1 | 0（ACC_DIST_1） | 1.05 s | 很近，堵车/慢速 |
| 2 | 33（ACC_DIST_2） | 1.25 s | 接近现在的 aggressive |
| 3 | 66（ACC_DIST_3） | 1.45 s | 现在的 standard |
| 4 | 100（ACC_DIST_4） | 1.60 s | 标准偏舒适 |
| 5 | 133（ACC_DIST_5） | 1.75 s | 现在的 relaxed |
| 6 | 166（ACC_DIST_6） | 1.90 s | 明显拉远 |
| 7 | 200（ACC_DIST_7） | 2.10 s | 最远，长途/舒适 |

The effective desired gap remains `v²/(2·comfort_brake) + t_follow·v + stop_distance`. Changing a level ramps
`t_follow` by at most 0.20 s per second. `ExtTnScrollLevel = -1` (default) keeps normal TN personality tuning; no
level is written until the wheel actually changes. Only read-only CAN is used; no extra TX or panda rule is added.

## Prebuilt binaries

The acados solvers are compiled code, so this plugin ships binaries for one platform. To build them, on the device
running the target openpilot version:

    cd /data/openpilot && PYTHONPATH=/data/openpilot python3 <checkout>/plugins/tn_long/build_native.py
    cd <checkout> && python3 tools/build_index.py

`build_native.py` writes `legacy_mpc/c_generated_code*/*.so` and stamps `plugin.json` with the platform
(`machine`, Python ABI). Devices only offer and load it on a matching platform. Rebuild after openpilot changes its
Python, numpy or acados versions. Until it is built, `build_index.py` leaves it out of `index.json`.

Status: host import check only. The solver build and the planner have not run on a device in this form.

## How to verify on the device

```bash
cat /data/xpilot_plugins/.params/ExtTnScrollLevel
cat /data/xpilot_plugins/.params/ExtTnFollowApplied
```

- `ExtTnScrollLevel`: `-1` before the first scroll, then `0..6` for levels 1..7.
- `ExtTnFollowApplied`: applied `t_follow` in milliseconds. It should move toward `1050, 1250,
  1450, 1600, 1750, 1900, 2100` (at most 1 Hz write, 0.2 s/s ramp).
- If the level changes but applied stays `-1`, the TN planner is not running (check `ExtTnLong`).
- The stock 3-bar personality display is unchanged; use the two params above for the 7-level readout.
