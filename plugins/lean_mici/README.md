# lean_mici 0.3.0 — FIX round 2

Default off; model role plus `lean_modeld` only, `rules: []`, no CAN sends.
No model, native library or compiled model container is packaged or committed.
Overall qualification is **BLOCKED on A1 and the Mac live adoption target**.

## Two independent gates

**A1: same-model reference.** The required Walnut V3 source ONNX SHA256 is
`ceb38c593005b3338aee4ca057d5ad9ee64c58f8fdba0b41641a9d4cc6c36e36`.
The licensed phone file was copied to private evidence outside Git and verified:
`walnutV3.bin`, 782864384 bytes, SHA256
`1ccf14a18a00448ca68b1b8f432f66bef65a214532fc18c363e51fd74b68f150`.
It is the compiled/encrypted context container, not that ONNX. No ONNX was
found on the phone; 10389 local ONNX files were hashed with no match. A1's
per-head max/p99 error and a justified NPU tolerance are therefore unavailable.
Neither stock prediction differences nor upstream decoder equality passes A1.
No key was exported and the model remains outside every repository.

**A2: runtime control envelope.** Stock and Walnut may make different predictions.
Only control-relevant divergence is compared; shapes/finite/IDs/EOF/epoch and
hard protocol fault latches stay in S1. The plan window uses system T_IDXS <=2s.

| Metric | Calibration p99.5 | Runtime bound |
|---|---:|---:|
| curvature difference times max predicted speed squared | 0.8142 m/s² | 1.5 m/s² |
| longitudinal acceleration difference | 1.5061 m/s² | 2.5 m/s² |
| lead probability difference | 0.8658 | 0.98 |
| first lead distance difference, both probability >0.5 | 22.3247 m | 35 m |

These leave 84%, 66%, 13% and 57% margin above calibration p99.5 respectively.
Curvature is speed-normalised because raw yaw-rate/speed is ill-conditioned near
standstill. At 20m/s the bound is 0.00375m^-1; it is not an unbounded low-speed
steering target. System MAX_CURVATURE still caps absolute curvature.

Physical checks cover speed (<=80m/s, forward >=-2m/s), first-point position
within 5m, longitudinal acceleration [-8,5]m/s², lateral acceleration <=6m/s²,
physical action/pose and confident lead distance [0,300]m. Irrelevant far-plan
position and ordinary pose differences are not equality failures.

An envelope outlier falls back without permanently latching the source.
Structural faults still latch. S1's zero-wait selection, five consecutive fresh
warm-up replies, exact frame association and EOF+45ms deadline are unchanged.
The small PhoneSource subclass changes only admission rejection to recoverable;
it does not wait, skip sequence checks or preserve warmth across missed replies.

## Real driving validation on this Mac

Calibration used official public model-replay route
`98395b7c5b27882e|0000002b--2686b5a2d0/1`, frames 0..599. An independent public
Tesla route `aebd8f1d4ea16066|000002d8--5850d3ea5c/2` was not used to fit bounds.
Both were hash-pinned. Recorded sensor/device geometry and calibration feed the
system's own camera config; decoded video stride/UV offset are honoured.
Auxiliary inputs are retained/reconstructed from the recording, identically
supplied to stock and phone; this is not a live-car test.

On OnePlus 3B162S00PHS00000 / Chipmunk OS 2.3 / Walnut V3:

- Independent 600-frame direct batch: envelope acceptance **99.33%**. Four
  curvature-envelope rejects were recorded separately. Replaying already-arrived
  replies measures eligibility, not live zero-wait adoption.
- Final actual concurrent stock + separate production worker + real phone + S1
  IPC: **700 replies / 700 published frames**. The first 100 prime phone history;
  all are retained. Next 600 normal frames: actual adoption **0% (0/600)**;
  rejected **596 old_frame, 3 absent, 1 late**. Whole-run adoption is also 0%;
  whole-run rejects: 695 old_frame, 4 absent, 1 late.
- Normal replay EOF→reply: p50 **35.4520ms**, p95 **46.7955ms**, p99 **52.8671ms**.
  Encoding is offline for this archived-HEVC acceptance test. Raw Mac software
  encoding was also measured and does not meet the 45ms budget; C3XL's existing
  hardware codec availability/performance remains a device-task gate.
- Mac stock inference takes about 13ms, so phone replies are not present at the
  zero-wait selection point. The earlier 96.17% experiment added a bounded wait
  and relaxed rewarming; it is retained as an **unshipped contract deviation**.
  This plugin does not silently change S1 to meet the adoption target.

The live test uses a separate worker process: POSIX byte-range locks are per
process, so a shared-process threaded producer is not a valid IPC test. Earlier
threaded/mixed-clock experiments are retained as invalid evidence. Mac replay
EOF uses S1's clock_gettime clock, not mach-based time.monotonic_ns with a
potential suspend offset. Metadata peeks in the child are hints only; the
full payload is still read/validated under its nonblocking lock. This avoids
continuously locking an unchanged slot and dropping the producer's update.

## Listener residency and idle

There is another non-Mac `_lanlink._tcp` advertiser at 172.16.1.210:8088, and the
phone already has an HTTP connection to it. Only discovery records and the
phone's existing sockets were read; that service was not contacted or modified.
The installed App's service-lost callback does nothing; AutoRun keeps serving
while Connected/Reconnecting and applies a reconnect grace. PID/start time and
HELLO instance remained stable in this round. A resident listener was not proof
of ongoing inference or a new task-caused process restart.

After withdrawing the task beacon, passive 15s observations found no BGM client,
server blocked in accept (or absent), and <=2% of one CPU core during the final
10s; the final run peak was **0.7998%**. All replies completed before withdrawal.
This passes inference-idle acceptance while a listener/service may remain.
No taps, force-stop, OS/network/hardware parameter writes were used to make it idle.

## Repeatable evidence

Each run saves result.json, frames.csv, raw FRAME/REPLY and SHA256SUMS. Model
containers, ONNX files, real-route caches and logs live outside Git under the
authorised evidence directory. No car or shared checkout was changed.

Set PYTHONPATH to the matching system, tinygrad_repo, msgq_repo and opendbc_repo:

```sh
python plugins/lean_mici/tests/prepare_route.py --route <downloaded-route> --route-id <id> --output <new-cache>
python plugins/lean_mici/tests/calibrate_envelope.py --input <calibration-run> --output <new-stats>
python plugins/lean_mici/tests/envelope_e2e.py --output <new-output>
python plugins/lean_mici/tests/e2e.py --system <system> --output <new-output>
python plugins/lean_mici/tests/live_route.py --system <system> --stock <prepared-stock> \
  --input <real-route-cache> --address <Mac-LAN-IPv4> --frames 700 --output <new-output>
python tools/build_index.py --check
```

CI uses system `xpilot` directly and records its resolved commit. No system
branch was required. Remaining gates: original same-Walnut ONNX/A1, live adoption
under the zero-wait contract, broader real-road validation, and C3XL camera/codec/
resources/thermal/control continuity. No system contract change is included.
