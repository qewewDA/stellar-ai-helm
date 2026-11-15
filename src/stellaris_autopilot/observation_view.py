"""Compact planning views of an already-read observation. Never requests a dump."""
from __future__ import annotations

VIEW_BRIEF = "brief"
VIEW_FULL = "full"
VIEWS = frozenset({VIEW_BRIEF, VIEW_FULL})

# Keep these state fields when present. Missing keys are omitted; a present
# value with known=false is kept as-is and never rewritten as an empty list.
_BRIEF_STATE = (
    "date", "country_id", "name", "resources", "paused", "pause_known",
    "fleets", "ships", "wars", "claims", "construction", "diplomacy",
    "army_queues", "starbase_slots", "control_state",
)
_BRIEF_TOP = ("status", "snapshot", "modified_at", "date")


def observation_view(value: str | None) -> str:
    if value is None:
        return VIEW_BRIEF
    if not isinstance(value, str) or value not in VIEWS:
        raise ValueError("view must be 'brief' or 'full'")
    return value


def apply_observation_view(observed, view: str = VIEW_BRIEF):
    view = observation_view(view)
    if view == VIEW_FULL or not isinstance(observed, dict):
        return observed
    brief = {key: observed[key] for key in _BRIEF_TOP if key in observed}
    state = observed.get("state")
    if isinstance(state, dict):
        sliced = {key: state[key] for key in _BRIEF_STATE if key in state}
        control = sliced.get("control_state")
        if isinstance(control, dict):
            sliced["control_state"] = {key: control[key] for key in
                                       ("fleet_stances", "queue_orders", "starbase_queues") if key in control}
        brief["state"] = sliced
    return brief
