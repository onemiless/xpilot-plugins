# Traffic Light 1.1.0

读取 AP-PARTY bus 2 的 0x25D，不发送 CAN。card 每 50 ms 最多发送一条 `traffic.observation`
（claim `msg:traffic.observation`，TTL 750 ms），包含原始帧时间、bus、DLC、地址及 `rawPayload` 十六进制字符串。
trafficcontrold 通过 `PluginMsgs.latest("traffic.observation", "traffic_light")` 接收，继续校验原始帧年龄，
在 modelV2 更新时发送 `traffic.target`（claim `msg:traffic.target`，TTL 350 ms）。目标保留现有字段和 valid 标志，
`TrafficRadarState` 仅用作本地构建数据的既有类型，不再发布同名 cereal 服务。

plannerd 的 `plan.filter` 从 `PluginMsgs.latest("traffic.target", "traffic_light")` 读取目标，保留原有
STOP/session、驾驶员覆盖、失联退化及终端低速保持规则。只有该 filter 可以改变最终计划；本次不改变控制算法。
发布最终计划时发送 `traffic.plan`（claim `msg:traffic.plan`，TTL 350 ms），磁贴从同 source 读取诊断，过期即清空。
三个发送进程正常合计最多约 60 Hz，共享 broker 的每来源限制。缺失 broker 时非阻塞丢弃，不重试。

先合并并部署含 `plugin_msgd` 的系统（最低 `4d149dc0e52c3038204b5f5e573680c30b1499ed`），再安装本插件。
`requires.msg` 不识别更早的单发布端实现，部署顺序必须由操作者保证。总开关仍为 `TeslaTrafficSignalControlEnabled`，
停止参考和最大控制车速设置不变。无设备或道路验证。

重复验证（系统及其依赖在 PYTHONPATH）：

```sh
cd plugins
python3 -m pytest -q -s traffic_light/tests/test_plugin_messages.py
python3 -m pytest -q traffic_light/tests
```

第一个命令输出可核验 JSON，覆盖真实 Unix socket/broker/cereal、原始 bytes、节流、来源、TTL、filter 发布与磁贴。
manager/Params 为夹具；系统 broker E2E 另测真实 Linux 凭据、多进程及重启。
