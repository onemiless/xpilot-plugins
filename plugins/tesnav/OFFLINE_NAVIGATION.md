# Offline navigation migration

This is an unconnected candidate, not an installed navigation-model feature.
The user selected offline migration only on 2026-10-27. No new listener,
authentication setting, device deployment or model switch is included.

## Route geometry

`route.parse_route(bytes)` parses the research App's original v3
`navigation_route` envelope, up to 2 MiB and 20000 latitude/longitude points.
`RouteStore.accept(body, peer, nav, wall_ms, mono)` requires the admitted live
NavigationStore owner, matching session/revision/coordinate system, a sequence
not ahead of its snapshot and a fresh source timestamp. Geometry cannot change
under the same revision. `current()` must be called after every snapshot and
on every consumer tick, including invalid snapshots, to clear expired/cancelled
routes and retire old uploads. It does not persist route coordinates.

`coordinates()` yields WGS-84 points only. GCJ-02 routes remain labelled for a
future coordinate-aware renderer; they must not be published as WGS-84 or
silently combined with a differently referenced basemap.

## Official command encoding

`model_navigation.navigation_input(maneuvers, traveled, complete=...,
source_ns=..., now_ns=...)` is pure logic. Each maneuver has `type`, `modifier`
and `distance` attributes using the official Mapbox-style vocabulary. Distances
and traveled are metres relative to one shared origin, not independently
measured distances to each next junction. The caller must bind the complete
list to the route/session before invoking this function.

The output contains `valid`, a tuple of 47 float32-representable values, and a
bounded reason code. Slots 0–45 encode the Valhalla command; slot 46 is remaining
distance divided by 100. A consumer must check validity separately: an invalid
input and a valid arrival-only input both have a zero vector.

The mapping follows commaai/openpilot commit
`a03bb1d4e80050cae0007780ecb8eb7cf06d0cb9`,
`openpilot/selfdrive/navigation/helpers.py`. Passed maneuvers, departure/arrival
and merge are skipped. Unlike the reference's permissive straight fallback,
unknown types/modifiers invalidate the entire input; unordered, nonfinite,
incomplete or older-than-2-second data is also invalid. No previous vector is
cached or carried into a new call. No array is sent to modeld by this module.

The supplied App route envelope has only geometry, not this complete maneuver
list. Its current-junction snapshot cannot be passed with `complete=True`.
The exporter still needs complete steps and a defined common distance origin.

## Validation and remaining work

Failure cases were written before implementation. Offline checks:

    python -m pytest plugins/tesnav/tests/test_route.py plugins/tesnav/tests/test_model_navigation.py plugins/tesnav/tests/test_navigation.py

2026-10-27: 56 passed on Windows Python, using synthetic points and times.
These checks do not prove phone interoperability, coordinate alignment, map
rendering, model ABI integration, GPU performance, vehicle actions or road use.
`service.py`, the published package/index and the device remain unchanged by
these offline modules. Full app steps, map rendering and a host model-input
contract remain separate work; a network entry point is explicitly deferred.

## sunnypilot reimplement-nav comparison (2026-10-27)

Fixed reference: `f7f9a3efae3a6552068201bd9e4e4dbe66aae577`.
Reference files are under `openpilot/selfdrive/navigation/`, except modeld
under `openpilot/sunnypilot/modeld_v2/`. The source license is retained in
THIRD_PARTY_NAVIGATION_LICENSE.txt. No upstream repository instructions become
authorization for changes to this device or the user's repositories.

| Reference | Plugin migration decision | Current result |
| --- | --- | --- |
| helpers.py command mapping | Pure logic can live in TesNav | Existing model_navigation.py matches all 140 supported vocabulary combinations tested against this fixed source; unknown/incomplete inputs remain invalid |
| route.py projection, distance and heading | Pure geometry can live in TesNav | Adapted as route_geometry.py, with explicit WGS84 and input validation; 75 sequential synthetic poses match the reference |
| helpers.py latest_gps_source | Selection logic is reusable, but SubMaster access belongs to a declared service | Not wired; phone position and native GPS must not silently exchange coordinate systems or source identity |
| mapsd.py orchestration | Split renderer from message/VisionIPC/network lifecycle | Not copied into plugin service; do not start its listener or remote tile downloads |
| modeld_v2/modeld.py takes_nav and input assignment | Generic model ABI and model execution are host responsibilities | Reference for a future host API only; not placed in the plugin |
| modeld_v2/modeld.py freshness | Retain per-frame clearing, strengthen production timestamp checks | Receive age alone is insufficient; a newly received map may carry timestamp zero or stale location data |

### Route geometry API and differences

`RouteTracker(points, coordinate_system='wgs84')` accepts `(longitude, latitude)`
pairs; `route.ModelRoute.points` is `(latitude, longitude)`, so the eventual
adapter must explicitly reverse each pair after any required coordinate
conversion. Creating a new tracker is required on route identity change or
invalidation. No tracker is automatically wired to RouteStore or service.py.

`snap(lon, lat)` returns route-centred `(longitude, latitude, bearing)` for a
future renderer. It retains the reference's monotonic progress, -30/+100 metre
search window and +/-50 metre heading chord. Consecutive duplicates are removed;
degenerate, nonfinite, out-of-range and antimeridian geometry is rejected.
An offline default 30 metre route-offset rejection prevents far-away fixes from
moving progress. The exact projected position must also satisfy the progress
window, since selecting an overlapping long segment alone does not enforce it.
These additional bounds are not calibrated road/lane safety guarantees.

The first fix may match any segment. Parallel roads, self-intersections, GPS
accuracy and correct source matching still require external evidence; this
tracker cannot establish the correct lane or authorize a maneuver. GCJ-02 is
rejected rather than renamed WGS84. Route geometry alone cannot manufacture
the App's missing complete maneuver list.

### Host-side gaps found in this branch

The reference discovers navigation capability from model input names and adds
navInstruction subscription and a mapsd VisionIPC client. Each model run zeroes
missing nav_map/navigation inputs. That provides a useful implementation
pattern, but our host contract must also validate shape/dtype, route identity,
producer timestamp, active model generation and invalidation on model/source
switch. The reference mapsd emits a zero timestamp with an invalid black image;
the inspected consumer checks map receive age, so we must not treat every
recently received image as valid navigation evidence.

GPU execution, recurrent-state reset/recovery, renderer resources and model
fallback remain host integration work, not plugin-controlled operations.

### Validation in this stage

69 offline tests passed: existing snapshots, route admission, navigation
encoding and 13 new geometry cases. A separate differential run against the
fixed downloaded source passed 140 command cases and 75 sequential poses using
synthetic routes. These counts describe different checks and are not added into
one coverage claim. No real map tiles, GPS, camera/model inference, network
listener or vehicle execution was used. Source remains a local candidate;
published dist/index and device software are unchanged.

## Offline host map/model contract (2026-10-27)

The companion host now contains `openpilot.tesla.navigation_inputs`: identity,
map/instruction frames and an offline `ModelInputAdapter`. See its
`docs/design/navigation-model-inputs.md` for the exact ABI, clock and lifecycle
contract. This is not a registered live host capability; no manifest hook is
added and service.py does not call it.

`model_packet.build_model_packet` connects the local RouteStore and command
encoder to that contract using an externally supplied offline MapFrame and
complete steps. Wrong route/coordinate identity, stale maps, missing steps or
route loss revoke both inputs. The adapter checks original observation ages,
copies buffers, adapts either verified model shape/dtype pair and produces
zero arrays on failure. Its reset_required flag does not itself reset a model.

The current packet contract treats contents at each original timestamp as
immutable. It deliberately does not extrapolate progress or regenerate a map
under the same source timestamp; a future derived-frame contract must preserve
original freshness separately. No fabricated complete route instructions or
map pixels are used outside synthetic tests.

Focused offline regression now passes 73 tests, including four producer-to-host
packet cases. The host navigation_inputs_check also passes. Real map rendering,
cross-process transport, live authority and actual model execution remain
unconnected. Local candidate only; no device, store or network change.

## Upstream-only migration (2026-10-28)

The offline frame types and ModelInputAdapter now live in this plugin's
model_inputs.py, copied from the prior pure host candidate at 5b8940e. The packet
builder and its tests no longer import openpilot.tesla.navigation_inputs. This
keeps offline preparation runnable against a stock host without installing a
private host module. The adapter is not a live authority or model input hook.

The live 0.1.1 ingress path still requires navigation_ingress, absent from stock
upstream 23eac2c. Its requirement remains in place. No replacement listener was
added and no unavailable model inputs are advertised as connected.

## Native bridge follow-up

The 0.3.0 candidate now replaces the live ingress implementation with consumption
of the existing external_compute pluginMsg envelope. No bridge/listener was
started. The legacy App still needs a compatible sender. Map/model inputs remain
offline and no model role is replaced. See README.md for the new transport and
persistent replay lifecycle. Prior navigation_ingress requirements above are
historical, not the current 0.3.0 manifest.
