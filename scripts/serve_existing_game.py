"""MCP for a manually opened, paused Stellaris save. Never launches a game."""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

from mcp.types import ToolAnnotations
from pydantic import StrictBool

# Serve this checkout's package, not whichever checkout the venv has installed.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stellaris_autopilot.direct_mcp import build_direct_server
from stellaris_autopilot.event_commands import EventCatalog
from stellaris_autopilot.existing_session import ExistingGameSession


def _event_catalog(game_dir, user_dir):
    """One lazy catalog; game files are read only when events are listed."""
    catalog = []

    def provide():
        if not catalog:
            catalog.append(EventCatalog.for_user_dir(game_dir, user_dir))
        return catalog[0]
    return provide


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-dir", type=Path, default=Path(r"D:\steam\steamapps\common\Stellaris"))
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--user-dir", type=Path)
    parser.add_argument("--pid", type=int, help="Optional local PID selection when multiple games exist")
    parser.add_argument("--extended", action="store_true")
    parser.add_argument("--allow-test-event", action="store_true",
                        help="Test saves only: authorize execute_action('fire_test_event') for one fixed vanilla event")
    parser.add_argument("--knowledge-bundle", type=Path)
    parser.add_argument("--connect-timeout", type=float, default=120,
                        help="Initialization budget in seconds; file work and cleanup can take extra time")
    args = parser.parse_args()
    session = ExistingGameSession(args.game_dir, args.state_dir, user_dir=args.user_dir,
                                  pid=args.pid, extended=args.extended, knowledge_bundle=args.knowledge_bundle,
                                  timeout_seconds=args.connect_timeout, test_events=args.allow_test_event)
    try:
        server = build_direct_server(args.state_dir / "not-connected.json", connection=session,
                                     context_provider=session.context,
                                     task_store_path=args.state_dir / "tasks.sqlite",
                                     knowledge_provider=session.knowledge if args.knowledge_bundle is not None else None,
                                     event_catalog_provider=_event_catalog(args.game_dir, args.user_dir),
                                     existing_game=True)
        readonly = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
        writing = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False)

        @server.tool(annotations=readonly)
        def attached_status() -> dict:
            """Read connection progress; an idle connection also checks its held process handles.

            No process discovery, attachment or save parsing. A stale connection requires an explicit retry.
            """
            return session.status()

        @server.tool(annotations=writing)
        async def connect_game(retry: StrictBool = False) -> dict:
            """Connect once to the manually opened paused save. Report failures; never retry automatically.

            MCP discovery is already ready before this call. Success includes a verified compact initial
            observation. Use retry=true only after the user corrects a previously reported failure.
            """
            return await session.connect_game_async(retry=retry)

        @server.tool(annotations=writing)
        async def session_brief() -> dict:
            """Return a brief observation, connecting once if needed (retry=false, no automatic retry).

            If already connected and no accepted writes are waiting for deferred
            verification, reads the latest snapshot only (no extra dump). If accepted
            writes are pending, requests one fresh dump and settles all of them.
            If not connected, calls connect_game(retry=false): failures are returned
            as-is; an unpaused game is not dumped. The payload uses observe_empire
            view=brief and includes deferred_verification when applicable.
            """
            from stellaris_autopilot.event_commands import describe_pending_events
            from stellaris_autopilot.observation_view import apply_observation_view
            status = session.status()
            if not (isinstance(status, dict) and (status.get("connected") is True
                                                  or status.get("status") == "connected"
                                                  or status.get("state") == "connected")):
                result = await session.connect_game_async(retry=False)
                if not (isinstance(result, dict) and (result.get("connected") is True
                                                      or result.get("status") == "connected"
                                                      or result.get("state") == "connected")):
                    return result
            pending = False
            try:
                pending = bool(session.has_accepted_operations())
            except (AttributeError, ValueError, OSError):
                pending = False
            observed = await asyncio.to_thread(
                session.request_observation if pending else session.observe)
            if isinstance(observed, dict) and isinstance(observed.get("state"), dict):
                try:
                    catalog = _event_catalog(args.game_dir, args.user_dir)()
                    described = describe_pending_events(observed["state"], catalog)
                    observed = {**observed, "state": {**observed["state"], "pending_events_described": described}}
                except (OSError, ValueError):
                    pass
            return apply_observation_view(observed, "brief")

        @server.tool(annotations=writing)
        async def checkpoint_game() -> dict:
            """Pause, verify and write a new save under save games/qs_mcp; keep the game running."""
            return await asyncio.to_thread(session.checkpoint_game)

        @server.tool(annotations=writing)
        async def disconnect_game(save: StrictBool = True) -> dict:
            """Optionally checkpoint, then detach MCP. The manually opened game remains running."""
            return await asyncio.to_thread(session.disconnect_game, save=save)

        server.run(transport="stdio")
    finally:
        session.close()


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError) as exc:
        print("Existing-game connection failed: " + str(exc), file=sys.stderr)
        raise SystemExit(1) from None
