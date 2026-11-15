"""Army recruitment through a colony's normal army build queue (pinned 4.5.2).

These helpers read snapshots, prepare typed requests and verify outcomes;
they never attach to a process.

Static evidence (4.5.2), see native_army.js: ``buildable_army`` (token 0x3dff)
carries an army type, a species handle, the colony handle and a starbase
handle (unused for planets) and is posted with ``add_buildable_to_queue_command``
to the colony's ``army_build_queue`` (queue ``type=army``, location type 2 =
the physical planet). A queued item is saved as
``buildable_army = { army_type="<key>" species=<id> planet=<colony id> starbase=<id> }``
(writer 0xa30050). The game's validator decides species eligibility, limits
and affordability.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from .control_commands import _controls, _queue_members, _stockpiles, _unchanged
from .pdx import PDXNode
from .ship_commands import _id, _one, paid_job

ARMY_KIND = "recruit_army"
_KEY = re.compile(r"[a-z][a-z0-9_]{0,120}\Z", re.ASCII)
_NONE = 4294967295


def _handle(value) -> str | None:
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        text = str(value)
        if text.isascii() and text.isdecimal() and int(text) < _NONE:
            return text
    return None


def observe_army_queues(root: PDXNode, country_id: str) -> dict:
    """Each owned planet's colony army queue and current army ids."""
    result = {"known": False, "by_id": {}}
    planets = _one(root, "planet")
    if not root.get_all("planet"):
        planets = _one(_one(root, "planets"), "planet")
    colonies = _one(root, "colony")
    queues = _one(_one(_one(root, "construction"), "queue_mgr"), "queues")
    if not all(isinstance(value, PDXNode) for value in (planets, colonies, queues)):
        return result
    found = {}
    for planet_id, planet in planets.entries:
        if not isinstance(planet, PDXNode) or str(_one(planet, "owner")) != country_id:
            continue
        colony_id = _handle(_one(planet, "colony"))
        if _handle(planet_id) is None or len(planets.get_all(planet_id)) != 1 or colony_id is None:
            return result
        colony = _one(colonies, colony_id)
        carrier = _one(colony, "carrier")
        if (not isinstance(colony, PDXNode) or _one(carrier, "type") != "planet"
                or _handle(_one(carrier, "reference")) != planet_id):
            return result
        references = colony.get_all("army_build_queue")
        if len(references) > 1:
            return result
        queue_id = _handle(references[0]) if references else None
        if queue_id is None:
            continue  # No army queue (e.g. a colony that cannot recruit).
        queue = _one(queues, queue_id)
        location = _one(queue, "location")
        if (not isinstance(queue, PDXNode) or len(queues.get_all(queue_id)) != 1
                or str(_one(queue, "owner")) != country_id or _one(queue, "type") != "army"
                or _one(location, "type") != 2 or _handle(_one(location, "id")) != planet_id):
            return result
        armies = colony.get_all("army")
        if len(armies) > 1 or (armies and not isinstance(armies[0], PDXNode)):
            return result
        army_ids = []
        for key, value in (armies[0].entries if armies else []):
            army_id = _handle(value)
            if key is not None or army_id is None or army_id in army_ids:
                return result
            army_ids.append(army_id)
        origin = _handle(_one(_one(planet, "coordinate"), "origin"))
        found[planet_id] = {"planet_id": planet_id, "colony_id": colony_id, "queue_id": queue_id,
                            "system_id": origin, "army_ids": army_ids}
    result.update(known=True, by_id=found)
    return result


@dataclass(frozen=True)
class ArmyRequest:
    planet_id: str
    army_type: str
    species_id: str
    starbase_id: str | None = None
    kind: str = ARMY_KIND

    def to_dict(self) -> dict:
        return {"planet_id": self.planet_id, "army_type": self.army_type, "species_id": self.species_id,
                "starbase_id": self.starbase_id if self.starbase_id is not None else str(_NONE)}


def army_request(kind: str, definition=None, *, planet_id, army_type, species_id,
                 starbase_id=None) -> ArmyRequest:
    """One army on one planet; ``starbase_id`` makes it rally at that starbase when done."""
    if kind != ARMY_KIND:
        raise ValueError("Unsupported army command")
    if definition is not True:
        raise ValueError("The army type must be found in versioned game knowledge")
    if not isinstance(army_type, str) or not _KEY.fullmatch(army_type):
        raise ValueError("Invalid army type id")
    return ArmyRequest(_id(planet_id), army_type, _id(species_id),
                       None if starbase_id is None else _id(starbase_id))


@dataclass(frozen=True)
class ResolvedArmyCommand:
    request: ArmyRequest
    colony_id: str
    queue_id: str

    @property
    def kind(self) -> str:
        return self.request.kind

    def to_dict(self) -> dict:
        return {**self.request.to_dict(), "colony_id": self.colony_id, "queue_id": self.queue_id}


def _again(request: ArmyRequest) -> ArmyRequest:
    if type(request) is not ArmyRequest:
        raise ValueError("A typed army request is required")
    return army_request(request.kind, True, planet_id=request.planet_id, army_type=request.army_type,
                        species_id=request.species_id, starbase_id=request.starbase_id)


def _army_queues(state: dict) -> dict:
    observed = state.get("army_queues")
    if not isinstance(observed, dict) or observed.get("known") is not True or not isinstance(observed.get("by_id"), dict):
        raise ValueError("Colony army queues are not exactly observable in this snapshot")
    return observed["by_id"]


def resolve_army_command(state: dict, request: ArmyRequest) -> ResolvedArmyCommand:
    request = _again(request)
    if state.get("country_id") != "0":
        raise ValueError("Army recruitment is restricted to country 0")
    entry = _army_queues(state).get(request.planet_id)
    if not isinstance(entry, dict) or entry.get("planet_id") != request.planet_id:
        raise ValueError("Select an owned planet with an observed army build queue")
    queue_id = _id(entry.get("queue_id"))
    colony_id = _id(entry.get("colony_id"))
    _queue_members(state, queue_id)
    queue = state["construction"]["queues_by_id"][queue_id]
    if queue.get("type") != "army" or queue.get("location") != {"type": 2, "id": request.planet_id}:
        raise ValueError("The planet's army build queue is not exactly observable")
    if any(isinstance(item, dict) and item.get("queue_id") == queue_id
           for item in state["construction"]["items_by_id"].values()):
        raise ValueError("That planet already has an army in its build queue")
    if request.starbase_id is not None and request.planet_id not in starbase_army_planets(state, request.starbase_id):
        raise ValueError("The planet is not an eligible army planet in that starbase's system")
    return ResolvedArmyCommand(request, colony_id=colony_id, queue_id=queue_id)


def _starbase_system(state: dict, starbase_id: str) -> str:
    starbase = _controls(state, "starbase_queues").get(starbase_id)
    if not isinstance(starbase, dict) or starbase.get("owner") != "0" or starbase.get("id") != starbase_id:
        raise ValueError("Select an observed owned starbase")
    fleet = state.get("fleets_by_id", {}).get(starbase.get("fleet_id"))
    system_id = fleet.get("system_id") if isinstance(fleet, dict) else None
    return _id(system_id)


def starbase_army_planets(state: dict, starbase_id: str) -> list[str]:
    """Owned planets in the starbase's system with an idle (empty) army queue, in id order."""
    system_id = _starbase_system(state, _id(starbase_id))
    items = state.get("construction", {}).get("items_by_id", {})
    busy = {item.get("queue_id") for item in items.values() if isinstance(item, dict)}
    planets = [entry["planet_id"] for entry in _army_queues(state).values()
               if isinstance(entry, dict) and entry.get("system_id") == system_id and entry.get("queue_id") not in busy]
    return sorted(planets, key=int)


def native_army_payload(command: ResolvedArmyCommand, token: str) -> dict:
    if type(command) is not ResolvedArmyCommand:
        raise ValueError("A resolved army command is required")
    _again(command.request)
    _id(command.colony_id)
    _id(command.queue_id)
    return {"kind": command.kind, "country_id": "0", "token": token, **command.to_dict()}


def _buildable_matches(buildable, request: ArmyRequest, colony_id: str) -> bool:
    if not isinstance(buildable, dict):
        return False
    expected = {"army_type": request.army_type, "species": int(request.species_id), "planet": int(colony_id)}
    extra = {key: value for key, value in buildable.items() if key not in expected}
    if request.starbase_id is not None:
        return (all(buildable.get(key) == value for key, value in expected.items())
                and extra == {"starbase": int(request.starbase_id)})
    return (all(buildable.get(key) == value for key, value in expected.items())
            and extra in ({}, {"starbase": _NONE}, {"starbase": -1}))


def verify_army_command(before: dict, after: dict, command: ResolvedArmyCommand) -> bool:
    """One new paid buildable_army item of exactly this type, species and colony, appended to the queue."""
    if type(command) is not ResolvedArmyCommand:
        return False
    try:
        request = _again(command.request)
        if resolve_army_command(before, request) != command:
            return False
        if after.get("date") != before.get("date") or not _unchanged(
                before, after, "country_id", "empire_build", "country_controls", "planets_by_id",
                "fleets_by_id", "shipbuilding", "army_queues"):
            return False
        item = paid_job(before, after, command.queue_id)
        if (item is None or item.get("buildable_type") != "buildable_army"
                or not _buildable_matches(item.get("buildable"), request, command.colony_id)
                or item.get("target") != {"planet_id": request.planet_id, "colony_id": command.colony_id}):
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


__all__ = ["ARMY_KIND", "ArmyRequest", "ResolvedArmyCommand", "observe_army_queues", "army_request",
           "starbase_army_planets",
           "resolve_army_command", "native_army_payload", "verify_army_command"]
