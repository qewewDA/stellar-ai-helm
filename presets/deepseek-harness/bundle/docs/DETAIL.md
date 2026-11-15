# 群星自动驾驶（v1.6.0）

DeepSeek Harness 自定义预设（id：`stellaris-mcp`）。

推荐循环：`session_brief` → 只从本轮可用性清单挑选 `enactable=true` 的项，用 `execute_plan` 批量写入 → 必要时推进时间 → 再一次 `session_brief` 或 `request_observation` 结算 `deferred_verification`。

- 区域特化：`convert_zone(planet_id, district_id, slot_index, target_zone_id)`。空槽新建，已占用槽替换。`target_zone_id` 只从该槽 `available_zones` 且 `enactable=true` 的模板中选，不要猜 `zone_id`。
- brief 的 `planets[].zone_slots[]` 含 `district_id` / `district_type` 与 `slots[]`（`slot_index`、`slot_key`、`occupied`、`current` 或空、`available_zones[]`）。城市区 3 槽（政府 + 2 个任选特化）；发电、采矿、农业区各 1 槽。
- 连接后第一次观察可能约 13–20 秒，属正常，不要重试；同一快照后续观察走缓存。
- 写入默认快速模式；建造与法令可用性规则与 v1.5.0 相同。

安装前先在 `bundle/cordis.patch.yml` 中把 `<REPO>`、`<STEAM>` 和 `<PATH-TO-KNOWLEDGE.json>` 换成你本机的实际路径。安装目标是本仓库里 `presets/deepseek-harness/bundle` 的绝对路径。已安装同一包名时先 `remove_bundle` 再 `install_bundle`，然后新建会话。
