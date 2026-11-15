# 群星自动驾驶 v1.6.0

把 `INSTALL-PROMPT.md` 全文贴进 DeepSeek Harness 的 **创造模式**。安装目标是本仓库克隆目录里 `presets/deepseek-harness/bundle` 的绝对路径。

## 安装前先改路径

打开 `bundle/cordis.patch.yml`，把下列占位符换成你本机的实际路径：

- `<REPO>`：本仓库的克隆或工作副本根目录（该目录下应有 `scripts/serve_existing_game.py` 和 `.venv`）。
- `<STEAM>`：含有 `steamapps/common/Stellaris` 的 Steam 库目录。
- `<PATH-TO-KNOWLEDGE.json>`：游戏知识库 JSON 的路径；该文件不随本仓库分发。

包名 `@local/dsh-stellaris-mcp-preset`。已安装时先 `remove_bundle` 再 `install_bundle`，不要重写 bundle，也不要另建一份。安装过程中不要启动或关闭群星。然后新建会话选择「群星自动驾驶」，确认能看到 `mcp__stellaris__session_brief`。

v1.6.0 采纳 MCP 12:40 语义：区域特化使用 `convert_zone(planet_id, district_id, slot_index, target_zone_id)`；目标只从该槽 `available_zones` 里选 `enactable=true` 的模板，不猜 `zone_id`。brief 含 `planets[].zone_slots[]`。城市区 3 槽，发电/采矿/农业区各 1 槽。连接后第一次观察可能约 13–20 秒，不要重试。不要在 MCP 配置里设置 `STELLARIS_AUTOPILOT_VERIFY`。
