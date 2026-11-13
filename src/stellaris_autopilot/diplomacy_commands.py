"""Unilateral diplomatic actions (close/open borders) through the normal proposal command.

Pinned to the 4.5.2 build. These helpers read snapshots, prepare typed
requests and verify outcomes; they never attach to a process.

Static evidence (4.5.2), see native_diplomacy.js: the console peace command
(0xeb8490) and the diplomacy view build a diplomatic action with the
per-type factory 0x9a9860, set sender/receiver (0x940120 / 0x940140) and
dispatch a ``diplo_action_propose_command`` (factory 0x9d8280, vtable
0x23b4138). Its validator 0x9d5e90 forwards the reason buffer to the action
check 0x942130.

Live 4.5.2 dump: each country's ``relations_manager`` holds one ``relation``
per known country (``owner``, ``country``, ``contact``, optional
``communications``, ``hostile``, ``closed_borders``, ``relation_current``,
``diplo_action_dates`` ...). ``closed_borders=yes`` sits on the closing
side's relation (seen on monsters' relations to country 0), so closing our
borders must add it to our relation to the target, and opening must remove
it. Everything else on our side of every relation must stay unchanged on the
same paused day, apart from the target's ``diplo_action_dates`` bookkeeping.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from .pdx import PDXNode
from .ship_commands import _id

DIPLOMACY_KINDS = frozenset({"close_borders", "open_borders"})
_DATE = re.compile(r"\d{4}\.\d{2}\.\d{2}\Z", re.ASCII)
_BOOKKEEPING = frozenset({"diplo_action_dates"})


def _canonical(value):
    if isinstance(value, PDXNode):
        return [[key, _canonical(item)] for key, item in value.items()]
    return str(value)


def _flag(node: PDXNode, key: str) -> bool | None:
    values = node.get_all(key)
    if not values:
        return False
    if len(values) != 1 or not isinstance(values[0], str) or values[0] not in {"yes", "no"}:
        return None
    return values[0] == "yes"


def observe_diplomacy(root: PDXNode, country_id: str = "0") -> dict:
    """Our side of every relation, keyed by the other country's id."""
    result = {"known": False, "by_country": {}}
    countries = root.get_all("country") if isinstance(root, PDXNode) else []
    if len(countries) != 1 or not isinstance(countries[0], PDXNode):
        return result
    own = countries[0].get_all(country_id)
    if len(own) != 1 or not isinstance(own[0], PDXNode):
        return result
    managers = own[0].get_all("relations_manager")
    if len(managers) != 1 or not isinstance(managers[0], PDXNode):
        return result
    found = {}
    for relation in managers[0].get_all("relation"):
        if not isinstance(relation, PDXNode):
            return result
        owner, other = relation.get_all("owner"), relation.get_all("country")
        if owner != [int(country_id)] and owner != [country_id]:
            return result
        if len(other) != 1 or not re.fullmatch(r"0|[1-9][0-9]{0,8}", str(other[0])):
            return result
        key = str(other[0])
        flags = {name: _flag(relation, name) for name in ("contact", "communications", "hostile", "closed_borders")}
        if key in found or key == country_id or any(value is None for value in flags.values()):
            return result
        their = countries[0].get_all(key)
        kind = their[0].get("type") if len(their) == 1 and isinstance(their[0], PDXNode) else None
        found[key] = {"country": key, "country_type": kind if isinstance(kind, str) else None, **flags,
                      "relation": _canonical(relation)}
    result.update(known=True, by_country=found)
    return result


@dataclass(frozen=True)
class DiplomacyRequest:
    kind: str
    target_country_id: str

    def to_dict(self) -> dict:
        return {"target_country_id": self.target_country_id}


def diplomacy_request(kind: str, *, target_country_id) -> DiplomacyRequest:
    if kind not in DIPLOMACY_KINDS:
        raise ValueError("Unsupported diplomatic action")
    target = _id(target_country_id)
    if target == "0" or int(target) > 0xFFFFFF:
        raise ValueError("Target another country by its observed id")
    return DiplomacyRequest(kind, target)


@dataclass(frozen=True)
class ResolvedDiplomacyCommand:
    request: DiplomacyRequest

    @property
    def kind(self) -> str:
        return self.request.kind

    def to_dict(self) -> dict:
        return self.request.to_dict()


def _again(request: DiplomacyRequest) -> DiplomacyRequest:
    if type(request) is not DiplomacyRequest:
        raise ValueError("A typed diplomacy request is required")
    return diplomacy_request(request.kind, **request.to_dict())


def _relations(state: dict) -> dict:
    if state.get("country_id") != "0":
        raise ValueError("Diplomatic actions have only been verified for country 0")
    diplomacy = state.get("diplomacy")
    if (not isinstance(diplomacy, dict) or diplomacy.get("known") is not True
            or not isinstance(diplomacy.get("by_country"), dict)):
        raise ValueError("Diplomatic relations are not exactly observable in this snapshot")
    return diplomacy["by_country"]


def resolve_diplomacy_command(state: dict, request: DiplomacyRequest) -> ResolvedDiplomacyCommand:
    request = _again(request)
    relation = _relations(state).get(request.target_country_id)
    if not isinstance(relation, dict) or relation.get("contact") is not True:
        raise ValueError("Select a country we have contact with")
    closing = request.kind == "close_borders"
    if relation.get("closed_borders") is closing:
        raise ValueError("Our borders are already " + ("closed" if closing else "open") + " to that country")
    return ResolvedDiplomacyCommand(request)


def native_diplomacy_payload(command: ResolvedDiplomacyCommand, token: str) -> dict:
    if type(command) is not ResolvedDiplomacyCommand:
        raise ValueError("A resolved diplomacy command is required")
    request = _again(command.request)
    return {"kind": request.kind, "country_id": "0", "token": token, **request.to_dict()}


def _without(relation: list, drop: frozenset) -> list:
    return [entry for entry in relation if entry[0] not in drop]


def verify_diplomacy_command(before: dict, after: dict, command: ResolvedDiplomacyCommand) -> bool:
    """Our border flag toward the target flipped on the same paused day; nothing else of ours moved."""
    if type(command) is not ResolvedDiplomacyCommand:
        return False
    try:
        request = _again(command.request)
        if (not isinstance(before.get("date"), str) or _DATE.fullmatch(before["date"]) is None
                or after.get("date") != before["date"] or after.get("country_id") != "0"
                or before.get("metadata", {}).get("campaign_id") != after.get("metadata", {}).get("campaign_id")):
            return False
        first, second = _relations(before), _relations(after)
        if set(first) != set(second):
            return False
        closing = request.kind == "close_borders"
        target = request.target_country_id
        old, new = first.get(target), second.get(target)
        if not isinstance(old, dict) or not isinstance(new, dict):
            return False
        if old.get("closed_borders") is closing or new.get("closed_borders") is not closing:
            return False
        ignored = _BOOKKEEPING | {"closed_borders"}
        if _without(old["relation"], ignored) != _without(new["relation"], ignored):
            return False
        return all(second[key] == value for key, value in first.items() if key != target)
    except (ValueError, TypeError, KeyError, AttributeError):
        return False
