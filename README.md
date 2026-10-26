# xpilot-plugins

Plugin repository for [xpilot](https://github.com/onemiless/xpilot) (Tesla-only sunnypilot fork).
Devices list this repository under Settings → Plugins → Plugin Store, install while parked, and apply on restart.

插件仓库：xpilot 设备在 设置 → Plugins → Plugin Store 里浏览这里的插件，停车时安装，重启后生效。
接口、类别、资源声明和底线规则见 xpilot 仓库根目录的 `XPILOT.md`。

## Layout

```
plugins/<id>/            plugin source: __init__.py, plugin.json (manifest), plugin.py, optional ui.py and modules
dist/<id>-<ver>.tar.gz   reproducible archives, generated
index.json               what devices read: id, version, api, category, url, sha256, generated
tools/build_index.py     builds dist/ and index.json; --check verifies they are current
```

## Submitting a plugin / 提交插件

1. Fork, add `plugins/<id>/`, open a pull request (see `CONTRIBUTING.md`). Read `XPILOT.md` first.
2. `plugin.json`: `id` equals the directory, `api` is the host's API version (1), a category from `XPILOT.md`,
   required `use` (`onroad`, `offroad`, or `both`) for its usage context,
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
   there is no translation layer. Store plugins are pure plugin/API extensions: all rule data
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

Moved from the xpilot repository (keep their original param keys):

- Display, installed by default on first boot: `speed_card`, `lead_card`, `speed_limit_card`, `car_tile`, `map_tile`,
  `clock_tile`, `ip_tile`, `blindspot_edges`.
- `bms`: battery tile. `traffic_light`: traffic light stop/start. `external_compute`: external compute bridge.
- Runtime Panda rules (need host/firmware ABI 1): `auto_speed` (0x3C2), `ambient_light` (0x679), both 1.1.0.
  The old 1.0.x archives are withdrawn. X-20261026-57 removes the legacy system headers and
  differential oracle; these published runtime rules stay unchanged. Merge the companion system PR
  first, then this documentation PR. No plugin archive rebuild or device flash is part of this change.

### Pure plugin/API migration: X-20261026-58 (blocked, 2026-10-26)

所有商店插件都必须通过通用插件 API 传递自己的数据：声明 `msg:<kind>`，使用
`plugins.send_msg` / `PluginMsgs`，不扩展系统 cereal。`traffic_light` 的控制继续只通过 `plan.filter`。
当前 `traffic_light` 和 `bms` 1.0.2 仍访问历史专用字段，尚未完成迁移；本次没有版本升级或新包发布。

阻碍是 `pluginMsg` 只有一个实际发布端，迁移却至少需要 card 和 plannerd 两个进程。
系统的 `tools/sp_tesla_e2e/plugin_msg_e2e.py` 用真实 IPC 在约一秒内成功送达单发布端的
100 条观测、100 条 BMS、20 条计划诊断。默认值、8 字节 CAN 十六进制及采样序号的代表性
wire 消息为 592/176/800 字节，名义合计 92,800 B/s；这是主机检查，不是实车带宽或容量上限。
第二进程发送会替换原发布端，原端随后报 `Address already in use`。BMS 即使降为 2 Hz 也不能解决
发布端冲突；card 当前也没有共享 publisher 参数。不能靠插件各自创建 `PubMaster` 完成迁移。

先补通用多来源发布能力、并发/故障/过期验证，再迁移并升级插件、重建 dist/index；不新增专用系统字段。
复现时在 xpilot 的依赖环境运行 `python3 tools/sp_tesla_e2e/plugin_msg_e2e.py`，当前退出码为 **1**，
JSON 中 `migration_ready` 为 `false`。系统 PR 先合、插件 PR 后合，均需人工审核。
新插件只有完全不再访问旧字段、且旧系统支持所需消息 API 时才能先装到旧系统；现有 1.0.2 不满足。
在迁移完成前不能先重命名系统字段；完成后保留 ordinal/类型，改名 `deprecated*` 并停止所有写入。

Tests: `cd plugins && python3 -m pytest -q .` with an xpilot checkout on `PYTHONPATH` (see `.github/workflows/ci.yml`).
Run `python3 tools/runtime_rules_check.py --output /tmp/runtime-rules` with that same `PYTHONPATH` to validate
source and archives against the real host, compare AUTO/AMBIENT reference bytes and exercise card setup.
It saves `result.json` with source identities/hashes, table binaries and the CRC-bearing card param.

## Native plugins / 带二进制的插件

Plugins with compiled code ship prebuilt binaries for one platform: `plugin.json` gets
`"platform": {"machine": "aarch64", "python": "cpython-312"}` (written by the plugin's `build_native.py`, run on the
target device). Devices only offer and load a native plugin on a matching platform. Binaries cannot be reviewed like
source: review the build script, and rebuild them yourself on your device before merging.
