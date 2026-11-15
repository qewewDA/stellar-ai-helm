"""Starbase modules and buildings through the normal build queue (pinned 4.5.2).

These helpers read snapshots, prepare typed requests and verify outcomes;
they never attach to a process.

Static evidence (4.5.2), see native_starbase.js: ``buildable_starbase_module``
and ``buildable_starbase_building`` carry a type, the starbase and a slot and
are posted with ``add_buildable_to_queue_command`` to the starbase's general
build queue, exactly like defense platforms. A queued item is saved as
``buildable_starbase_module = { starbase_module="<key>" slot=<n> starbase=<id> }``
(``starbase_building`` likewise). The game's validator decides slot limits,
prerequisites and affordability.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from .control_commands import _controls, _queue_members, _stockpiles, _unchanged
from .pdx import PDXNode
from .ship_commands import _id, _one, paid_job

STARBASE_KINDS = {"queue_starbase_module": ("module_id", "modules", "buildable_starbase_module", "starbase_module"),
                  "queue_starbase_building": ("building_id", "buildings", "buildable_starbase_building",
                                              "starbase_building")}
_KEY = re.compile(r"[a-z][a-z0-9_]{0,120}\Z", re.ASCII)
_SLOT = re.compile(r"0|[1-9][0-9]?\Z", re.ASCII)


def _slots(node) -> dict | None:
    if node is None:
        return {}
    if not isinstance(node, PDXNode):
        return None
    found = {}
    for key, value in node.entries:
        if key is None or not _SLOT.fullmatch(key) or key in found or not isinstance(value, str) or not _KEY.fullmatch(value):
            return None
        found[key] = value
    return found


def observe_starbase_slots(root: PDXNode, starbase_ids) -> dict:
    """Built modules and buildings by slot for the given (owned) starbases."""
    result = {"known": False, "by_id": {}}
    stations = _one(_one(root, "starbase_mgr"), "starbases")
    if not isinstance(stations, PDXNode):
        return result
    found = {}
    for starbase_id in starbase_ids:
        nodes = stations.get_all(str(starbase_id))
        if len(nodes) != 1 or not isinstance(nodes[0], PDXNode):
            return result
        entry = {}
        for name in ("modules", "buildings"):
            blocks = nodes[0].get_all(name)
            if len(blocks) > 1:
                return result
            slots = _slots(blocks[0] if blocks else None)
            if slots is None:
                return result
            entry[name] = slots
        found[str(starbase_id)] = entry
    result.update(known=True, by_id=found)
    return result


@dataclass(frozen=True)
class StarbaseRequest:
    kind: str
    starbase_id: str
    slot: str
    type_id: str

    def to_dict(self) -> dict:
        return {"starbase_id": self.starbase_id, "slot": self.slot, STARBASE_KINDS[self.kind][0]: self.type_id}


def starbase_request(kind: str, definition=None, *, starbase_id, slot, type_id) -> StarbaseRequest:
    if kind not in STARBASE_KINDS:
        raise ValueError("Unsupported starbase command")
    if definition is not True:
        raise ValueError("The module or building must be found in versioned game knowledge")
    if not isinstance(type_id, str) or not _KEY.fullmatch(type_id):
        raise ValueError("Invalid starbase module or building id")
    slot = str(slot) if type(slot) is int else slot
    if not isinstance(slot, str) or not _SLOT.fullmatch(slot):
        raise ValueError("Invalid starbase slot")
    return StarbaseRequest(kind, _id(starbase_id), slot, type_id)


@dataclass(frozen=True)
class ResolvedStarbaseCommand:
    request: StarbaseRequest
    station_id: str
    queue_id: str

    @property
    def kind(self) -> str:
        return self.request.kind

    def to_dict(self) -> dict:
        return {**self.request.to_dict(), "station_id": self.station_id, "queue_id": self.queue_id}


def _again(request: StarbaseRequest) -> StarbaseRequest:
    if type(request) is not StarbaseRequest:
        raise ValueError("A typed starbase request is required")
    return starbase_request(request.kind, True, starbase_id=request.starbase_id, slot=request.slot,
                            type_id=request.type_id)


def _starbase_slots(state: dict) -> dict:
    observed = state.get("starbase_slots")
    if not isinstance(observed, dict) or observed.get("known") is not True:
        raise ValueError("Starbase modules and buildings are not exactly observable in this snapshot")
    return observed["by_id"]


def resolve_starbase_command(state: dict, request: StarbaseRequest) -> ResolvedStarbaseCommand:
    request = _again(request)
    starbase = _controls(state, "starbase_queues").get(request.starbase_id)
    if not isinstance(starbase, dict) or starbase.get("owner") != "0" or starbase.get("id") != request.starbase_id:
        raise ValueError("Select an observed owned starbase with a build queue")
    queue_id = _id(starbase.get("queue_id"))
    _queue_members(state, queue_id)
    queue = state["construction"]["queues_by_id"][queue_id]
    if queue.get("type") != "starbase" or queue.get("location") != {"type": 0, "id": starbase.get("station_id")}:
        raise ValueError("The starbase build queue is not exactly observable")
    _, group, buildable_type, field = STARBASE_KINDS[request.kind]
    slots = _starbase_slots(state).get(request.starbase_id)
    if not isinstance(slots, dict):
        raise ValueError("That starbase's slots are not exactly observable")
    if request.slot in slots[group]:
        raise ValueError("That slot is already built")
    for item in state["construction"]["items_by_id"].values():
        buildable = item.get("buildable") if isinstance(item, dict) else None
        if (item.get("queue_id") == queue_id and item.get("buildable_type") == buildable_type
                and isinstance(buildable, dict) and str(buildable.get("slot")) == request.slot):
            raise ValueError("That slot already has a queued item")
    return ResolvedStarbaseCommand(request, station_id=_id(starbase.get("station_id")), queue_id=queue_id)


def native_starbase_payload(command: ResolvedStarbaseCommand, token: str) -> dict:
    if type(command) is not ResolvedStarbaseCommand:
        raise ValueError("A resolved starbase command is required")
    _again(command.request)
    _id(command.station_id)
    _id(command.queue_id)
    return {"kind": command.kind, "country_id": "0", "token": token, **command.to_dict()}


def verify_starbase_command(before: dict, after: dict, command: ResolvedStarbaseCommand) -> bool:
    """One new paid item of exactly this type, slot and starbase, appended to the queue."""
    if type(command) is not ResolvedStarbaseCommand:
        return False
    try:
        request = _again(command.request)
        if resolve_starbase_command(before, request) != command:
            return False
        if after.get("date") != before.get("date") or not _unchanged(
                before, after, "country_id", "empire_build", "country_controls", "planets_by_id",
                "fleets_by_id", "shipbuilding", "starbase_slots"):
            return False
        if _controls(after, "starbase_queues") != _controls(before, "starbase_queues"):
            return False
        _, _, buildable_type, field = STARBASE_KINDS[request.kind]
        item = paid_job(before, after, command.queue_id)
        if (item is None or item.get("buildable_type") != buildable_type
                or item.get("buildable") != {field: request.type_id, "slot": int(request.slot),
                                             "starbase": int(request.starbase_id)}):
            return False
        first, second = _stockpiles(before), _stockpiles(after)
        charged = {key for key, value in item["resources"].items() if value}
        if (first is None or second is None or set(first) != set(second)
                or any(second[key] != value for key, value in first.items() if key not in charged)):
            return False
        orders_before, orders_after = (_controls(state, "queue_orders") for state in (before, after))
        expected = {**orders_before, command.queue_id: [*orders_before.get(command.queue_id, []), item["id"]]}
        return orders_after == expected and _queue_members(after, command.queue_id) == expected[command.queue_id]
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return False


__all__ = ["STARBASE_KINDS", "StarbaseRequest", "ResolvedStarbaseCommand", "observe_starbase_slots",
           "starbase_request", "resolve_starbase_command", "native_starbase_payload", "verify_starbase_command",
]
