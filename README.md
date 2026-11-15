[English](README.en.md) | 简体中文

# stellar-ai-helm

在游戏运行时，通过《群星》自身的原生命令路径指挥 Stellaris 4.5.2 的 MCP 服务器。原生适配器校验 `stellaris.exe` 的精确 SHA-256，构建或哈希不符时拒绝原生控制。

推荐入口是 `scripts/serve_existing_game.py`：由你手动启动游戏、读档并保持暂停，MCP 只附加当前这一局，不启动也不关闭游戏。仓库：<https://github.com/qewewDA/stellar-ai-helm>。

## 它是什么

stellar-ai-helm 把一局已暂停的单人、非铁人战役接到 MCP 客户端（例如 DeepSeek Harness）。模型通过标准 MCP 工具观察帝国状态，从游戏当前允许的选项里挑选动作，再沿游戏 AI 使用的原生命令路径下单。Python 导入包名仍是 `stellaris_autopilot`，现有命令行入口 `stellaris-autopilot` 保持不变，并增加了 `stellar-ai-helm` 别名。

当前原生后端只支持玩家国家 `0`。已验证构建为 `Cygnus v4.5.2 (9776)`，`stellaris.exe` 的 SHA-256 为 `400df27c82ddc845aa9dce79bd468d81f18f060d299cd263e3afa93aef4f7a83`，Frida 固定为 `17.9.4`。这是固定版本、固定样本上的实机结果，不能外推到全部 DLC、全部起源或未来版本。

## 功能

在附加已运行的 4.5.2 并加上 `--extended` 后，当前接口覆盖：

- **研究**：指定领域选择科技
- **建造**：区划、建筑的入队、升级、拆除、取消与队列调整
- **区域特化**：按星球、区划和槽位列出当前特化与可选项；用 `convert_zone(planet_id, district_id, slot_index, target_zone_id)` 在空槽新建或替换已有特化
- **政策 / 法令**：列出并设置政策，激活或关闭法令
- **决议**：列出并颁布星球决议
- **物种权利**：列出各物种当前权利，并设置主物种权利
- **恒星基地**：模块、建筑、防御平台
- **舰船 / 殖民 / 舰队**：造舰、殖民船、勘察、建站、殖民、舰队订单与姿态
- **陆军**：在单颗星球招募，或从恒星基地向该星系各空闲己方星球同时开造
- **宣称**：对星系增加一层宣称
- **外交**：开关边境、侮辱、宿敌、使馆、改善或恶化关系，以及商业协定、研究协定、移民条约、互不侵犯等提议
- **战争与和平**：宣战与提出和约（`status_quo` / `surrender` / `demand_surrender`）
- **舰船设计**：将已有舰船改为帝国已有设计；查阅船体尺寸、区块、部件数值和当前设计（知识库缺值会标为未知，不编造）
- **事件**：仅处理玩家（国家 0）的待处理事件并选择选项
- **时间与检查点**：暂停或恢复、检查点（写入 `save games/qs_mcp`，不覆盖原存档）、断开连接
- **批量执行**：`execute_plan` 一次串行执行 1 到 20 步

工具存在不等于每一项都已在同一局实机走完全流程。逐项说明与实机状态见 [docs/usage-4.5.2.md](docs/usage-4.5.2.md)。

不加 `--extended` 时，只开放已有实机证据的基础操作：`select_research`、`queue_district`、`queue_building`、`set_paused`，以及 `issue_order` 的 `fleet_wait` 与 `fleet_orbit`。

仓库里还保留了启动器管理的持久战役入口 `scripts/serve_campaign.py`，说明见 [docs/campaign.md](docs/campaign.md)。日常推荐仍是附加已运行的游戏。

## 可用性如何判定

决议、政策、法令、物种权利选项、区划、建筑、区域特化，以及战争目标等需要「从一组选项里挑」的内容，都以游戏自带校验器当时的 potential / allow 判定为准，而不是固定清单。观察结果只列出当前通过前提的项，并标明现在能否颁布或建造；不能的，若游戏给了原因会一并附上。查不到的事实标为 `known: false` 或未知，不会改写成空列表，也不会编造数值。

过期的观察不要继续下单；每次观察都会按当时帝国状态重新计算。

## 快速模式与严格核验

默认是**快速模式**：游戏校验器接受命令后立即返回 `command_accepted`（`deferred_verification: pending`），不在每一步后打存档核验。随后一次 `request_observation`，或在有待核验项时调用 `session_brief`，会打一份存档，用 `deferred_verification.verifications[]`（字段为 `id` / `action` / `status` / `reason`，status 为 `verified` / `failed` / `unverified`）统一结算。`observe_empire` 只读、不打 dump；若仍有未结算的写入，会标 `deferred_verification.pending=true`。

需要逐步严格核验时，调用 `execute_plan(..., verify=true)`，或设置环境变量 `STELLARIS_AUTOPILOT_VERIFY=1`。此时成功结果为 `command_verified`。`blocked` 表示命令未发出。核验结果为 `failed` 或 `unverified` 的操作会留在日志里，**不要换 ID 重放**。

## 运行要求

- Windows 10/11（实机原生命令只在 Windows 上可用）
- Stellaris 4.5.2，且 `stellaris.exe` 哈希与上面的精确 SHA-256 一致
- Python 3.11+，使用 [uv](https://docs.astral.sh/uv/) 安装
- Frida 17.9.4（`native` 额外依赖，仅 Windows 生效）
- 单人、非铁人存档；由你手动启动游戏（不要带持久战役启动器的 `-qsowned=` 标记），读档后保持暂停

知识库 JSON（`--knowledge-bundle`）不随仓库分发。政策、物种权利、恒星基地模块与建筑、陆军类型和舰船知识等工具需要它来核对定义。未显式指定时，默认会尝试仓库相对路径 `artifacts/qs-game-knowledge.json`，不存在则不启用知识工具。

## 安装

在仓库根目录，Windows PowerShell：

```powershell
uv sync --frozen --extra dev --extra mcp --extra native
uv run python scripts/serve_existing_game.py --help
```

`mcp` 提供 MCP 服务端，`native` 安装 Frida。没有原生依赖时可以跑离线解析、测试和只读 MCP，但不能建立原生控制。

## MCP 客户端配置

先正常启动群星、读入要接续的存档并暂停，然后用 stdio 启动本服务。下面的参数形式与 DeepSeek Harness「群星自动驾驶」预设一致，请把路径换成你本机的实际位置：

```json
{
  "mcpServers": {
    "stellaris": {
      "command": "<repo>\\.venv\\Scripts\\python.exe",
      "args": [
        "<repo>\\scripts\\serve_existing_game.py",
        "--state-dir", "<attached-state>",
        "--game-dir", "<Steam>\\steamapps\\common\\Stellaris",
        "--extended",
        "--knowledge-bundle", "<path-to-knowledge.json>"
      ],
      "cwd": "<repo>"
    }
  }
}
```

用户目录默认从 `game-dir\userdir.txt` 或 Documents 解析，也可用 `--user-dir` 指定，且必须与游戏实际使用的目录一致。同机有多个 `stellaris.exe` 时在本机加 `--pid`，不要让模型猜。

连接成功后可用 `attached_status()`、`checkpoint_game()` 和 `disconnect_game()`：检查点写到游戏的 `save games/qs_mcp`，断开只释放附加，游戏继续运行。

## 推荐循环

1. `session_brief()`：尚未连接时会以 `connect_game(retry=false)` 连接一次（失败不自动重试；游戏未暂停则不 dump）。已连接且没有待核验写入时，只读最新快照；有待核验项时打一份 dump 并结算。
2. 只从本轮观察给出的当前可用列表里挑选，用一次 `execute_plan` 批量写入。
3. 若需要时间推进，再使用 `set_paused` 恢复或暂停。
4. 用 `request_observation`（或有待核验项时的 `session_brief`）统一结算延期核验。任一项为 `failed` 或 `unverified` 就停下来报告，不要重试。

## DeepSeek Harness 预设「群星自动驾驶」

这是为本 MCP 单独维护的 DeepSeek Harness Agent 预设，包名为 `@local/dsh-stellaris-mcp-preset`，不在本仓库内。当前维护副本在 `C:\Users\qwqwq\agent-tools\deepseek-harness-stellaris\`，安装目标是其中的 `bundle`。把该目录下的 `INSTALL-PROMPT.md` 全文贴进 DeepSeek Harness 的「创造模式」，按提示安装后，新建会话并选择「群星自动驾驶」。若提示已经装过，需要先卸载再安装。模型和温度在会话设置里选择，不能写进预设。

## 安全约定

- 连接有暂停门禁：游戏必须已经暂停。不要绕过这道检查，也不要用模拟按键替用户点继续。
- 不要启动、关闭或重启游戏；本入口不负责开关游戏进程。
- 不要切换到游戏原生 AI 托管。内阁、传统和飞升选择留给使用者。
- 只处理玩家（国家 0）事件，拒绝其他国家的事件。
- 结果不明确的操作记入 journal，保留为 `unverified`，**不要换新 ID 重放**。
- 不要猜测 PID，也不要猜测决议、政策、法令、权利、战争目标、星港或建造/特化的 ID；只从当前观察给出的可用列表里选。

## 已知限制

- 领袖招募与任命、联邦、附庸、星区管理尚未实现。
- 非主物种的权利选项目前标为未知：游戏原生命令路径只处理主物种。
- 原生命令仅支持哈希相符的 Stellaris 4.5.2。
- 互不侵犯条约可能被游戏引擎内部的硬编码检查拒绝，且不返回原因文本；原因尚未定位，暂不可靠。
- `end_rivalry` 受「宣布宿敌后十年内不能结束」的游戏规则限制，尚未实机验证。

## 更多文档

完整工具清单、参数和逐项实机状态见 [docs/usage-4.5.2.md](docs/usage-4.5.2.md)。附加已运行游戏的连接细节见 [docs/attach-existing.md](docs/attach-existing.md)。

本仓库只包含源码、Mod 模板和测试，不附带游戏本体、知识 JSON、真实存档或本机 MCP 配置。许可证为 MIT。
