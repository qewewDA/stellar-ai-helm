"""Planetary decisions through the normal enact command (pinned 4.5.2).

These helpers read snapshots, prepare typed requests and verify outcomes;
they never attach to a process.

Static evidence (4.5.2), see native_decision.js: ``enact_decision_planet_command``
(token 0x2b85, factory 0x6b94e0, vtable 0x2391880) carries the decision type
(+0x20), a carrier reference {planet id, type 0 = planet} (+0x28) and the
enacting country (+0x30). The diplomacy-free UI path 0x17d0080 fills exactly
these fields, validates with 0x6b34c0 (decision valid, carrier valid, planet
owner == country, potential/allow, cost) and posts the command.

A queued enactment is saved as a construction item ``buildable_decision =
{ decision="<key>" carrier=... }``; an immediate one adds its planet modifier
(``timed_modifier.items``) and charges the cost. Verification accepts exactly
one of the two on the same paused day with nothing else of ours changed
apart from the charged stockpiles.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re

from .pdx import PDXNode
from .ship_commands import _id

DECISION_KINDS = frozenset({"enact_planet_decision"})
_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,120}\Z", re.ASCII)
_DATE = re.compile(r"\d{4}\.\d{2}\.\d{2}\Z", re.ASCII)
_UNSTABLE = frozenset({"physics_research", "society_research", "engineering_research"})


def _modifiers(planet: PDXNode) -> list | None:
    """Sorted modifier names on one planet's timed_modifier list; None if malformed."""
    blocks = planet.get_all("timed_modifier")
    if not blocks:
        return []
    if len(blocks) != 1 or not isinstance(blocks[0], PDXNode):
        return None
    items = blocks[0].get_all("items")
    if not items:
        return []
    if len(items) != 1 or not isinstance(items[0], PDXNode):
        return None
    names = []
    for key, entry in items[0].entries:
        if key is not None or not isinstance(entry, PDXNode):
            return None
        name = entry.get_all("modifier")
        if len(name) != 1 or not isinstance(name[0], str) or not _KEY.fullmatch(name[0]):
            return None
        names.append(name[0])
    return sorted(names)


def observe_planet_modifiers(root: PDXNode, planet_ids) -> dict:
    """Timed modifiers on our planets, keyed by planet id."""
    result = {"known": False, "by_planet": {}}
    if not isinstance(root, PDXNode):
        return result
    pools = root.get_all("planet")
    if not pools:
        holders = root.get_all("planets")
        if len(holders) != 1 or not isinstance(holders[0], PDXNode):
            return result
        pools = holders[0].get_all("planet")
    if len(pools) != 1 or not isinstance(pools[0], PDXNode):
        return result
    found = {}
    for planet_id in planet_ids:
        nodes = pools[0].get_all(str(planet_id))
        if len(nodes) != 1 or not isinstance(nodes[0], PDXNode):
            return result
        names = _modifiers(nodes[0])
        if names is None:
            return result
        found[str(planet_id)] = names
    result.update(known=True, by_planet=found)
    return result


@dataclass(frozen=True)
class DecisionDefinition:
    cost: tuple = ()
    owned_planets_only: bool = True

    def cost_dict(self) -> dict:
        return dict(self.cost)


def decision_definition(record: dict) -> DecisionDefinition:
    """Exact base cost of one decision from a knowledge lookup."""
    if not isinstance(record, dict) or record.get("status") != "found":
        raise ValueError("The decision was not found unambiguously in versioned game knowledge")
    definition = record.get("definition")
    if not isinstance(definition, dict):
        raise ValueError("The decision record has no definition")
    if definition.get("owned_planets_only") != "yes":
        raise ValueError("Only decisions for our own planets are offered")
    resources = definition.get("resources")
    cost = resources.get("cost", {}) if isinstance(resources, dict) else {} if resources is None else None
    if not isinstance(cost, dict) or not all(
            isinstance(key, str) and _KEY.fullmatch(key) and type(value) in (int, float)
            and math.isfinite(value) and value > 0 for key, value in cost.items()):
        raise ValueError("The decision cost is not a fixed positive resource cost")
    return DecisionDefinition(cost=tuple(sorted((key, float(value)) for key, value in cost.items())))


@dataclass(frozen=True)
class DecisionRequest:
    kind: str
    planet_id: str
    decision_id: str
    definition: DecisionDefinition

    def to_dict(self) -> dict:
        return {"planet_id": self.planet_id, "decision_id": self.decision_id}


def decision_request(kind: str, definition: DecisionDefinition | None = None, *, planet_id, decision_id) -> DecisionRequest:
    if kind not in DECISION_KINDS:
        raise ValueError("Unsupported decision command")
    if type(definition) is not DecisionDefinition:
        raise ValueError("Exact decision facts from versioned knowledge are required")
    if not isinstance(decision_id, str) or not _KEY.fullmatch(decision_id):
        raise ValueError("Invalid decision id")
    return DecisionRequest(kind, _id(planet_id), decision_id, definition)


@dataclass(frozen=True)
class ResolvedDecisionCommand:
    request: DecisionRequest

    @property
    def kind(self) -> str:
        return self.request.kind

    def to_dict(self) -> dict:
        return self.request.to_dict()


def _again(request: DecisionRequest) -> DecisionRequest:
    if type(request) is not DecisionRequest:
        raise ValueError("A typed decision request is required")
    return decision_request(request.kind, request.definition, planet_id=request.planet_id,
                            decision_id=request.decision_id)


def _planet_modifiers(state: dict) -> dict:
    observed = state.get("planet_modifiers")
    if not isinstance(observed, dict) or observed.get("known") is not True:
        raise ValueError("Planet modifiers are not exactly observable in this snapshot")
    return observed["by_planet"]


def resolve_decision_command(state: dict, request: DecisionRequest) -> ResolvedDecisionCommand:
    request = _again(request)
    if state.get("country_id") != "0":
        raise ValueError("Planet decisions have only been verified for country 0")
    if request.planet_id not in (state.get("planets_by_id") or {}):
        raise ValueError("Select one of our observed planets")
    if request.planet_id not in _planet_modifiers(state):
        raise ValueError("That planet's modifiers are not exactly observable")
    stock = state.get("resources") or {}
    for key, value in request.definition.cost:
        if type(stock.get(key)) not in (int, float) or stock[key] < value:
            raise ValueError("The observed stockpile cannot pay the decision's base cost")
    return ResolvedDecisionCommand(request)


def native_decision_payload(command: ResolvedDecisionCommand, token: str) -> dict:
    if type(command) is not ResolvedDecisionCommand:
        raise ValueError("A resolved decision command is required")
    request = _again(command.request)
    return {"kind": request.kind, "country_id": "0", "token": token, **request.to_dict()}


def _stock(state: dict) -> dict | None:
    resources = state.get("resources")
    if not isinstance(resources, dict) or not resources or not all(
            type(value) in (int, float) and math.isfinite(value) for value in resources.values()):
        return None
    return {key: value for key, value in resources.items() if key not in _UNSTABLE}


def _charged_only(before: dict, after: dict, charged: dict, *, exact: bool) -> bool:
    first, second = _stock(before), _stock(after)
    if first is None or second is None or set(first) != set(second):
        return False
    for key, value in first.items():
        if key not in charged:
            if second[key] != value:
                return False
        elif exact:
            if not math.isclose(value - second[key], charged[key], rel_tol=1e-9, abs_tol=1e-6):
                return False
        elif not second[key] < value:
            return False
    return set(charged) <= set(first)


def decision_outcome(before: dict, after: dict, command: ResolvedDecisionCommand) -> str | None:
    """'queued' or 'enacted' when exactly that decision's effect is observed, else None."""
    if type(command) is not ResolvedDecisionCommand:
        return None
    try:
        request = _again(command.request)
        if (not isinstance(before.get("date"), str) or _DATE.fullmatch(before["date"]) is None
                or after.get("date") != before["date"] or after.get("country_id") != "0"
                or before.get("metadata", {}).get("campaign_id") != after.get("metadata", {}).get("campaign_id")):
            return None
        old_items = before["construction"]["items_by_id"]
        new_items = after["construction"]["items_by_id"]
        first, second = _planet_modifiers(before), _planet_modifiers(after)
        if set(first) != set(second):
            return None
        others_same = all(second[key] == value for key, value in first.items() if key != request.planet_id)
        added = [key for key in new_items if key not in old_items]
        if any(new_items.get(key) != value for key, value in old_items.items()):
            return None
        if added:
            item = new_items[added[0]]
            buildable = item.get("buildable") if isinstance(item, dict) else None
            if (len(added) != 1 or item.get("paying_country") != "0"
                    or item.get("buildable_type") != "buildable_decision" or not isinstance(buildable, dict)
                    or buildable.get("decision") != request.decision_id or not others_same
                    or second[request.planet_id] != first[request.planet_id]):
                return None
            costs = item.get("resources")
            if not isinstance(costs, dict) or not _charged_only(
                    before, after, {key: value for key, value in costs.items() if value}, exact=True):
                return None
            return "queued"
        gained = [name for name in second[request.planet_id] if name not in first[request.planet_id]]
        if not gained or not others_same:
            return None
        if not _charged_only(before, after, request.definition.cost_dict(), exact=False):
            return None
        return "enacted"
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def verify_decision_command(before: dict, after: dict, command: ResolvedDecisionCommand) -> bool:
    return decision_outcome(before, after, command) is not None


def list_planet_decisions(state: dict, records: list[dict]) -> dict:
    """Read-only: our planets with their modifiers and the knowledge decisions they could take.

    Availability (potential/allow) needs runtime evaluation; the game's validator
    decides at enactment. Decisions blocked by an observed ``NOT has_modifier``
    allow clause are marked.
    """
    modifiers = state.get("planet_modifiers") or {}
    by_planet = modifiers.get("by_planet") if modifiers.get("known") is True else {}
    decisions = []
    for record in records:
        definition = record.get("definition") if isinstance(record, dict) else None
        if not isinstance(definition, dict) or definition.get("owned_planets_only") != "yes":
            continue
        try:
            cost = decision_definition({"status": "found", "definition": definition}).cost_dict()
        except ValueError:
            continue
        allow = definition.get("allow") if isinstance(definition.get("allow"), dict) else {}
        blocked = allow.get("NOT", {}).get("has_modifier") if isinstance(allow.get("NOT"), dict) else None
        decisions.append({"decision_id": record.get("id"), "name": (record.get("names") or {}).get("simp_chinese")
                          or (record.get("names") or {}).get("english"), "cost": cost,
                          "blocked_by_modifier": blocked if isinstance(blocked, str) else None})
    planets = []
    for planet_id, planet in (state.get("planets_by_id") or {}).items():
        names = by_planet.get(planet_id)
        planets.append({"planet_id": planet_id, "name": planet.get("name"), "modifiers": names,
                        "blocked": sorted(entry["decision_id"] for entry in decisions
                                          if names is not None and entry["blocked_by_modifier"] in names)})
    return {"status": "observed", "side_effects": "none", "planets": planets,
            "decisions": sorted(decisions, key=lambda entry: str(entry["decision_id"])),
            "note": "Potential/allow are evaluated by the game's validator when enacting; not prefiltered."}
