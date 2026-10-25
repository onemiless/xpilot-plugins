# lean_mici 0.1.0 — blocked candidate, not a phone-ready release

TASK X-20261025-39. Default off; claims only `role:perception.model` and
`process:lean_modeld`; `rules: []`; no CAN sender. No models are included.

The attached OnePlus 15T (`3B162S00PHS00000`, Chipmunk OS 2.3 / app 0.17-os)
automatically started its server after the Mac LAN beacon was published and sent
a 48-byte **BGM1 v4 HELLO**. S1 and this candidate require **BGM1 v2**, exact
8320-byte replies and 2066 floats. No FRAME was sent to the incompatible phone.
Walnut V3 selection through the existing App, 600-frame inference, numeric/control
qualification and EOF latency percentiles are **not completed**. The last read
selection was Hazelnut V1; no preferences, OS, model files or phone permissions were written.

`profile.admit()` deliberately returns False. A synthetic matching decoder is not
Walnut qualification. Do not open admission until same-input numeric and control
replay gates pass. Do not pad/truncate replies or change a v4 version byte to v2.

The checked-in Linux aarch64 `.so` is the reproducible **warp/ABI test build**:
`lean_camera_available() == 0`. `prepare_install()` therefore refuses activation.
The source camera bridge uses xpilot VisionIPC and V4LEncoder, but the full AGNOS
DSO was not built: the available container lacks the matching Cap'n Proto/QCOM
SDK and compiled PIC system libraries. Do not label the packaged `.so` a working
C3XL sender. Rebuild the full DSO and validate it before a store release.

## Host contracts

- S1: system `057a6f76b8c8001301a6136c6e7db3efa8e50f7d`.
- S2 merged to system `xpilot` at `f2000d3ec6dee5f42d02d01384851febca60ac03`.
- CI checks out system `xpilot` directly and records the resolved system HEAD.
  No task-branch pin or source overlay remains.
- Entry import requires both S1 and S2 before taking the model role. Preparation
  reads the stock model/warps and checks native ABI; it never waits for a phone.
- Runner calls `run_with_model_source(ModelSource(...))`. Network, discovery and
  native encoding live in one optional child; there are no child cereal publishers.
  The child's private V4LEncoder base constructor omits the system constructor's
  publisher side effect. This linkage requires full target build verification.
- Metadata/replies use the S1 latest-only file mailbox, locks and length-last
  commit. Host keeps per-frame local stock inference and the 45ms/five-warmup
  contract. Only host modeld publishes the four standard messages.
- Native source dimensions/stride/UV offset come from VisionBuf, encoder output
  alignment from system `get_nv12_info`, and calibrated warp matrices from S1.
  No C4 source-camera constants or CPU/FIFO tuning are copied.
- Onroad beacon is a LAN-bound HTTP root with Avahi D-Bus `_lanlink._tcp` service;
  `_bigmodel._tcp` discovery rejects ambiguity and is restricted to wlan0 subnet.
  Mac uses Bonjour only in validation. No USB/ADB inference transport.
- Offroad SIGTERM terminates/kills the child, joins the status thread and removes
  the mailbox/status. A dead optional child stays local for the drive.

## Repeatable checks

Use a Python 3.12 environment containing the system's existing dependencies.
`SYSTEM` below is a disposable S1/S2 snapshot, never the shared system checkout.
On Mac, put its matching msgq, tinygrad and opendbc checkouts on PYTHONPATH.

```sh
python plugins/lean_mici/build_native.py --host --output /tmp/liblean_mici-host.so
python plugins/lean_mici/tests/native_e2e.py --library /tmp/liblean_mici-host.so --output /tmp/x39-native
PYTHONPATH="$SYSTEM:$SYSTEM/tinygrad_repo:$SYSTEM/msgq_repo:$SYSTEM/opendbc_repo" \
  python plugins/lean_mici/tests/e2e.py --system "$SYSTEM" --output /tmp/x39-host
python plugins/lean_mici/tests/phone.py --address <Mac-LAN-IPv4> --output /tmp/x39-phone
python tools/build_index.py --check
```

Latest Mac host run: 409 frames passed, including controlled fragmented TCP,
malformed mailbox candidates, frozen/killed child fallback, base inference failures,
store rollback and runner cleanup. Mac and Linux padded-stride warp checks passed;
the Linux warp-only ELF rebuilt byte-for-byte. The wider plugin suite had 341
passes and one existing tesla_mixed_control steering-codec assertion failure,
independently reproduced from untouched baseline 9a095ca on this Mac.

E2E output directories contain result.json, frames.csv, wire logs and SHA256SUMS.
QCOM inference/VisionIPC/native capture, platform identity and preparation are
explicit test doubles in host replay. TCP/socket fragmentation, mailbox, store
transaction, discovery, postprocessing, Capnp/msgq publication and selfdrived
health checks execute real code. Production admission remains closed.

Reproduce the packaged warp-only ELF on an aarch64 Linux host with C++17:

```sh
python plugins/lean_mici/build_native.py --host
```

Full target build (not verified), with matching PIC system libraries and SDK:

```sh
python plugins/lean_mici/build_native.py --system <matching-built-system>
```

Remaining device gates: full native build/link and absence of publishers; camera
geometry/stride/UV and SOF pairing; encoder callbacks/dual IDR/reconnect; 10 onroad
auto-start cycles; 30min thermal/power; DM/loggerd/control health; source/action
continuity and independent road qualification. No C3XL was contacted in this task.
