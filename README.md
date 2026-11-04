# xpilot-plugins

Plugin repository for [xpilot](https://github.com/onemiless/xpilot) (Tesla-only sunnypilot fork).
Devices list this repository under Settings → Plugins → Plugin Store, install while parked, and apply on restart.

插件仓库：xpilot 设备在 设置 → Plugins → Plugin Store 里浏览这里的插件，停车时安装，重启后生效。
接口、类别、资源声明和底线规则见 xpilot 仓库根目录的 `XPILOT.md`。

## Layout

```
plugins/<id>/            plugin source: __init__.py, plugin.json (manifest), plugin.py, optional ui.py and modules
dist/<id>-<ver>.tar.gz   reproducible archives, generated
index.json               what devices read: id, version, api, category, url, sha256, default, generated
tools/build_index.py     builds dist/ and index.json; --check verifies they are current
```

## Submitting a plugin / 提交插件

1. Fork, add `plugins/<id>/`, open a pull request (see `CONTRIBUTING.md`). Read `XPILOT.md` first.
2. `plugin.json`: `id` equals the directory, `api` is the host's API version (1), a category from `XPILOT.md`,
   required `use` (`onroad`, `offroad`, or `both`) for its usage context,
   optional boolean `default: true` for first-boot installation (absent means false),
   `claims` for everything the plugin uses, params with types BOOL/INT/FLOAT/STRING/JSON. Downloaded plugins
   must not reuse built-in ids or param keys; prefix new keys with `Ext`. The plugins moved here from the xpilot
   repository keep their original key names so existing device settings stay valid.
   Tiles must match `use`: `onroad` excludes offroad tiles, `offroad` excludes onroad tiles, and
   `show="always"` requires `both`. This declaration does not change hook runtime conditions.
3. Use relative imports inside the plugin (`from .logic import x`): installed plugins load as `xpilot_ext.<id>`.
4. Prefer pure Python. Compiled code must ship as prebuilt binaries with a `platform` and a `build_native.py`
   that reproduces them (see Native plugins). CAN senders declare `rules` (`template_clone.v1`) and
   `requires: {"rule_abi": 1}`; `can.tx` claims must equal the rules' TX address/bus pairs. The host validates
   and compiles these rules for the Panda runtime engine. Legacy `safety` manifests are rejected;
   there is no translation layer. Sensor frames a device needs while disengaged (e.g. ARS408 speed/yaw)
   add `"sensor_input": true`: the RX frame then only proves presence, and fields/constants, TTL, interval and
   budget still apply (system XPILOT.md section 6). Store plugins are pure plugin/API extensions: all rule data
   stays in the plugin manifest; no plugin-specific C header, registry or build hook lives in the system.
5. Run `python3 tools/build_index.py` and commit `dist/` and `index.json` with the source.
6. Describe in the PR what the plugin changes on the road, which claims it takes, and how you tested it.

## Review checklist (maintainer) / 审核清单

- `python3 tools/build_index.py --check` passes on the PR head (archives match the reviewed source).
- From an xpilot checkout: `python3 tools/sp_tesla_e2e/store_e2e.py --repo <this checkout>` passes.
- Claims match the code: every CAN address sent, hook used and process started is claimed; nothing reads
  or writes outside its own params and messages; no network access unless it is an external compute provider.
- Control plugins (lateral, longitudinal, lane change, engagement, ...) state their floors and failure behaviour.
- No obfuscated code, no downloaded or generated code at runtime, no subprocess or shell use.

Archives are not signed yet: the sha256 in `index.json` only protects against corrupt or swapped downloads.
Trust comes from this reviewed repository, so only merge what you have read.

## Plugins

- `plugins/overspeed_warning`: example. Alerts category, one store-backed setting, an `alert` claim, a relative import.
- `plugins/tn_long`: TN longitudinal (TN-NoDEC) as a `long_planner`, with prebuilt acados solvers. See its README.
- `plugins/tesla_mixed_control`: dzid26 angle-based cooperative steering. Enable while parked and restart;
  SP must own steering and stock Autosteer must not steer concurrently. See its README for timing and host-only validation limits.
- `plugins/lang_zh_chs`: complete Simplified Chinese language pack for the xpilot interface.

Moved from the xpilot repository (keep their original param keys):

- Display, installed by default on first boot: `speed_card`, `lead_card`, `speed_limit_card`, `car_tile`, `map_tile`,
  `clock_tile`, `ip_tile`, `blindspot_edges`.
- `bms`: battery tile. `traffic_light`: traffic light stop/start. `external_compute`: external compute bridge.
- Runtime Panda rules (need host/firmware ABI 1): `auto_speed` (0x3C2), `ambient_light` (0x679), both 1.1.0.
  The old 1.0.x archives are withdrawn. X-20261026-57 removes the legacy system headers and
  differential oracle; these published runtime rules stay unchanged. Merge the companion system PR
  first, then this documentation PR. No plugin archive rebuild or device flash is part of this change.

`default` is store metadata, separate from the enable param. The builder copies it to `index.json`;
the host chooses first-boot packages from that flag and keeps its one-time `.defaults_done` marker.
An old index with no flagged packages leaves the marker unset and is retried after the plugin PR merges.
The eight display packages above carry the flag; no plugin ids are registered in the host.

`external_compute` 1.0.3 owns its TCP provider and only needs `process:externald` and `msg:external.in`.
It publishes `pluginMsg` kind `external.in`, with the TCP dictionary (`source`, `kind`, `seq`, JSON-text
`payload`, `validForMs`) as payload; outer source is `external_compute` and outer TTL matches the peer TTL.
In-process consumers use `PluginMsgs().fresh("external.in")` and their own checked advisory hooks.
For outbound data, a sender claims `msg:external.out` and uses `send_msg` with the TCP dictionary as payload.
The bridge forwards only fresh `external.out`, sets TCP `source` to the sending plugin id and never echoes
inbound messages. Inbound and outbound publishers run concurrently through the host's `plugin_msgd`
broker; neither the bridge nor sending plugins create a `pluginMsg` PubMaster. Outbound events are
forwarded once each, including repeated identical payloads. `PluginMsgs` consumers read fresh inbound data.
The broker authenticates externald using its `process:externald` claim, with no external-specific host mapping.
TCP hello/auth/rate/size limits stay in the plugin; the generated token lives in the store-backed
`XpilotExternalToken` param (`/data/xpilot_plugins/.params/`). Plain TCP requires a trusted network.

Merge/deploy the X-20261026-59 system PR first, then this plugin PR; no automatic merge.
external_compute 1.0.1 uses the removed host hook/services; 1.0.2 creates its own publisher.
Upgrade to 1.0.3 with the broker host; deploy only the matching pair after both PRs merge.

### Pure plugin/API migration: X-20261026-58

`traffic_light` 和 `bms` **1.1.0** 使用 `plugins.send_msg` / `PluginMsgs`，不再读写历史
`CarStateSP` / `LongitudinalPlanSP` 专用字段，也不再发布 `trafficRadarState` 服务。
须先部署包含 `plugin_msgd` 的系统（`onemiless/xpilot@4d149dc0e52c3038204b5f5e573680c30b1499ed`
或后续版本），再安装这两个插件；`requires.msg` 只检查消息 API，不能识别旧单发布端实现。
系统侧专用字段仅保留 wire ordinal/type/node ID，改名 `deprecated*`。

| source | kind / claim | 发布进程 / 最高频率 | TTL | 接收方 |
|---|---|---|---|---|
| traffic_light | traffic.observation / msg:traffic.observation | card / 20 Hz | 750 ms | trafficcontrold |
| traffic_light | traffic.target / msg:traffic.target | trafficcontrold / modelV2 20 Hz | 350 ms | plannerd plan.filter |
| traffic_light | traffic.plan / msg:traffic.plan | plannerd / 20 Hz | 350 ms | Traffic Light 磁贴 |
| bms | bms.state / msg:bms.state | card / 2 Hz | 最多 2000 ms，按最旧 CAN 帧剩余寿命缩短 | Battery 磁贴 |

同一个 traffic_light source 的三个进程共享 100 Hz / 256 KiB/s 预算，正常合计最多约 60 Hz。
所有消费者用 `latest(kind, source)` 固定来源。观测保留原始帧时间、bus、DLC、地址和十六进制 payload，
controller 仍检查原始 CAN 的 750 ms 时效；目标还检查模型发布时间的 350 ms 时效。
过期观测不产生新证据，目标丢失走既有仲裁退化/低速保持规则；磁贴过期显示空状态。
`traffic_light` 的控制只经 `plan.filter`，不新增 CAN、planner 或 controller ownership。
详见 [traffic_light README](plugins/traffic_light/README.md) 和 [bms README](plugins/bms/README.md)。

消息 E2E：`cd plugins && python3 -m pytest -q -s traffic_light/tests/test_plugin_messages.py`。
实际执行 CAN 解码 → Unix datagram → broker → cereal → PluginMsgs → controller/filter/tiles，输出 JSON 结果。
manager/Params 使用隔离夹具；跨进程认证、限流和重启由系统 `plugin_msg_e2e.py` 验证。
这不证明设备 CAN、物理 UI 或道路控制，系统 PR 先合/部署，插件 PR 后合/安装，均需人工审核。

Tests: `cd plugins && python3 -m pytest -q .` with an xpilot checkout on `PYTHONPATH` (see `.github/workflows/ci.yml`).
Run `python3 tools/runtime_rules_check.py --output /tmp/runtime-rules` with that same `PYTHONPATH` to validate
source and archives against the real host, compare AUTO/AMBIENT reference bytes and exercise card setup.
It saves `result.json` with source identities/hashes, table binaries and the CRC-bearing card param.

## Native plugins / 带二进制的插件

Plugins with compiled code ship prebuilt binaries for one platform: `plugin.json` gets
`"platform": {"machine": "aarch64", "python": "cpython-312"}` (written by the plugin's `build_native.py`, run on the
target device). Devices only offer and load a native plugin on a matching platform. Binaries cannot be reviewed like
source: review the build script, and rebuild them yourself on your device before merging.
