# X-20261025-39: fixed before implementation

F1 absent / unloaded model: local stock inference continues without waiting.
F2 NSD, HTTP, Wi-Fi ordering/address change, multiple phones: only one wlan0-subnet peer; ambiguity stays local.
F3 TCP fragments, truncation, oversized segments, version/type/flags/length mismatch: bounded reader, reject ABI.
F4 nonfinite/zero raw or malformed decoded heads: host locks the optional source for this drive.
F5 replay/order/epoch/frame/EOF/future timestamps: S1 checks exact frame association.
F6 late/burst/200ms stall: metadata newest only, no catch-up, no host network call.
F7 child crash/freeze/restart or missing dual IDR: isolated optional child; new epoch and five warmups.
F8 missing local model/warp: preparation fails; local runtime failure propagates.
F9 one publisher/four heads: real S1 modeld publication and health replay.
F10 device geometry/stride/UV/config and resource competition: device gate, no C4 camera constants.
F11 install rollback/state transition, old S1/S2 ABI: real store transaction; reject before taking role.
F12 thermal/power/screen/cold boot: device gate, no phone OS writes.
F13 trajectory/lead/pose/action discontinuity: bounded admission plus device/control replay gate.

Host tests replace QCOM inference/VisionIPC and stock readiness only. A synthetic peer
is not Walnut qualification. No test may set a production profile qualification flag.
Every E2E directory contains result.json, frames.csv, wire logs and SHA256SUMS.

## FIX round 1 (before v4/camera implementation)
- Validate full v4 HELLO=48B and REPLY=8480B/8440 payload, all 2066 outputs,
  echoed frame/EOF and flags; preserve full NavExt in wire evidence. Serialize an
  explicit S1 v2 envelope from known fields; never guess/pad a model layout.
- Python VisionIPC must match both IDs/EOF, copy before the next receive, honour
  real width/height/stride/UV offset and reject uncalibrated transforms. Verify
  padded geometry against the system warp and decode both HEVC streams.
- Encoder gap/reconnect requires fresh codec state, dual IDR and a new epoch;
  ordinary GOPs must not continuously reset the phone history.
- Runtime admission compares finite common heads and actual bounded source
  discontinuities; extreme plan/lead/pose/action candidates still fail closed.
- Real-phone replay: >=600 frames, all raw FRAME/REPLY and telemetry, same decoded
  camera bytes and desire/traffic/action_t for stock inference. Report head errors
  and acceptance honestly; different model weights are not numeric equivalence.
- Withdraw beacon, wait without taps/force-stop, then prove server and NPU stop.

## FIX round 2 (before safety-envelope implementation)
A1 is same-Walnut ONNX versus phone, with identical decoded HEVC and auxiliary
inputs. A stock-model equality or an upstream decoder comparison cannot pass A1.
Refuse missing/wrong-hash ONNX as an unavailable reference, not a numeric pass.
A2 uses real recorded driving frames, a disjoint calibration/evaluation split,
control-relevant curvature/acceleration through 2s and lead probability/distance.
Reject impossible physical motion and outlying control divergence; irrelevant
far-plan/pose differences must not cause an equality failure. Record each reason.
Retain S1 structural/freshness/frame/epoch latches. Envelope rejection is per-frame;
structural/protocol faults still latch. Late/absent phone never waits past 45ms.
C observes CPU ticks, waiting inference threads, sessions and processed-frame
activity after withdrawal. Listener or bound service residency alone is not failure.
Do not establish new BGM clients while measuring idle. Audit leftover advertisers
and the app's discovery/probe/restart logic without probing the car.
Final contract review: preserve S1 zero-wait selection and five consecutive fresh
replies after a rejection; a healthy-source deadline wait is not an allowed way
to reach the adoption target. Record the actual zero-wait adoption separately.

## FINAL (044edf530f, before validation adaptation)
The merged host now owns engaged bounded waiting and tolerates short output gaps;
test recovery against that contract without a plugin wait or sequence override.
A1 is coordinator-waived: retain exact v4/S1 wire audit and independent real-route
99.33% A2 envelope evidence; never label the waiver numerical equivalence.
Live adoption must be >=90% on >=600 normal real frames with the real phone.
Late replies still fall back at EOF+45ms. Refuse a busy phone before connecting.
Store updates must preserve both enabled and disabled user toggles, and package
metadata must remain default off with no model/container files.

## REVIEW FIX (fixed before implementation)
- Equal plans with divergent action means must be rejected, including a stock
  profile without action; cap action lateral demand by MAX_CURVATURE*max(v,1)^2.
- Reject excessive lead probability divergence and compare confident lead distance
  if either side sees a lead. Calibrate on the original driving split, not eval.
- An envelope rejection clears warm-up and requires five fresh accepted replies.
- Bad HELLO/REPLY/struct/Avahi/calibration/encoder input must close that connection,
  reset codecs and reconnect; startup camera failure must also be recoverable.
- A killed parent must leave no child/camera/encoder/TCP/beacon (Linux PDEATHSIG).
- Slow-drip messages time out on one overall deadline, across header and body.
- TOFU stores discovery instance name; HELLO's instance_id is per-start random,
  not a stable identity. Unpinned ambiguity refuses; pinned outsiders are logged.
- Sequence-reset replies, including after a road-only IDR, must never be mislabeled
  as reset_without_epoch; reconnect and require dual IDR rather than forge pairing.
- Manager/store import must not import modeld/tinygrad. Existing host refusal and
  store rollback/default-off/update-toggle behaviour remain covered by E2E.
