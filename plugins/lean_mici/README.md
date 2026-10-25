# lean_mici 0.2.0 — FIX round 1

Default off; claims only `role:perception.model` and `process:lean_modeld`;
`rules: []`; no CAN sender. No models or native binaries are redistributed.

This candidate now runs the real BGM1 v4 phone path. Its **release gate remains blocked**: Walnut V3 did not match the system stock model on the same
620 decoded camera pairs, and stable phone auto-stop is not proven. Do not treat working transport or green host CI as
phone model qualification.

## Implementation

- Python VisionIPC reads matching camera IDs/EOF, uses each VisionBuf's actual
  width/height/stride/UV offset, and copies into model NV12 before receiving again.
  Calibrated warp matrices come from S1. No C4 source geometry is embedded.
- PyAV/FFmpeg already appears in system camerad. Mac/PC uses libx265; AGNOS uses
  the existing `hevc_v4l2m2m` codec. `prepare_install()` loads the stock artifacts
  and encodes a real probe, refusing missing artifacts/codec or delayed output.
  AGNOS's Python package and V4L2 codec availability still need device validation.
- Custom `.so`, C++/V4L bridge, native build script and old archive were removed.
- Network/encoding remain in one optional child. Only host modeld publishes the
  standard four messages. The latest-only S1 metadata/reply mailbox is unchanged.
- The child validates the entire v4 reply, preserves exactly 2066 output floats,
  explicitly serializes S1's v2 envelope and maps wire indices to host frame IDs.
  NavExt is framed and retained in test wire; this plugin does not use navigation.
- Runtime `admit()` is enabled with finite/near-term plan, lead, pose and action
  bounds. A semantic failure still triggers S1's drive lock and immediate local
  fallback. No always-false placeholder or native readiness stub remains.
- LAN beacon/unique subnet-scoped discovery and offroad child/IPC cleanup remain.
  No ADB inference transport or phone OS modification is used.

## Wire provenance

The requested `lochuan/lean-mici/lean-master` still resolves to
`617e00670a123da12809a65796985aae05710a08`, which defines **v2**. The local
`onemiless/lean-mici` v4 port at `5edae8e28596f0f9d0bc2c3d2b28219c9c4bb665`
retained an incorrect 160-byte FRAME header for this installed phone.

The installed Chipmunk 0.17-os library's `bgm1::parse_frame` confirms the actual
**152-byte** v4 FRAME header (road starts at 0x98; vehicle data at 144–148;
149–151 reserved). `pack_reply` and real wire confirm **8480-byte** REPLY,
payload length 8440, 2066 floats, four telemetry uint32 values, 160-byte NavExt
and reserved MAC. The captured HELLO is 48 bytes/version 4. Strict tests reject
other lengths/versions/flags; models are never padded or truncated.

S1 remains `057a6f76b8`; CI checks out system `xpilot` directly (S2 merged at
`f2000d3ec6dee5f42d02d01384851febca60ac03`) and records resolved HEAD. No system
change or X-40 branch was needed.

## Real phone proof on this Mac

- OnePlus `3B162S00PHS00000`, Chipmunk OS 2.3. App UI navigation was turned off
  and Walnut V3 selected with its own "set current" action. Model SHA256:
  `1ccf14a18a00448ca68b1b8f432f66bef65a214532fc18c363e51fd74b68f150`.
- 620 dual-camera frames over LAN v4; archived synthetic 512x256 inputs, 20Hz
  replay schedule, controlled valid desire pulses/traffic/action_t (not road-derived),
  staggered dual HEVC.
  Latency measures replay EOF to reply, excluding offline archive encoding.
- EOF→reply p50 **32.2187ms**, p95 **40.4523ms**, p99 **62.3921ms**;
  **98.2258%** arrived within 45ms. A second committed-driver 620-frame run measured
  31.7117/40.4725/73.3464ms and 96.9355% within 45ms, with the same numeric
  rejection. These are Mac replay measurements, not C3XL.
- Beacon withdrawal stopped the server automatically; subsequent read-only
  snapshots showed no NPU process or NpuService. A passive-only repeat observed
  NPU exit **10.805s** after withdrawal, but a later passive snapshot again
  found NpuService and a 7070 listener without a task beacon. This is a
  transient stop, **not stable auto-stop proof**; the restart cause is unknown.
  The passive test now requires 10s continuous absence. Active TCP polling
  was removed because it creates new clients; its timeout is retained.
  A later 64.24s passive check did not achieve 10s continuous NPU absence.
  No taps during/after the run, no ADB force-stop.
- All **1,280,920 decoded values** matched the pinned upstream decoder exactly.
  This proves codec/head parsing, not equivalence of different model weights.
- Real stock model SHA256 `65a08adc31d5c456219687d99b7bf5e44d61dae2d49ea67850e76105c7248cce`
  was loaded from local LFS cache and compiled with the system's own tinygrad
  compiler for Mac. Same decoded image bytes and auxiliary inputs were used.
  At predeclared rtol=.02/atol=.05, numeric match was **0%**; per-frame admission
  **3.5484%**; replay through unchanged S1 selected **0%** phone, correctly locking
  on admission failure. The phone manifest identifies source ONNX
  `ceb38c593005b3338aee4ca057d5ad9ee64c58f8fdba0b41641a9d4cc6c36e36`,
  which differs from stock. This is not a same-model equivalence proof; the
  exact contribution of weights, frontend and temporal state is unproven.

## Repeatable checks

Failure modes were fixed in tests/FAILURES.md. Every E2E directory saves
result.json, frames.csv, wire evidence and SHA256SUMS. Set PYTHONPATH to the
matching system, tinygrad_repo, msgq_repo and opendbc_repo; use Python 3.12 with
its existing dependencies and PyAV.

```sh
python plugins/lean_mici/tests/v4_e2e.py --output <new-camera-output>
python plugins/lean_mici/tests/e2e.py --system <system> --output <new-host-output>
python plugins/lean_mici/tests/phone_replay.py --address <Mac-LAN-IPv4> \
  --input <archived-nv12-dir> --stock <prepared-stock-dir> --system <system> \
  --frames 620 --output <new-phone-output>
python plugins/lean_mici/tests/phone_replay.py --address <Mac-LAN-IPv4> \
  --input <archived-nv12-dir> --stock <prepared-stock-dir> --system <system> \
  --replay <retained-phone-output> --output <new-selection-replay>
python tools/build_index.py --check
```

Remaining gates: stock numeric qualification, stable automatic phone shutdown
and source/control continuity;
C3XL's PyAV/V4L2 availability, actual geometry/resource contention, onroad cycles,
thermal/power and road qualification. No car was contacted in this task.
