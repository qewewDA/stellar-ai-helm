# 4.5.2 附加模式使用说明与工具清单

本文是“手动启动的 4.5.2 游戏 + `serve_existing_game.py`”这条日常入口的速查。连接细节、身份校验和常见问题见 [attach-existing.md](attach-existing.md)，早期工具的逐项证据见 [direct-control.md](direct-control.md)。

## 1. 准备

1. 游戏版本必须是项目固定哈希的 4.5.2（`stellaris.exe` SHA-256 不符直接拒绝）。单人、非铁人存档。
2. 用户目录：游戏按 `<Steam>\steamapps\common\Stellaris\userdir.txt` 选择用户目录；MCP 的 `--user-dir` 必须指向同一个目录（不填则按同一规则解析）。
3. 正常启动游戏（不要带 `-qsowned=`，那是持久战役启动器的标记），读档后保持**暂停**。
4. 一台机器只开一个游戏；多开时由人在本机用 `--pid` 指定。

## 2. 启动 MCP

```powershell
.\.venv\Scripts\python.exe scripts\serve_existing_game.py `
  --state-dir <附加状态目录> `
  --game-dir "<Steam>\steamapps\common\Stellaris" `
  --extended
```

- `--extended`：开放本文第 4 节的全部玩法工具（不加时只有研究/区划/建筑/暂停与两种舰队订单）。
- `--allow-test-event`：仅测试档使用。开放 `test_setup_*` 与 `establish_test_contact` 等测试动作（控制台式设置：资源、星港升级、传统前置、建交、好感），**不要在正式存档上开启**。
- `--knowledge-bundle`：可选的版本化知识库；政策、物种权利、恒星基地模块/建筑、陆军类型等工具需要它来核对定义。

## 3. 典型流程

1. `connect_game()`（游戏须已暂停）→ `attached_status()` 确认 `connected`。
2. `observe_empire()` 读状态；需要最新数据时先 `request_observation()`。
3. 调用玩法工具。每个写操作：本地校验 → 正常原生命令 → 游戏自带校验器 → 新存档转储比对后置条件。返回 `command_verified` 才算成功；`blocked` 带原因，未发出命令。
4. 推进时间：`set_paused(false)` / `set_paused(true)`，或 `execute_plan` 批量执行。
5. 结束：`checkpoint_game()`（检查点写入 `save games/qs_mcp`，不覆盖原存档）→ `disconnect_game(save=false)`。游戏保持运行。

结果不明确的操作会留在日志中（`status()` 的 `unresolved`）；**不要换新 ID 重放**。

## 4. 玩法工具与实机状态（2026-11-15，D 盘测试局 2200 年）

| 类别 | 工具 | 状态 |
| --- | --- | --- |
| 连接/时间 | `connect_game` `attached_status` `checkpoint_game` `disconnect_game` `set_paused` `probe_connection` | 实机通过 |
| 观察 | `observe_empire` `request_observation` `list_actions` `status` | 实机通过 |
| 研究/建设 | `select_research` `queue_district` `queue_building` `upgrade_building` `demolish_building` `convert_zone` `cancel_construction` `move_queue_item` | 见 direct-control.md |
| 造舰/舰队 | `queue_ship` `queue_colony_ship` `issue_order` `survey_planet` `build_station` `colonize_planet` `set_fleet_stance` | 见 direct-control.md；`fleet_wait` 本轮复测通过 |
| 政策/法令 | `list_policies` `set_policy` `activate_edict` `deactivate_edict` | `set_policy`（贸易政策）实机通过 |
| 物种权利 | `list_species_rights` `set_species_right` | 实机通过 |
| 星球决议 | `list_planet_decisions` `enact_planet_decision` | 见 direct-control.md |
| 恒星基地 | `queue_starbase_module` `queue_starbase_building` `queue_defense_platform` | 模块/建筑实机通过（需空槽位） |
| 陆军 | `recruit_army`（单星球，一次一支，队列非空即拒绝）`recruit_armies_at_starbase`（该星系每个空闲己方星球各一支，完工后集结到该恒星基地） | 两条路径实机通过；多星球扇出仅离线测试 |
| 宣称 | `add_system_claim`（一次 +1 宣称，消耗影响力；`state.claims.by_system` 读取） | 实机通过（对宿敌首都星系 596 叠到 2 层） |
| 事件 | `list_pending_events` `choose_event_option` | 实机通过（不处理 AI 国家事件） |
| 外交 | `list_diplomacy`（含 `pacts`、`our_proposals`、`recent_proposals`） | 实机通过 |
| 外交（单方面） | `close_borders` `open_borders` `insult` `make_rival` `recall_embassy` `improve_relations` `harm_relations` | `make_rival`、`recall_embassy`、派遣使节实机通过 |
| 外交（提议） | `propose_diplomacy`：embassy / commercial_pact / research_agreement / migration_pact / non_aggression_pact | 使馆被接受；商业/研究/移民提议送达，AI 自行拒绝（`recent_proposals` 显示 declined_or_expired） |
| 战争 | `list_wars` `declare_war(target_country_id, war_goal)` `offer_peace(target_country_id, terms, war_id)` | 实机通过：对 66（Saiiban League）`wg_conquest` 宣战得到战争 `0`（防御方含 65/66/16777218）；随即 `status_quo` 求和，经 `our_proposals` 中一条新的 `action_offer_peace` 验证（战争仍在，待对方接受）。`terms`：`status_quo` / `surrender` / `demand_surrender`。 |
| 舰船设计 | `list_ships` `set_ship_design(ship_id, design_id)` | 实机通过：舰队 3 的船 3 从设计 49（待升级 3057）改为现役设计 3057，升级标记清除。只改一艘已有船的设计，不新建设计。 |

### 已知限制

- **互不侵犯条约**：脚本条件（信任 50、使馆）都满足，仍被游戏引擎内部的硬编码检查拒绝，且不返回原因文本（校验函数 0x9bd040 中无提示的关系位/国家检查）。原因未定位，暂不可用。
- **结束宿敌** `end_rivalry`：游戏规定宿敌宣布后 10 年内不能结束（测试局为 2210.01.24 前），未实机验证。
- **未实现**：联邦与附庸、星区管理、领袖招募与任命。宣战/和平与舰船设计选择已实机验证，见上表。
- 宏观国策仍由使用者决定；本项目不会切换到游戏原生 AI 托管。
