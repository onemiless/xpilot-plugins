# Battery (BMS) 1.1.0

只读 vehicle bus 1 的 0x132/0x292，需要相应线束。总开关仍为 `XpilotBms`，默认关闭。
card 最多 2 Hz 使用 `plugins.send_msg` 发布 `bms.state`（claim `msg:bms.state`）。
payload 为 `available`，可用时含 `soc`（%）、`voltage`（V）、`current`（A，放电为正）；磁贴计算 kW。
TTL 最多 2000 ms，并按两条 CAN 帧中最旧一条的剩余寿命缩短，缺帧或过期发布 `available=false`。
磁贴通过 `PluginMsgs.latest("bms.state", "bms")` 读取，缺失、不可用或过期显示 `--`。
不再访问 `CarStateSP` 专用字段，不发送 CAN。

先部署包含 `plugin_msgd` 的系统（最低 `4d149dc0e52c3038204b5f5e573680c30b1499ed`），再安装插件。
`requires.msg` 不识别旧单发布端实现，须遵守部署顺序。broker 缺失时不阻塞、不重试，已有缓存自然过期。
电流符号及物理总线仍来自现有 DBC 假设，未做设备验证。

验证（系统及依赖在 PYTHONPATH）：`cd plugins` 后运行
`python3 -m pytest -q -s traffic_light/tests/test_plugin_messages.py bms/tests`。
实际消息 E2E 输出 JSON，覆盖 CAN 解码、broker、接收和磁贴状态；不证明物理线束或设备渲染。
