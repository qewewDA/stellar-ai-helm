"""Typed normal planet orders; physical planets and native colonies stay distinct.

These helpers prepare requests and inspect snapshots. They never attach to a
process or execute native code. A returned/submitted native order is not proof
of success; the conservative verifiers require an exact new state change.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, replace
import math
import re

from .native import building_request, resolve_building


_ID = re.compile(r"0|[1-9][0-9]{0,9}\Z", re.ASCII)
_TOKEN = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
KINDS = frozenset({"upgrade_building", "demolish_building", "convert_zone", "cancel_construction"})


def _id(value) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value) or int(value) >= 4294967295:
        raise ValueError("Expected a canonical observed object handle")
    return value


def _key(value, prefix: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(prefix + r"_[A-Za-z0-9_]{1,120}", value, re.ASCII):
        raise ValueError("Expected a bounded data identifier")
    return value


@dataclass(frozen=True)
class PlanetRequest:
    kind: str
    planet_id: str
    zone_id: str | None = None
    building_id: str | None = None
    target_building: str | None = None
    target_zone: str | None = None
    item_id: str | None = None
    district_id: str | None = None
    slot_index: int | None = None

    def to_dict(self) -> dict:
        return {key: value for key, value in asdict(self).items() if value is not None}


_FIELDS = {
    "upgrade_building": {"planet_id", "zone_id", "building_id", "target_building"},
    "demolish_building": {"planet_id", "zone_id", "building_id"},
    "convert_zone": {"planet_id", "target_zone"},  # plus district_id+slot_index and/or occupied zone_id
    "cancel_construction": {"planet_id", "item_id"},
}


def _slot_index(value) -> int:
    if type(value) is int and not isinstance(value, bool) and 0 <= value < 32:
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit() and 0 <= int(value) < 32:
        return int(value)
    raise ValueError("Expected a district zone slot index")


def planet_request(kind: str, **payload) -> PlanetRequest:
    if kind == "convert_zone":
        return _convert_zone_request(payload)
    if kind not in KINDS or set(payload) != _FIELDS[kind]:
        raise ValueError("Unsupported planet order or unexpected fields")
    values = {key: (_key(value, "building") if key == "target_building" else
                    _key(value, "zone") if key == "target_zone" else _id(value))
              for key, value in payload.items()}
    return PlanetRequest(kind, **values)


def _convert_zone_request(payload: dict) -> PlanetRequest:
    """Build or replace a specialization on an explicit district slot.

    Preferred fields: planet_id, district_id, slot_index, target_zone.
    An occupied zone handle (zone_id) is also accepted, alone or together
    with the district slot when they refer to the same observed slot.
    """
    allowed = {"planet_id", "district_id", "slot_index", "target_zone", "zone_id"}
    if set(payload) - allowed or "planet_id" not in payload or "target_zone" not in payload:
        raise ValueError("Unsupported planet order or unexpected fields")
    planet_id = _id(payload["planet_id"])
    target_zone = _key(payload["target_zone"], "zone")
    zone_id = _id(payload["zone_id"]) if payload.get("zone_id") is not None else None
    has_slot = payload.get("district_id") is not None or payload.get("slot_index") is not None
    if has_slot:
        if payload.get("district_id") is None or payload.get("slot_index") is None:
            raise ValueError("convert_zone requires both district_id and slot_index")
        district_id = _id(str(payload["district_id"]) if type(payload["district_id"]) is int else payload["district_id"])
        return PlanetRequest("convert_zone", planet_id=planet_id, district_id=district_id,
                             slot_index=_slot_index(payload["slot_index"]), target_zone=target_zone,
                             zone_id=zone_id)
    if zone_id is None:
        raise ValueError("convert_zone requires district_id and slot_index, or an occupied zone_id")
    return PlanetRequest("convert_zone", planet_id=planet_id, zone_id=zone_id, target_zone=target_zone)


@dataclass(frozen=True)
class ResolvedPlanetCommand:
    request: PlanetRequest
    colony_id: str
    queue_id: str
    district_id: str | None = None
    slot_index: int | None = None
    source_zone: str | None = None
    source_building: str | None = None

    @property
    def kind(self) -> str:
        return self.request.kind

    def to_dict(self) -> dict:
        values = {key: value for key, value in asdict(self).items()
                  if key != "request" and value is not None}
        return {**self.request.to_dict(), **values}


def _request_again(request: PlanetRequest) -> PlanetRequest:
    if type(request) is not PlanetRequest:
        raise ValueError("A typed planet request is required")
    payload = request.to_dict()
    return planet_request(payload.pop("kind"), **payload)


def _state_id(value) -> str:
    return _id(str(value) if type(value) is int else value)


def _queue_items(state: dict, queue_id: str) -> dict:
    """Cross-check normalized queue membership rather than trusting one index."""
    construction = state["construction"]
    queue = construction["queues_by_id"][queue_id]
    indexed = construction.get("items_by_id")
    listed = queue.get("items")
    if not isinstance(indexed, dict) or not isinstance(listed, list):
        raise ValueError("The exact queue item list is unobservable")
    expected = {}
    for item_id, item in indexed.items():
        if not isinstance(item, dict) or _id(item_id) != item.get("id"):
            raise ValueError("Conflicting construction item identity")
        if item.get("queue_id") == queue_id:
            expected[item_id] = item
    observed = {}
    for item in listed:
        if (not isinstance(item, dict) or _id(item.get("id")) in observed
                or item.get("queue_id") != queue_id):
            raise ValueError("Conflicting queue item membership")
        observed[item["id"]] = item
    if (observed != expected or type(queue.get("item_count")) is not int
            or queue["item_count"] != len(observed)):
        raise ValueError("Conflicting queue item membership")
    return observed


def _owned_planet(state: dict, planet_id: str) -> tuple[dict, str, str]:
    if state.get("country_id") != "0":
        raise ValueError("Planet native orders are restricted to country 0")
    planet = state.get("planets_by_id", {}).get(planet_id)
    if (not isinstance(planet, dict) or planet.get("id") != planet_id
            or _state_id(planet.get("owner")) != "0" or _state_id(planet.get("controller")) != "0"):
        raise ValueError("Expected an owned, controlled physical planet")
    colony = _state_id(planet.get("colony"))
    queue = _state_id(planet.get("build_queue"))
    construction = state.get("construction", {})
    observed = construction.get("queues_by_id", {}).get(queue)
    if (construction.get("known") is not True or not isinstance(observed, dict)
            or observed.get("known") is not True or observed.get("items_known") is not True
            or observed.get("id") != queue or observed.get("owner") != "0" or observed.get("type") != "planet"
            or observed.get("location") != {"type": 2, "id": planet_id}):
        raise ValueError("The exact physical-planet construction queue is unobservable")
    _queue_items(state, queue)
    return planet, colony, queue


def resolve_planet_command(state: dict, request: PlanetRequest) -> ResolvedPlanetCommand:
    request = _request_again(request)
    planet, colony_id, queue_id = _owned_planet(state, request.planet_id)
    if request.kind == "cancel_construction":
        item = state["construction"].get("items_by_id", {}).get(request.item_id)
        if (not isinstance(item, dict) or item.get("queue_id") != queue_id or item.get("paying_country") != "0"
                or item.get("target") != {"planet_id": request.planet_id, "colony_id": colony_id}
                or not _progress(item) or not isinstance(item.get("resources"), dict)):
            raise ValueError("The exact pending paid construction item is unobservable")
        return ResolvedPlanetCommand(request, colony_id, queue_id)
    if request.kind == "convert_zone":
        return _resolve_convert_zone(state, request, planet, colony_id, queue_id)
    zone = planet.get("zones_by_id", {}).get(request.zone_id)
    if (planet.get("zones_known") is not True or not isinstance(zone, dict)
            or zone.get("id") != request.zone_id or zone.get("buildings_known") is not True
            or not isinstance(zone.get("buildings_by_id"), dict)):
        raise ValueError("The complete exact zone and building references are required")
    district_id, source_zone = _id(zone.get("district_id")), _key(zone.get("type"), "zone")
    slot = zone.get("slot_index")
    if type(slot) is not int or not 0 <= slot < 32:
        raise ValueError("The zone's observed district slot index is required")
    # This also checks the normal shared district/queue gates and namespace join.
    resolved = resolve_building(state, building_request(request.planet_id, request.zone_id,
                                                       "building_reference_guard"))
    if resolved.colony_id != colony_id or resolved.queue_id != queue_id:
        raise ValueError("Conflicting colony or queue identity")
    source_building = None
    if request.building_id is not None:
        building = zone["buildings_by_id"].get(request.building_id)
        if not isinstance(building, dict) or building.get("id") != request.building_id:
            raise ValueError("The requested building is not in the exact observed zone")
        source_building = _key(building.get("type"), "building")
        if request.kind == "upgrade_building" and source_building == request.target_building:
            raise ValueError("An upgrade must select a different target type")
    return ResolvedPlanetCommand(request, colony_id, queue_id, district_id, slot, source_zone, source_building)


def observed_zone_slots(planet: dict) -> list[dict]:
    """Flatten observed district specialization slots, including empty ones."""
    slots = []
    raw = planet.get("zone_slots") if isinstance(planet, dict) else None
    if isinstance(raw, list):
        for district in raw:
            if not isinstance(district, dict):
                continue
            district_id = district.get("district_id")
            district_type = district.get("district_type")
            for slot in district.get("slots") or []:
                if not isinstance(slot, dict) or type(slot.get("slot_index")) is not int:
                    continue
                item = dict(slot)
                item.setdefault("district_id", district_id)
                item.setdefault("district_type", district_type)
                slots.append(item)
        return slots
    for zone in (planet.get("zones_by_id") or {}).values() if isinstance(planet.get("zones_by_id"), dict) else []:
        if not isinstance(zone, dict) or type(zone.get("slot_index")) is not int:
            continue
        slots.append({
            "slot_index": zone["slot_index"], "occupied": True,
            "zone_id": zone.get("id") or zone.get("zone_id"), "type": zone.get("type"),
            "district_id": zone.get("district_id"), "district_type": zone.get("district_type"),
        })
    return slots


def _resolve_convert_zone(state, request, planet, colony_id, queue_id) -> ResolvedPlanetCommand:
    if planet.get("zones_known") is not True:
        raise ValueError("The exact district zone slots are unobservable")
    slots = observed_zone_slots(planet)
    chosen = None
    if request.district_id is not None and request.slot_index is not None:
        matches = [slot for slot in slots
                   if slot.get("district_id") == request.district_id and slot.get("slot_index") == request.slot_index]
        if len(matches) != 1:
            raise ValueError("The exact district slot is unobservable")
        chosen = matches[0]
        if request.zone_id is not None and chosen.get("zone_id") != request.zone_id:
            raise ValueError("Zone handle does not match the district slot")
    elif request.zone_id is not None:
        matches = [slot for slot in slots if slot.get("zone_id") == request.zone_id]
        if len(matches) != 1:
            raise ValueError("The exact occupied zone is unobservable")
        chosen = matches[0]
    else:
        raise ValueError("convert_zone requires district_id and slot_index, or an occupied zone_id")
    occupied = chosen.get("occupied") is not False and chosen.get("zone_id") not in (None, "4294967295")
    zone_id = _id(chosen["zone_id"]) if occupied else None
    source_zone = _key(chosen.get("type"), "zone") if occupied else None
    if occupied and source_zone == request.target_zone:
        raise ValueError("Zone conversion must select a different zone type")
    district_id = _id(chosen.get("district_id"))
    slot = chosen.get("slot_index")
    if type(slot) is not int or not 0 <= slot < 32:
        raise ValueError("The zone's observed district slot index is required")
    filled = replace(request, zone_id=zone_id, district_id=district_id, slot_index=slot)
    return ResolvedPlanetCommand(filled, colony_id, queue_id, district_id, slot, source_zone, None)


def native_planet_payload(command: ResolvedPlanetCommand, token: str) -> dict:
    if type(command) is not ResolvedPlanetCommand or not isinstance(token, str) or not _TOKEN.fullmatch(token):
        raise ValueError("A resolved typed command and canonical operation token are required")
    _request_again(command.request)
    _id(command.colony_id)
    _id(command.queue_id)
    if command.kind == "cancel_construction":
        if any(value is not None for value in (command.district_id, command.slot_index,
                                               command.source_zone, command.source_building)):
            raise ValueError("A cancellation cannot carry zone or building fields")
    else:
        _id(command.district_id)
        if type(command.slot_index) is not int or not 0 <= command.slot_index < 32:
            raise ValueError("Invalid resolved district slot")
        if command.kind == "convert_zone":
            if command.source_building is not None:
                raise ValueError("Zone conversion cannot carry a source building")
            if command.source_zone is not None:
                _key(command.source_zone, "zone")
            elif command.request.zone_id is not None:
                raise ValueError("An occupied zone conversion requires the source zone type")
        else:
            _key(command.source_zone, "zone")
            _key(command.source_building, "building")
    return {**command.to_dict(), "country_id": "0", "token": token}


def _progress(item: dict) -> bool:
    progress, needed = item.get("progress"), item.get("progress_needed")
    return (type(progress) in (int, float) and type(needed) in (int, float)
            and math.isfinite(progress) and math.isfinite(needed) and 0 <= progress < needed)


def _window(before: dict, after: dict) -> bool:
    first, second = before.get("metadata", {}), after.get("metadata", {})
    return (before.get("country_id") == after.get("country_id") == "0"
            and isinstance(first.get("campaign_id"), str) and bool(first["campaign_id"])
            and first.get("campaign_id") == second.get("campaign_id")
            and isinstance(first.get("game_version"), str) and bool(first["game_version"])
            and first["game_version"] == second.get("game_version")
            and re.fullmatch(r"[0-9]{4}\.[0-9]{2}\.[0-9]{2}", before["date"], re.ASCII) is not None
            and re.fullmatch(r"[0-9]{4}\.[0-9]{2}\.[0-9]{2}", after["date"], re.ASCII) is not None
            and before["date"] <= after["date"] and before["date"][:7] == after["date"][:7])


def _payment(before: dict, after: dict, costs: dict, refund: bool = False) -> bool:
    if not costs or not any(type(value) in (int, float) and value > 0 for value in costs.values()):
        return False
    for resource, cost in costs.items():
        if type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0:
            return False
        if cost:
            old, new = before["resources"].get(resource), after["resources"].get(resource)
            if (type(old) not in (int, float) or type(new) not in (int, float)
                    or not math.isfinite(old) or not math.isfinite(new)
                    or not math.isclose((new - old) if refund else (old - new), cost,
                                        rel_tol=1e-8, abs_tol=0.0001)):
                return False
    return True


def verify_planet_command(before: dict, after: dict, command: ResolvedPlanetCommand) -> bool:
    """Conservative exact receipts; native validation/dispatch acknowledgement is insufficient."""
    try:
        if not _window(before, after) or resolve_planet_command(before, command.request) != command:
            return False
        _, colony, queue_id = _owned_planet(after, command.request.planet_id)
        if colony != command.colony_id or queue_id != command.queue_id:
            return False
        old_planet = before["planets_by_id"][command.request.planet_id]
        new_planet = after["planets_by_id"][command.request.planet_id]
        old_items, new_items = before["construction"]["items_by_id"], after["construction"]["items_by_id"]
        if command.kind == "cancel_construction":
            item = old_items[command.request.item_id]
            return (command.request.item_id not in new_items
                    and command.request.item_id not in _queue_items(after, queue_id)
                    and {key: value for key, value in old_items.items() if key != command.request.item_id} == new_items
                    and old_planet == new_planet and _payment(before, after, item["resources"], refund=True))
        if new_planet.get("zones_known") is not True:
            return False
        if command.kind == "demolish_building":
            expected = deepcopy(old_planet)
            del expected["zones_by_id"][command.request.zone_id]["buildings_by_id"][command.request.building_id]
            return new_planet == expected and new_items == old_items
        if resolve_planet_command(after, command.request) != command or old_planet != new_planet:
            return False
        added = [item for key, item in new_items.items() if key not in old_items and item.get("queue_id") == queue_id]
        if (len(added) != 1 or set(new_items) - set(old_items) != {added[0].get("id")}
                or any(new_items.get(key) != value for key, value in old_items.items())):
            return False
        item = added[0]
        if (item.get("paying_country") != "0" or not _progress(item)
                or item.get("target") != {"planet_id": command.request.planet_id, "colony_id": colony}
                or not _payment(before, after, item["resources"])):
            return False
        if command.kind == "upgrade_building":
            return item.get("buildable_type") == "buildable_planet_upgrade_building" and item.get("buildable") == {
                "building": command.request.target_building, "planet": int(colony),
                "zone": int(command.request.zone_id), "upgrade_building": int(command.request.building_id)}
        return item.get("buildable_type") == "buildable_zone" and item.get("buildable") == {
            "zone": command.request.target_zone, "planet": int(colony),
            "district": int(command.district_id), "zone_slot": command.slot_index}
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return False
