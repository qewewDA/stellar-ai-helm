"""Assign a ship design to one owned ship through set_ship_design_command (pinned 4.5.2).

These helpers read snapshots, prepare typed requests and verify outcomes;
they never attach to a process.

Static evidence (4.5.2), see native_ship_design.js: the command carries one
design handle and a list of ship handles. The current design is
``ships.<id>.ship_design_implementation.design`` (and an optional pending
``upgrade``). The game's validator decides whether that design can be
applied to that ship.
"""
from __future__ import annotations

from dataclasses import dataclass

from .control_commands import _stockpiles, _unchanged
from .pdx import PDXNode
from .ship_commands import _handle, _id, _one

SET_SHIP_DESIGN = "set_ship_design"
_UNSET = 4294967295


def _design_ref(node) -> dict | None:
    if not isinstance(node, PDXNode):
        return None
    design = _handle(_one(node, "design"))
    if design is None:
        return None
    upgrade = _one(node, "upgrade")
    if upgrade == _UNSET:
        upgrade_id = None
    else:
        upgrade_id = _handle(upgrade)
        if upgrade_id is None:
            return None
    return {"design_id": design, "upgrade_id": upgrade_id}


def observe_ships(root: PDXNode, country_id: str, fleets: list) -> dict:
    """Current design (and pending upgrade) of every ship in one of our fleets."""
    result = {"known": False, "by_id": {}}
    if not isinstance(fleets, list):
        return result
    fleet_ids = set()
    for fleet in fleets:
        if not isinstance(fleet, dict) or _handle(fleet.get("id")) is None:
            return result
        fleet_ids.add(fleet["id"])
    ships = _one(root, "ships")
    if not isinstance(ships, PDXNode):
        return result
    found: dict[str, dict] = {}
    for ship_id, ship in ships.entries:
        if not isinstance(ship, PDXNode):
            if ship == "none" and _handle(ship_id) is not None:
                continue
            return result
        handle = _handle(ship_id)
        fleet_id = _handle(_one(ship, "fleet"))
        if handle is None or fleet_id is None:
            return result
        if fleet_id not in fleet_ids:
            continue
        if handle in found or len(ships.get_all(ship_id)) != 1:
            return result
        implementation = _design_ref(_one(ship, "ship_design_implementation"))
        if implementation is None:
            return result
        found[handle] = {"id": handle, "fleet_id": fleet_id, **implementation}
    return {"known": True, "by_id": found}


@dataclass(frozen=True)
class ShipDesignRequest:
    ship_id: str
    design_id: str
    kind: str = SET_SHIP_DESIGN

    def to_dict(self) -> dict:
        return {"ship_id": self.ship_id, "design_id": self.design_id}


def ship_design_request(kind: str, *, ship_id, design_id) -> ShipDesignRequest:
    if kind != SET_SHIP_DESIGN:
        raise ValueError("Unsupported ship-design command")
    return ShipDesignRequest(_id(ship_id), _id(design_id))


@dataclass(frozen=True)
class ResolvedShipDesignCommand:
    request: ShipDesignRequest
    current_design_id: str
    current_upgrade_id: str | None
    fleet_id: str

    @property
    def kind(self) -> str:
        return self.request.kind

    def to_dict(self) -> dict:
        return self.request.to_dict()


def _ships(state: dict) -> dict:
    observed = state.get("ships")
    if not isinstance(observed, dict) or observed.get("known") is not True or not isinstance(observed.get("by_id"), dict):
        raise ValueError("Our ships are not exactly observable in this snapshot")
    return observed["by_id"]


def resolve_ship_design_command(state: dict, request: ShipDesignRequest) -> ResolvedShipDesignCommand:
    if type(request) is not ShipDesignRequest:
        raise ValueError("A typed ship-design request is required")
    request = ship_design_request(request.kind, ship_id=request.ship_id, design_id=request.design_id)
    if state.get("country_id") != "0":
        raise ValueError("Ship-design commands are restricted to country 0")
    ship = _ships(state).get(request.ship_id)
    if not isinstance(ship, dict) or _id(str(ship.get("design_id"))) is None or _id(str(ship.get("fleet_id"))) is None:
        raise ValueError("That ship is not exactly observable in this snapshot")
    if ship["design_id"] == request.design_id and ship.get("upgrade_id") in {None, request.design_id}:
        raise ValueError("That ship already uses the requested design")
    designs = (state.get("shipbuilding") or {}).get("designs_by_id")
    if not isinstance(designs, dict) or (state.get("shipbuilding") or {}).get("designs_known") is not True:
        raise ValueError("Our ship designs are not exactly observable in this snapshot")
    if request.design_id not in designs:
        raise ValueError("That design is not in our current design collection")
    upgrade = ship.get("upgrade_id")
    if upgrade is not None:
        upgrade = _id(str(upgrade))
    return ResolvedShipDesignCommand(request, ship["design_id"], upgrade, ship["fleet_id"])


def native_ship_design_payload(command: ResolvedShipDesignCommand, token: str) -> dict:
    if type(command) is not ResolvedShipDesignCommand:
        raise ValueError("A resolved ship-design command is required")
    return {"kind": command.kind, "country_id": "0", "token": token, **command.to_dict()}


def verify_ship_design_command(before: dict, after: dict, command: ResolvedShipDesignCommand) -> bool:
    """That ship now uses the requested design; only optional refit resources may fall."""
    if type(command) is not ResolvedShipDesignCommand:
        return False
    try:
        if resolve_ship_design_command(before, command.request) != command:
            return False
        if after.get("date") != before.get("date") or not _unchanged(
                before, after, "country_id", "empire_build", "planets_by_id", "construction"):
            return False
        old, new = _ships(before), _ships(after)
        if set(old) != set(new):
            return False
        ship = new.get(command.request.ship_id)
        if (not isinstance(ship, dict) or ship.get("design_id") != command.request.design_id
                or ship.get("fleet_id") != command.fleet_id):
            return False
        expected = {key: value for key, value in old.items() if key != command.request.ship_id}
        leftover = {key: value for key, value in new.items() if key != command.request.ship_id}
        if leftover != expected:
            return False
        first, second = _stockpiles(before), _stockpiles(after)
        if first is None or second is None or set(first) != set(second):
            return False
        spendable = {"alloys", "minerals", "energy", "influence"}
        for key, value in first.items():
            if key in spendable:
                if second[key] > value:
                    return False
            elif second[key] != value:
                return False
        return True
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return False


__all__ = ["SET_SHIP_DESIGN", "ShipDesignRequest", "ResolvedShipDesignCommand", "observe_ships",
           "ship_design_request", "resolve_ship_design_command", "native_ship_design_payload",
           "verify_ship_design_command"]
