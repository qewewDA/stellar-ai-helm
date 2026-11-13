"""Typed fleet stance, construction queue priority and defense platform commands.

Pinned to the 4.5.2 build. These helpers observe snapshots, prepare typed
requests and verify outcomes; they never attach to a process or execute
native code. The game's normal command validators stay authoritative; these
checks only reject requests the snapshot already proves illegal. Submission
is never proof of success: verification needs exactly the intended change.

Static evidence (4.5.2):
* Fleet stance: switch_fleet_stance_command factory 0xa70c40 (vtable
  0x23b9e90, validate 0xa61f40, execute 0xa61ef0 -> setter 0xc74c60). The
  fleet writer (0xc5d710, at 0xc5e260) omits stance 0 (passive) and writes
  ``fleet_stance=aggressive`` (1) or ``fleet_stance=evasive`` (2).
* Queue priority: increase/decrease_once_buildable_priority factories
  0xacd870/0xacd910 (validate 0xacc290/0xacc700, execute 0xacc1c0/0xacc630)
  swap the item with its previous/next neighbour in the queue's item list.
  The queue writer (0x7b7b50) emits that list as ``items`` only when it is
  non-empty, in native order. The "max" variants (0xacd8c0/0xacd960) were
  located but are not exposed because they skip items non-adjacently.
* Defense platform: buildable_defense_platform factory 0x8cb340, enqueued by
  the normal add-to-queue command (0xacd7d0, validator 0xacb850) into the
  starbase ``build_queue``, as the starbase build entry 0x15dbe60 does. The
  buildable writer (0x8c5db0) emits ``ship_design_implementation`` and
  ``starbase``; payment is charged by the normal queue admission.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re

from .pdx import PDXNode
from .ship_commands import _handle, _id, _one, paid_job


KINDS = frozenset({"set_fleet_stance", "move_queue_item", "queue_defense_platform"})
STANCES = ("passive", "aggressive", "evasive")
DIRECTIONS = ("up", "down")
# Vanilla 4.5.2 defense platform hull sizes (common/ship_sizes).
PLATFORM_SIZES = frozenset({"military_station_small", "military_station_medium", "military_station_large"})
_UNSET = 4294967295


def observe_fleet_stances(root: PDXNode, fleet_ids) -> dict:
    """Stance of every listed fleet; the writer omits the passive default."""
    result = {"known": False, "by_id": {}}
    collection = _one(root, "fleet")
    if not isinstance(collection, PDXNode):
        return result
    stances = {}
    for fleet_id in fleet_ids:
        nodes = collection.get_all(fleet_id)
        if len(nodes) != 1 or not isinstance(nodes[0], PDXNode):
            return result
        values = nodes[0].get_all("fleet_stance")
        if not values:
            stances[fleet_id] = "passive"
        elif len(values) == 1 and values[0] in ("aggressive", "evasive"):
            stances[fleet_id] = values[0]
        else:
            return result
    return {"known": True, "by_id": stances}


def observe_queue_orders(root: PDXNode, country_id: str) -> dict:
    """Native item order of every queue owned by ``country_id``."""
    result = {"known": False, "by_id": {}}
    construction = _one(root, "construction")
    queues = _one(_one(construction, "queue_mgr"), "queues")
    if not isinstance(queues, PDXNode):
        return result
    orders = {}
    for queue_id, queue in queues.entries:
        if not isinstance(queue, PDXNode):
            continue
        if _handle(_one(queue, "owner")) != country_id:
            continue
        if _handle(queue_id) is None or len(queues.get_all(queue_id)) != 1:
            return result
        lists = queue.get_all("items")
        if not lists:
            orders[queue_id] = []
            continue
        if len(lists) != 1 or not isinstance(lists[0], PDXNode) or not lists[0].entries:
            return result
        ids = [_handle(value) if key is None else None for key, value in lists[0].entries]
        if None in ids or len(ids) != len(set(ids)):
            return result
        orders[queue_id] = ids
    return {"known": True, "by_id": orders}


def observe_starbase_queues(root: PDXNode, country_id: str, fleets: list[dict]) -> dict:
    """Owned starbases with their general (non-shipyard) build queue."""
    result = {"known": False, "by_id": {}}
    stations = _one(_one(root, "starbase_mgr"), "starbases")
    ships = _one(root, "ships")
    queues = _one(_one(_one(root, "construction"), "queue_mgr"), "queues")
    if not all(isinstance(value, PDXNode) for value in (stations, ships, queues)):
        return result
    own_fleets = {fleet.get("id") for fleet in fleets}
    found = {}
    for starbase_id, starbase in stations.entries:
        if not isinstance(starbase, PDXNode):
            continue
        station_id = _handle(_one(starbase, "station"))
        station = _one(ships, station_id) if station_id is not None else None
        if not isinstance(station, PDXNode):
            return result
        fleet_id = _handle(_one(station, "fleet"))
        if fleet_id not in own_fleets:
            continue
        queue_id = _handle(_one(starbase, "build_queue"))
        queue = _one(queues, queue_id) if queue_id is not None else None
        location = _one(queue, "location")
        if (_handle(starbase_id) is None or len(stations.get_all(starbase_id)) != 1 or queue is None
                or _handle(_one(queue, "owner")) != country_id or _one(queue, "type") != "starbase"
                or _one(location, "type") != 0 or _handle(_one(location, "id")) != station_id):
            return result
        found[starbase_id] = {"id": starbase_id, "station_id": station_id, "fleet_id": fleet_id,
                              "queue_id": queue_id, "owner": country_id, "level": _one(starbase, "level")}
    return {"known": True, "by_id": found}


def observe_control_state(root: PDXNode, country_id: str, fleets: list[dict], fleets_known: bool) -> dict:
    unknown = {"known": False, "by_id": {}}
    return {
        "fleet_stances": observe_fleet_stances(root, [fleet["id"] for fleet in fleets]) if fleets_known else dict(unknown),
        "queue_orders": observe_queue_orders(root, country_id),
        "starbase_queues": observe_starbase_queues(root, country_id, fleets) if fleets_known else dict(unknown),
    }


@dataclass(frozen=True)
class ControlRequest:
    kind: str
    fleet_id: str | None = None
    stance: str | None = None
    item_id: str | None = None
    direction: str | None = None
    starbase_id: str | None = None
    design_id: str | None = None

    def to_dict(self) -> dict:
        if self.kind == "set_fleet_stance":
            return {"fleet_id": self.fleet_id, "stance": self.stance}
        if self.kind == "move_queue_item":
            return {"item_id": self.item_id, "direction": self.direction}
        return {"starbase_id": self.starbase_id, "design_id": self.design_id}


def control_request(kind: str, **payload) -> ControlRequest:
    if kind == "set_fleet_stance" and set(payload) == {"fleet_id", "stance"}:
        if payload["stance"] not in STANCES:
            raise ValueError("Stance must be passive, aggressive or evasive")
        return ControlRequest(kind, fleet_id=_id(payload["fleet_id"]), stance=payload["stance"])
    if kind == "move_queue_item" and set(payload) == {"item_id", "direction"}:
        if payload["direction"] not in DIRECTIONS:
            raise ValueError("Direction must be up or down")
        return ControlRequest(kind, item_id=_id(payload["item_id"]), direction=payload["direction"])
    if kind == "queue_defense_platform" and set(payload) == {"starbase_id", "design_id"}:
        return ControlRequest(kind, starbase_id=_id(payload["starbase_id"]), design_id=_id(payload["design_id"]))
    raise ValueError("Unsupported control command or unexpected fields")


@dataclass(frozen=True)
class ResolvedControlCommand:
    request: ControlRequest
    current_stance: str | None = None
    queue_id: str | None = None
    neighbor_id: str | None = None
    station_id: str | None = None

    @property
    def kind(self) -> str:
        return self.request.kind

    def to_dict(self) -> dict:
        values = self.request.to_dict()
        if self.kind == "set_fleet_stance":
            values["current_stance"] = self.current_stance
        elif self.kind == "move_queue_item":
            values.update(queue_id=self.queue_id, neighbor_id=self.neighbor_id)
        else:
            values.update(station_id=self.station_id, queue_id=self.queue_id)
        return values


def _request_again(request: ControlRequest) -> ControlRequest:
    if type(request) is not ControlRequest:
        raise ValueError("A typed control request is required")
    return control_request(request.kind, **request.to_dict())


def _controls(state: dict, key: str) -> dict:
    if state.get("country_id") != "0":
        raise ValueError("Native control commands have only been verified for country 0")
    controls = state.get("control_state")
    value = controls.get(key) if isinstance(controls, dict) else None
    if not isinstance(value, dict) or value.get("known") is not True or not isinstance(value.get("by_id"), dict):
        raise ValueError("The required control observation is not exactly known in this snapshot")
    return value["by_id"]


def _queue_members(state: dict, queue_id: str) -> list[str]:
    """The queue's native order, cross-checked against the item manager."""
    construction = state.get("construction")
    if not isinstance(construction, dict) or construction.get("known") is not True:
        raise ValueError("Construction queues and items are not exactly observable")
    queue = construction.get("queues_by_id", {}).get(queue_id)
    if (not isinstance(queue, dict) or queue.get("known") is not True or queue.get("items_known") is not True
            or queue.get("owner") != "0"):
        raise ValueError("The owned construction queue is unobservable")
    order = _controls(state, "queue_orders").get(queue_id)
    members = {key for key, value in construction.get("items_by_id", {}).items() if value.get("queue_id") == queue_id}
    if (not isinstance(order, list) or len(order) != len(set(order)) or set(order) != members
            or queue.get("item_count") != len(order)):
        raise ValueError("The native queue order conflicts with observed queue items")
    return list(order)


def resolve_control_command(state: dict, request: ControlRequest) -> ResolvedControlCommand:
    request = _request_again(request)
    if request.kind == "set_fleet_stance":
        stances = _controls(state, "fleet_stances")
        fleet = state.get("fleets_by_id", {}).get(request.fleet_id)
        if not isinstance(fleet, dict) or fleet.get("mobile") is not True:
            raise ValueError("Select an observed mobile fleet owned by this empire")
        current = stances.get(request.fleet_id)
        if current not in STANCES:
            raise ValueError("The fleet's current stance is unobservable")
        if current == request.stance:
            raise ValueError("The fleet already has this stance")
        return ResolvedControlCommand(request, current_stance=current)
    if request.kind == "move_queue_item":
        item = state.get("construction", {}).get("items_by_id", {}).get(request.item_id)
        if not isinstance(item, dict) or item.get("paying_country") != "0":
            raise ValueError("Select an observed construction item paid by this empire")
        queue_id = _id(item.get("queue_id"))
        order = _queue_members(state, queue_id)
        position = order.index(request.item_id)
        neighbour = position - 1 if request.direction == "up" else position + 1
        if not 0 <= neighbour < len(order):
            raise ValueError("The item is already at that end of its queue")
        return ResolvedControlCommand(request, queue_id=queue_id, neighbor_id=order[neighbour])
    status = state.get("shipbuilding", {})
    design = status.get("designs_by_id", {}).get(request.design_id) if status.get("designs_known") is True else None
    if not isinstance(design, dict) or design.get("id") != request.design_id:
        raise ValueError("Select a current country ship design")
    if design.get("ship_size") not in PLATFORM_SIZES:
        raise ValueError("The design is not a defense platform design")
    starbase = _controls(state, "starbase_queues").get(request.starbase_id)
    if not isinstance(starbase, dict) or starbase.get("owner") != "0" or starbase.get("id") != request.starbase_id:
        raise ValueError("Select an observed owned starbase with a build queue")
    queue_id = _id(starbase.get("queue_id"))
    _queue_members(state, queue_id)
    queue = state["construction"]["queues_by_id"][queue_id]
    if queue.get("type") != "starbase" or queue.get("location") != {"type": 0, "id": starbase.get("station_id")}:
        raise ValueError("The starbase build queue is not exactly observable")
    return ResolvedControlCommand(request, station_id=_id(starbase.get("station_id")), queue_id=queue_id)


def native_control_payload(command: ResolvedControlCommand, token: str) -> dict:
    if type(command) is not ResolvedControlCommand:
        raise ValueError("A resolved control command is required")
    request = _request_again(command.request)
    if request.kind == "set_fleet_stance":
        if command.current_stance not in STANCES or command.current_stance == request.stance:
            raise ValueError("The current stance must be known and differ")
        if (command.queue_id, command.neighbor_id, command.station_id) != (None, None, None):
            raise ValueError("Unexpected resolved fields")
    elif request.kind == "move_queue_item":
        _id(command.queue_id)
        _id(command.neighbor_id)
        if command.neighbor_id == request.item_id or (command.current_stance, command.station_id) != (None, None):
            raise ValueError("Unexpected resolved fields")
    else:
        _id(command.queue_id)
        _id(command.station_id)
        if (command.current_stance, command.neighbor_id) != (None, None):
            raise ValueError("Unexpected resolved fields")
    return {"kind": request.kind, "country_id": "0", "token": token, **command.to_dict()}


def _stockpiles(state: dict) -> dict | None:
    resources = state.get("resources")
    if (not isinstance(resources, dict) or not resources
            or not all(type(value) in (int, float) and math.isfinite(value) for value in resources.values())):
        return None
    return resources


_DATE = re.compile(r"[0-9]{4}\.[0-9]{2}\.[0-9]{2}\Z", re.ASCII)


def _unchanged(before: dict, after: dict, *keys: str) -> bool:
    return all(before.get(key) == after.get(key) for key in keys)


def verify_control_command(before: dict, after: dict, command: ResolvedControlCommand) -> bool:
    """Require exactly one intended change on the same (paused) game day."""
    if type(command) is not ResolvedControlCommand:
        return False
    try:
        request = _request_again(command.request)
        if (not isinstance(before.get("date"), str) or _DATE.fullmatch(before["date"]) is None
                or after.get("date") != before["date"] or after.get("country_id") != "0"
                or not _unchanged(before, after, "country_id", "empire_build", "country_controls", "planets_by_id")):
            return False
        stances_before, stances_after = (_controls(state, "fleet_stances") for state in (before, after))
        orders_before, orders_after = (_controls(state, "queue_orders") for state in (before, after))
        if request.kind == "set_fleet_stance":
            expected = {**stances_before, request.fleet_id: request.stance}
            return (stances_before.get(request.fleet_id) == command.current_stance
                    and stances_after == expected and orders_after == orders_before
                    and _unchanged(before, after, "fleets_by_id", "construction", "shipbuilding")
                    and _stockpiles(before) is not None and _stockpiles(after) == _stockpiles(before))
        if stances_after != stances_before:
            return False
        if request.kind == "move_queue_item":
            order = _queue_members(before, command.queue_id)
            position = order.index(request.item_id)
            other = position - 1 if request.direction == "up" else position + 1
            if not 0 <= other < len(order) or order[other] != command.neighbor_id:
                return False
            order[position], order[other] = order[other], order[position]
            return (_queue_members(after, command.queue_id) == order
                    and orders_after == {**orders_before, command.queue_id: order}
                    and _unchanged(before, after, "fleets_by_id", "construction", "shipbuilding")
                    and _stockpiles(before) is not None and _stockpiles(after) == _stockpiles(before))
        if resolve_control_command(before, request) != command:
            return False
        if not _unchanged(before, after, "fleets_by_id", "shipbuilding") or (
                _controls(after, "starbase_queues") != _controls(before, "starbase_queues")):
            return False
        item = paid_job(before, after, command.queue_id)
        if (item is None or item.get("buildable_type") != "buildable_defense_platform"
                or item.get("buildable") != {
                    "ship_design_implementation": {"design": int(request.design_id), "upgrade": _UNSET,
                                                   "growth_stage": 0},
                    "starbase": int(request.starbase_id)}):
            return False
        first, second = _stockpiles(before), _stockpiles(after)
        charged = {key for key, value in item["resources"].items() if value}
        if (first is None or second is None or set(first) != set(second)
                or any(second[key] != value for key, value in first.items() if key not in charged)):
            return False
        expected_orders = {**orders_before, command.queue_id: [*orders_before.get(command.queue_id, []), item["id"]]}
        return orders_after == expected_orders and _queue_members(after, command.queue_id) == expected_orders[command.queue_id]
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return False
