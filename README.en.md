English | [简体中文](README.md)

# stellar-ai-helm

An MCP server that drives Stellaris 4.5.2 through the game's own native command paths while the game is running. The native adapters check an exact SHA-256 of `stellaris.exe` and refuse native control if the build or hash does not match.

The recommended entry point is `scripts/serve_existing_game.py`: you start Stellaris yourself, load a save, and leave the game paused. The MCP server only attaches to that session; it does not launch or close the game. Repository: <https://github.com/qewewDA/stellar-ai-helm>.

## What it is

stellar-ai-helm connects a paused single-player, non-ironman campaign to an MCP client such as DeepSeek Harness. The model observes the empire through standard MCP tools, picks actions from what the game currently allows, and issues orders along the same native command paths the game AI uses. The Python import package remains `stellaris_autopilot`. The existing `stellaris-autopilot` console script is unchanged, and a `stellar-ai-helm` alias is also provided.

The native backend currently supports only player country `0`. The verified build is `Cygnus v4.5.2 (9776)`. The SHA-256 of `stellaris.exe` is `400df27c82ddc845aa9dce79bd468d81f18f060d299cd263e3afa93aef4f7a83`, and Frida is pinned to `17.9.4`. These are live results on a fixed build and a fixed sample; they do not generalize to every DLC, origin, or future version.

## Features

With a running 4.5.2 session attached and `--extended` enabled, the current interface covers:

- **Research**: select a technology in a given field
- **Construction**: queue districts and buildings, upgrade, demolish, cancel, and reorder the queue
- **District specializations**: list the current specialization and available options for each planet, district, and slot; use `convert_zone(planet_id, district_id, slot_index, target_zone_id)` to fill an empty slot or replace an existing specialization
- **Policies and edicts**: list and set policies; activate or deactivate edicts
- **Decisions**: list and enact planet decisions
- **Species rights**: list current rights for observed species, and set rights for the primary species
- **Starbases**: modules, buildings, and defense platforms
- **Ships, colonization, and fleets**: shipbuilding, colony ships, surveys, stations, colonization, fleet orders, and stance
- **Armies**: recruit one army on a single planet, or start one army on every idle owned planet in a starbase's system
- **Claims**: add one layer of claim on a system
- **Diplomacy**: open or close borders, insult, rivalry, embassies, improve or harm relations, and proposals for commercial pacts, research agreements, migration pacts, and non-aggression pacts
- **War and peace**: declare war and offer peace (`status_quo` / `surrender` / `demand_surrender`)
- **Ship design**: change an existing ship to another design the empire already has; look up ship sizes, sections, component stats, and current designs (missing knowledge values are marked unknown and are never invented)
- **Events**: handle pending events for the player (country 0) only, and choose an option
- **Time and checkpoints**: pause or resume, write a checkpoint to `save games/qs_mcp` without overwriting the original save, and disconnect
- **Batched execution**: `execute_plan` runs 1 to 20 steps serially in one call

The presence of a tool does not mean every path has been live-tested end to end in the same campaign. Per-tool notes and live status are in [docs/usage-4.5.2.md](docs/usage-4.5.2.md).

Without `--extended`, only the basic operations with existing live evidence are exposed: `select_research`, `queue_district`, `queue_building`, `set_paused`, and the `fleet_wait` and `fleet_orbit` orders on `issue_order`.

The repository also keeps a launcher-managed campaign entry, `scripts/serve_campaign.py`; see [docs/campaign.md](docs/campaign.md). Attaching to an already running game remains the recommended daily path.

## How availability works

Anything that requires choosing from a set of options — decisions, policies, edicts, species-rights options, districts, buildings, district specializations, and war goals — is judged by the game's own validator at that moment (`potential` / `allow`), not by a static list. Observations list only options that currently pass their prerequisites, and mark whether each can be enacted or built now. If the game provides a failure reason, it is included. Unknown facts are left as `known: false` or unknown; they are not rewritten as empty lists, and missing numbers are not invented.

Do not keep issuing orders from a stale observation. Each observation is recomputed from the empire's current state.

## Fast mode and strict verification

The default is **fast mode**: once the game's validator accepts a command, the tool returns `command_accepted` (`deferred_verification: pending`) and does not dump a save after every step. The next `request_observation`, or `session_brief` when accepted writes are still pending, takes one dump and settles every accepted operation in `deferred_verification.verifications[]` (fields `id` / `action` / `status` / `reason`; status is `verified`, `failed`, or `unverified`). `observe_empire` is read-only and does not dump; if accepted writes are still unsettled, it sets `deferred_verification.pending=true`.

For the older per-step path, call `execute_plan(..., verify=true)` or set the environment variable `STELLARIS_AUTOPILOT_VERIFY=1`. Strict success is then `command_verified`. `blocked` means the command was never sent. Operations that settle as `failed` or `unverified` stay in the journal; **do not replay them under a new ID**.

## Requirements

- Windows 10/11 (live native commands are available only on Windows)
- Stellaris 4.5.2, with a `stellaris.exe` hash that matches the exact SHA-256 above
- Python 3.11+, installed with [uv](https://docs.astral.sh/uv/)
- Frida 17.9.4 (the `native` extra; Windows-only)
- A single-player, non-ironman save; start the game yourself (do not use the campaign launcher's `-qsowned=` flag), load the save, and leave it paused

The knowledge-bundle JSON (`--knowledge-bundle`) is not shipped with the repository. Tools for policies, species rights, starbase modules and buildings, army types, and ship knowledge use it to check definitions. If you omit the flag, the server tries the repo-relative path `artifacts/qs-game-knowledge.json` and disables knowledge tools when that file is absent.

## Install

From the repository root, in Windows PowerShell:

```powershell
uv sync --frozen --extra dev --extra mcp --extra native
uv run python scripts/serve_existing_game.py --help
```

The `mcp` extra provides the MCP server; `native` installs Frida. Without the native extra you can still run offline parsing, tests, and read-only MCP, but you cannot establish native control.

## MCP client configuration

Start Stellaris normally, load the save you want to continue, and leave it paused. Then start this server over stdio. The argument shape below matches the DeepSeek Harness 「群星自动驾驶」 preset; replace the paths with the ones on your machine:

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

The user directory is resolved from `game-dir\userdir.txt` or Documents by default. You can also set `--user-dir`; it must be the same directory the game is actually using. If more than one `stellaris.exe` is running, pass `--pid` yourself on the machine — do not let the model guess it.

After a successful connection, `attached_status()`, `checkpoint_game()`, and `disconnect_game()` are available. Checkpoints are written to the game's `save games/qs_mcp` folder. Disconnecting only releases the attachment; the game keeps running.

## Recommended loop

1. `session_brief()`: if the session is not connected yet, this calls `connect_game(retry=false)` once (it does not retry on failure, and it will not dump an unpaused game). If already connected and no accepted writes are waiting for verification, it reads the latest snapshot only; if writes are pending, it takes one dump and settles them.
2. Pick only from the currently available lists in that observation, and issue a batch of writes with one `execute_plan`.
3. If the game clock should advance, use `set_paused` to resume or pause.
4. Settle deferred verification with `request_observation` (or `session_brief` when writes are still pending). If any item is `failed` or `unverified`, stop and report it; do not retry.

## DeepSeek Harness preset 「群星自动驾驶」

This repository includes a DeepSeek Harness agent preset at `presets/deepseek-harness/`. The package name is `@local/dsh-stellaris-mcp-preset`. Before installing, replace `<REPO>`, `<STEAM>`, and `<PATH-TO-KNOWLEDGE.json>` in `presets/deepseek-harness/bundle/cordis.patch.yml` with the paths on your machine. Then paste the full text of `presets/deepseek-harness/INSTALL-PROMPT.md` into DeepSeek Harness's 创造模式 (Creation mode), install it as instructed, then open a new session and select 「群星自动驾驶」. If the bundle is already installed, remove it first and install again. The model and temperature are chosen in the session settings; they cannot be set in the preset.

## Safety rules

- Connecting is gated on a paused game. Do not bypass that check, and do not simulate keypresses to unpause or continue for the user.
- Do not start, close, or restart the game; this entry point does not own the game process.
- Do not switch the empire to the game's native AI. Cabinet, tradition, and ascension choices stay with the user.
- Handle events only for the player (country 0). Events for other countries are rejected.
- Ambiguous results are written to the journal and left as `unverified`. **Do not replay them under a new ID.**
- Do not guess a PID, and do not guess IDs for decisions, policies, edicts, rights, war goals, starbases, or construction and specialization templates; pick only from the lists in the current observation.

## Known limits

- Leader recruitment and assignment, federations, vassals, and sector management are not implemented.
- Rights options for non-primary species are currently marked unknown: the native command path only handles the primary species.
- Native commands support only Stellaris 4.5.2 with a matching executable hash.
- Non-aggression pacts may still be rejected by a hardcoded engine check that returns no reason text; the cause has not been pinned down, so this path is not reliable yet.
- `end_rivalry` is limited by the game rule that a rivalry cannot be ended within ten years of being declared, and has not been live-verified.

## Further documentation

The full tool list, arguments, and per-item live status are in [docs/usage-4.5.2.md](docs/usage-4.5.2.md). Connection details for an already running game are in [docs/attach-existing.md](docs/attach-existing.md).

This repository ships source, a Mod template, and tests only. It does not include the game itself, the knowledge JSON, real save files, or a machine-local MCP config. Licensed under MIT.
