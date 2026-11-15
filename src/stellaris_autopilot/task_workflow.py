"""Synchronous short plans, with durable recovery instead of payment retries."""
from __future__ import annotations

import asyncio
from pathlib import Path
import sqlite3
import threading

from .task_plan import dispatch_step, normalize_plan, request_identifier
from .task_store import TaskStore


_VERIFIED = frozenset({
    "command_verified", "pause_verified", "construction_verified", "research_verified", "order_verified",
    "command_accepted",
})


class TaskWorkflow:
    def __init__(self, connection, path: Path, definitions=None):
        self.connection = connection
        self.store = TaskStore(path)
        # Versioned definition facts for policy/edict steps; without them those steps are blocked.
        self.definitions = definitions

    def execute(self, request_id: str, steps, expected_snapshot: str | None = None, *,
                _cancel_event: threading.Event | None = None) -> dict:
        from .task_owner import current_owner

        request_identifier(request_id)
        steps, plan_hash = normalize_plan(steps, expected_snapshot)
        record, claimed = self.store.claim(request_id, plan_hash, steps, expected_snapshot, current_owner())
        if not claimed:
            return record
        if _cancel_event is not None and _cancel_event.is_set():
            self.store.cancel(request_id)

        def run(connection, config):
            guard = connection._plan_local.guard
            guard["expected_snapshot"] = expected_snapshot
            guard["steps"] = steps
            for index, step in enumerate(steps):
                guard["steps"] = steps[index:]
                if _cancel_event is not None and _cancel_event.is_set():
                    self.store.cancel(request_id)
                if not self.store.start_step(request_id, index):
                    return self.store.finish(request_id, "cancelled")
                result = (dispatch_step(connection, step) if self.definitions is None
                          else dispatch_step(connection, step, self.definitions))
                status = result.get("status") if isinstance(result, dict) else None
                if status in _VERIFIED:
                    step_status = "accepted" if status == "command_accepted" else "verified"
                    self.store.step_result(request_id, index, step_status, result)
                    if "snapshot" in result:
                        guard["expected_snapshot"] = result["snapshot"]
                elif status == "already_selected":
                    self.store.step_result(request_id, index, "already_satisfied", result)
                    guard["expected_snapshot"] = guard.get("last_snapshot")
                else:
                    known_rejections = {"blocked", "stopped", "stale", "busy"}
                    outcome = status if status in known_rejections else "unverified"
                    self.store.step_result(request_id, index, outcome, result if isinstance(result, dict) else {
                        "status": "unverified", "reason": "The executor returned no correlated result",
                    })
                    prior = self.store.get(request_id)["steps"][:index]
                    final = "unverified" if outcome == "unverified" else "partial" if any(
                        item["status"] in {"verified", "accepted", "already_satisfied"} for item in prior) else outcome
                    return self.store.finish(request_id, final)
            record = self.store.get(request_id)
            if any(step.get("status") == "accepted" for step in record["steps"]):
                if all(step.get("status") in {"accepted", "already_satisfied"} for step in record["steps"]):
                    return self.store.finish(request_id, "command_accepted")
                return self.store.finish(request_id, "command_accepted")
            return self.store.finish(request_id, "completed")

        try:
            result = self.connection.run_plan(run)
            # An attached-session lifecycle can reject a second writer before callback entry.
            if result.get("status") == "busy":
                return self.store.finish(request_id, "busy", reason="Another writer owns the connection; this plan was not queued")
            return result
        except BaseException as exc:
            record = self.store.get(request_id)
            executing = any(step["status"] == "executing" for step in record["steps"])
            prior = any(step["status"] in {"verified", "already_satisfied"} for step in record["steps"])
            result = self.store.finish(request_id, "unverified" if executing else "partial" if prior else "blocked", reason=(
                "A step was interrupted; inspect its journal before retrying" if executing else
                "The task coordinator or writer cleanup failed after verified steps; inspect before any retry" if prior else
                "The live connection was unavailable before the first step; no plan step was submitted"))
            if not isinstance(exc, (ValueError, OSError, RuntimeError)):
                raise
            return result

    async def execute_async(self, request_id: str, steps, expected_snapshot: str | None = None) -> dict:
        import anyio

        cancel_event = threading.Event()
        worker = asyncio.create_task(asyncio.to_thread(self.execute, request_id, steps, expected_snapshot,
                                                      _cancel_event=cancel_event))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            cancel_event.set()
            # Cancellation is recorded separately; never abandon a submitted command or release its lease.
            with anyio.CancelScope(shield=True):
                try:
                    self.store.cancel(request_id)
                except (OSError, sqlite3.Error):
                    pass
                finally:
                    # Even a locked/full task database must not abandon the active native submission.
                    while not worker.done():
                        try:
                            await asyncio.shield(worker)
                        except asyncio.CancelledError:
                            continue
                # Cancel may have arrived before the initial durable claim.
                try:
                    self.store.cancel(request_id)
                except (OSError, sqlite3.Error):
                    pass
                worker.result()
            raise
