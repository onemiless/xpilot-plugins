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
