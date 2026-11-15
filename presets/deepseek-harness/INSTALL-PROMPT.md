# 请把本文从下一行起到文末全部复制，粘贴到 DeepSeek Harness 的「创造模式」会话中发送。

请把「群星自动驾驶」更新到已写好的 v1.6.0，不要另起不同的预设，也不要重写 bundle。加载 `dsh-doc` 后按官方流程安装。

## 安装前先改路径

请先打开本仓库中的 `presets/deepseek-harness/bundle/cordis.patch.yml`，把下列占位符换成你本机的实际路径后再安装：

- `<REPO>`：本仓库的克隆或工作副本根目录（该目录下应有 `scripts/serve_existing_game.py` 和 `.venv`）。
- `<STEAM>`：含有 `steamapps/common/Stellaris` 的 Steam 库目录。
- `<PATH-TO-KNOWLEDGE.json>`：游戏知识库 JSON 的路径；该文件不随本仓库分发。

## 安装目标

`install_bundle` 的 `target` 必须是本仓库里 `presets/deepseek-harness/bundle` 的绝对路径。需要 Full access 或当次批准。

## 若已经装过（同一包名必须先卸再装）

包名为 `@local/dsh-stellaris-mcp-preset`。开启 HMR 时对已装包直接 `install_bundle` 会 `already-installed`。请先 `list_bundles`：已有则先 `remove_bundle`（目标 `@local/dsh-stellaris-mcp-preset`），再 `install_bundle`；没有则直接安装。不要创建第二个 bundle，不要手改 profile。

装完后新建会话，选择「群星自动驾驶」，确认能看到 `mcp__stellaris__session_brief`。不要在安装过程中启动或关闭群星。
