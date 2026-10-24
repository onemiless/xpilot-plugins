# Navigation Assist (navassist)

Phone navigation for xpilot: the device receives canonical v3 Android/iOS navigation observations, shows the next
maneuver and lane guidance, lowers the cruise ceiling before a supported turn, and lets Tesla's own lane verdict
plus an optional phone blind spot veto a lane change. It can request one bounded physical turn signal on 0x3E9;
SP's own DesireHelper / AutoLaneChangeController remains the only lane-change start authority.

## What runs

| Part | Hook / process | Effect |
|---|---|---|
| Receiver | `navassistd` (`process:navassistd`) | UDP 4213, strict v3 schema, per-source rate limit, session/sequence/TTL replay boundary |
| OEM | same process, `can` input | Reads 0x39B / 0x399 / 0x238; publishes `oem.lanes` |
| Topology | same process, `modelV2` | Visible lane count and ego index; publishes `lanes.topology` |
| Intent | same process | Target lane + turn-lamp request; publishes `lanes.intent` |
| Speed | `long_target` | Lowers the common cruise target before a supported maneuver; never requests a stop |
| Lane policy | `role:lane_change.policy` | Vetoes a change on Tesla's explicit reject, availability reject without local topology, or a confirmed phone blind spot |
| Turn signal | card `control_sends` | Clones a fresh idle 0x3E9 frame, changes request/reason/counter/checksum only; panda plugin `navassist` validates every frame |
| HUD | `ui.py` ScreenOverlay | Maneuver, distance, recommended lane, current OEM ALC verdict |

No new cereal messages are added: state travels on `pluginMsg` kinds `nav.state`, `lanes.topology`,
`lanes.intent`, `oem.lanes`. Nothing here writes steering, acceleration, curvature or CarState.

## Phone ingest

The phone posts the canonical v3 JSON snapshot (`navigation_snapshot`, `schemaVersion: 3`) to UDP 4213. The
parser rejects unknown/duplicate fields, non-finite numbers, unsupported enums, non-increasing sequence,
decreasing route revision, retired sessions, a source wall clock outside the receiver window, and bodies over
8 KiB. Each accepted packet is acknowledged with `navassist_udp_ack`; a separate 512-byte
`lane_position_request` returns the display-only `lanePosition` block (unknown until an absolute visual
producer exists).

The signed HTTP/discovery transport from the source branch is not included in 1.0.0. UDP is the Android/iOS
default and needs no pairing token; run it only on the controlled private LAN described in the source branch.

## Settings

All keys are store-backed (`plugin.json`): `ExtNavAssistEnabled`, `ExtNavAssistLaneChange`,
`ExtNavAssistTurnSignal`, `ExtNavAssistTurnSlowdown`, `ExtNavAssistLookahead`,
`ExtNavAssistExitLookahead`, `ExtNavAssistSignalLead`, `ExtNavAssistTurnSpeed`,
`ExtNavAssistMaxChanges`.

## Safety and limits

- `long_target` only lowers the target; SP's minimum selection keeps lead/FCW/model limits in force. The ceiling
  floor is 2 m/s; it never asks for a stop or acceleration.
- Lane positioning is one visible lane at a time. Phone absolute indices are compared with local visible indices
  only when the phone's total count equals the visible count and the recommendation touches the maneuver-side
  edge; otherwise the fallback is the extreme visible lane inside the configured lookahead.
- A single OEM frame never vetoes: the blocker requires two consecutive frames and clears after six.
- The physical lamp is bounded by panda: idle template only, request 1/2/3, reason 8/4, protected bits equal to
  the template, counter = template + 1, 100 ms rate, 200 frames per 60 s, `controls_allowed` for an active
  request, and the matching `safety.json` entry/param bit. Cancel (request 3) does not require
  `controls_allowed` because it only releases the lamp; active requests still do.
- Driver blinkers, blind spots and the configured ALC mode are still enforced by the stock DesireHelper.

## Not in 1.0.0

- Signed HTTP/discovery pairing and phone identity pinning.
- Absolute lane numbers from the camera image classifier (`road_lane_position.json` stays unknown, so phone
  lane feedback is unknown).
- Mid-lane-change force-abort in DesireHelper: the plugin cancels the lamp when a veto appears, but the stock
  state machine has no plugin abort hook.
- Closing-risk diagnostics and the Android companion app.

## Tests

```sh
cd plugins && python3 -m pytest -q navassist/tests
```

The panda side runs with the system repository:

```sh
cd opendbc_repo && PYTHONPATH=. python3 -m pytest -q opendbc/safety/tests/test_xpilot_plugins.py
```
