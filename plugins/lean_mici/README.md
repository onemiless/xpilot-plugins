# lean_mici 0.6.2

## Navigation

The phone receives calibrated vehicle motion and returns navigation for the tile.
The navigation tile shows a maneuver arrow, distance, instruction, and remaining time.
Navigation is display only; turn-speed control is not included.

Default off; model role plus `lean_modeld` only, `rules: []`, no CAN sends.
No model or compiled model container is packaged or committed. The aarch64 native camera bridge is shipped with reproducible build_native.py.
Requires system `17227fd31e6d9b0c73a09d2d1b82fd84c4911df8` (X-20261025-42 packet-callback publisher fix). The bridge uses the system V4LEncoder and upstream WarpLut, not PyAV. Build on the target from the matching built xpilot checkout with `python3 <plugin>/build_native.py`. The normal recorder remains on its original stream; the private model-view encoders publish no msgq or files.

## C3XL camera path (0.5.1)

lean-master `617e00670a123da12809a65796985aae05710a08` feeds the phone calibrated
512x256 NV12 model views through the system Venus V4LEncoder. Full-resolution
recording HEVC and H.264 livestreams do not meet that contract. This plugin uses
that same hardware path directly, with the unchanged upstream NEON WarpLut and
Venus-aligned ION buffers. It never decodes or re-encodes the recording stream.
Both camera IDs/EOFs must match modeld metadata, and SOFs must pair within 10 ms.
Both encoders submit before either output is awaited. Header bytes precede actual
IDRs; flags are parsed from the actual Annex-B NALs. Recovery requests dual IDR.
The wire index uses the existing 50 ms time slots and the mailbox maps replies
back to modeld's exact frame ID/EOF. The worker closes both encoders on reconnect.

`prepare_install()` checks the stock model/warp and loads the prebuilt bridge;
it neither starts cameras nor imports PyAV. A malformed saved peer shape is
unpinned; only a dictionary with a string `name` pins a phone. Software encoding
exists solely in the offline replay fixtures under tests/. Real device evidence
and limitations live in `/Users/mile/work/evidence/X-20261025-42/REPORT.md`.

## Historical 0.5.0 qualification

**A1 waiver (coordinator FINAL decision):** the phone has only the encrypted
compiled Walnut V3 container; the original ONNX `ceb38c593005...` is unavailable.
The accepted baseline substitute was the exact BGM1 v4/S1 wire audit plus **596/600
(99.33%) A2 envelope agreement** on an independent real driving route. This is
not a claim of same-model numerical equivalence. Models stay outside the package
and repository.

**A2: runtime control envelope.** Stock and Walnut may make different predictions.
Only control-relevant divergence is compared; shapes/finite/IDs/EOF/epoch and
hard protocol fault latches stay in S1. The plan window uses system T_IDXS <=2s.

| Metric | Calibration reference | Runtime bound |
|---|---:|---:|
| curvature difference times max predicted speed squared | 0.8142 m/s² | 1.5 m/s² |
| longitudinal acceleration difference | 1.5061 m/s² | 2.5 m/s² |
| lead probability difference (calibration p99) | 0.7129 | 0.80 |
| first lead distance difference, either probability >0.5 (p99) | 32.2000 m | 35 m |

These leave 84% and 66% margin above plan calibration p99.5, and 12% and 9% above lead calibration p99 respectively.
Curvature is speed-normalised because raw yaw-rate/speed is ill-conditioned near
standstill. At 20m/s the bound is 0.00375m^-1; it is not an unbounded low-speed
steering target. System MAX_CURVATURE still caps absolute curvature.

Physical checks cover speed (<=80m/s, forward >=-2m/s), first-point position
within 5m, longitudinal acceleration [-8,5]m/s², lateral acceleration <=6m/s²,
physical action/pose and confident lead distance [0,300]m. Irrelevant far-plan
position and ordinary pose differences are not equality failures.

An envelope outlier resets warm-up and requires five fresh accepted replies before
phone selection resumes; it does not permanently latch the source.
Structural faults still latch. The system owns selection: cold/absent sources
never wait; recently valid sources may wait for the current frame only until
EOF+45ms. Its three-frame liveness window, warm-up and exact frame/epoch checks
come directly from ModelSource. The plugin adds no wait or sequence override.

## Action, lifecycle and peer fixes

Admission compares the actual action means against local action, or derives a
reference from the local first plan point when stock has no action head. Action
lateral-demand/acceleration differences use the same 1.5/2.5 m/s² bounds. Their
calibration p99.5 errors are 0.9945/1.1624 m/s². The physical lateral action limit
is min(6, MAX_CURVATURE*max(v,1)^2). S1 passes only heads to admit, so v is the
local first-plan speed proxy. Every action comparison remains a pure bounded
calculation. On the same calibration/independent routes, acceptance changes from
100%/99.33% to **99.33%/99.17%**; independent rejects are four curvature and one
lead-probability outlier. One-sided lead detection uses the same distance bound.

Bad discovery, wire, calibration, struct and codec input drops the connection;
new Camera/encoders and dual IDR are required on reconnect. Linux child startup
sets PR_SET_PDEATHSIG(SIGKILL), with the parent's PID supplied before spawn and
checked for the startup race. Mac validation also exits when its parent changes.
Each HELLO or REPLY has one monotonic 200ms message budget, including header and
body, in addition to individual recv timeouts. Manager/store discovery imports
no modeld/tinygrad; the existing isolated prepare probe checks the host ABI.

TOFU stores the first successfully HELLO-validated discovery instance name in
plugin JSON param `ExtLeanMiciPeer`. Later connections accept only that name and
log other advertisers. HELLO has no stable device ID: instance_id is random on
each server start. To select another phone or accept a changed NSD name, stop the
plugin offroad and remove only `/data/xpilot_plugins/.params/ExtLeanMiciPeer`;
the next unique peer is learned. The enable toggle is preserved.

lean-master `617e00670a` frame_codec.h defines REPLY bit0 as SEQ_RESET (server
created a sequence), bit1 as ZERO_PAIR (zero t-4 image). FRAME bit0 is separately
road IDR. That branch's codec is v2; the retained v4 reference `5edae8e285` and
captured v4 replies keep these flag meanings while adding NavExt. Road-only IDR
alone does not advance the epoch. A SEQ_RESET reply without a sent dual IDR
closes the connection and starts again with fresh codecs/dual IDR; it is never
published as reset_without_epoch. The direct phone replay enforces the same rule.

## Real driving validation on this Mac

Calibration used official public model-replay route
`98395b7c5b27882e|0000002b--2686b5a2d0/1`, frames 0..599. An independent public
Tesla route `aebd8f1d4ea16066|000002d8--5850d3ea5c/2` was not used to fit bounds.
Both were hash-pinned. Recorded sensor/device geometry and calibration feed the
system's own camera config; decoded video stride/UV offset are honoured.
Auxiliary inputs are retained/reconstructed from the recording, identically
supplied to stock and phone; this is not a live-car test.

On OnePlus 3B162S00PHS00000 / Chipmunk OS 2.3 / Walnut V3, the REVIEW FIX run
uses the merged host and real concurrent stock inference, a separate production
worker, phone TCP and S1 IPC. The first 100 frames prime phone history; the next
600 real driving frames are the adoption gate (>=90%). All inputs/replies and
startup outcomes are retained. Fresh REVIEW FIX results against `044edf530f`:

- **700/700 replies**, normal adoption **564/600 (94.00%)**, above the 90% gate.
  Including startup, adoption is **646/700 (92.29%)**.
- Normal EOF→reply p50/p95/p99: **35.1045 / 43.5872 / 48.9179 ms**.
- Normal fallback reasons: **20 absent, 16 late**. All-run counts:
  24 absent, 18 late, 6 old_frame, 1 envelope:lead_probability_divergence, 5 warmup. Expired replies stay local.
- Passive inference-idle observation after beacon withdrawal: **PASS**.

Evidence: `/Users/mile/work/evidence/X-20261025-39/review-fix/`, including raw wire,
frames.csv, telemetry, source identities, checks and SHA256SUMS.

Encoding is offline for this archived-HEVC test. Raw Mac software encoding does
not meet the budget; C3XL camera/codec/resource performance remains a device task.
The prior zero-wait measurements and plugin wait prototypes are historical;
REVIEW FIX uses only the merged system's engaged bounded wait.

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
10s; the FIX2 run peak was **0.7998%**. All replies completed before withdrawal.
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

The 0.5.1 candidate CI pins system `17227fd31e6d9b0c73a09d2d1b82fd84c4911df8`
and records both source identities. The manifest retains `ExtLeanMici` and its
`default: 0`; updates use the existing store transaction and preserve enabled
and disabled user choices. The index uses the usual main-branch package URL;
this task prepares and pushes the branch without merging or opening a PR.

Historical 0.5.0 remaining device checks: C3XL capture geometry/stride, hardware codec, resource
contention, thermal/power and control continuity. No car deployment is claimed.
