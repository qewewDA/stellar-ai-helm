"""Unilateral diplomatic actions through the normal diplomatic proposal command.

Pinned to the 4.5.2 build. These helpers read snapshots, prepare typed
requests and verify outcomes; they never attach to a process.

Static evidence (4.5.2), see native_diplomacy.js: the console peace command
(0xeb8490) and the diplomacy view build a diplomatic action with the
per-type factory 0x9a9860, set sender/receiver (0x940120 / 0x940140) and
dispatch a ``diplo_action_propose_command`` (factory 0x9d8280, vtable
0x23b4138). Its validator 0x9d5e90 forwards the reason buffer to the action
check 0x942130. Only actions marked ``auto_accepted = yes`` in
common/diplomatic_actions/00_actions.txt are offered: close/open borders,
insult, declare rival, end rivalry and recall embassy. Establishing an
embassy needs the recipient's acceptance and is not offered.

The relation writer 0x66a7d0 writes, per known country, ``owner``,
``country``, ``contact``, ``hostile`` ... ``is_rival``, ``embassy``,
``rival_date`` ... ``modifier`` ... ``communications`` ... ``closed_borders``
... ``relation_current`` ... ``diplo_action_dates``. Our side is
``country.0.relations_manager``; the target's side (``owner=target
country=0``) carries the opinion modifiers it holds about us.

Verification is exact on our side: the one flag the action controls flips
on the same paused day and nothing else of ours changes, apart from
``diplo_action_dates`` bookkeeping and, for rivalry, ``rival_date``. An
insult changes no flag of ours; it must add an ``opinion_insult`` modifier
to the target's opinion of us.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re

from .pdx import PDXNode
from .ship_commands import _id

# kind -> (our flag, value before, value after, extra fields allowed to change)
_FLAG_ACTIONS = {
    "close_borders": ("closed_borders", False, True, frozenset()),
    "open_borders": ("closed_borders", True, False, frozenset()),
    "make_rival": ("is_rival", False, True, frozenset({"rival_date"})),
    "end_rivalry": ("is_rival", True, False, frozenset({"rival_date"})),
    "recall_embassy": ("embassy", True, False, frozenset()),
}
INSULT_MODIFIER = "opinion_insult"
DIPLOMACY_KINDS = frozenset(_FLAG_ACTIONS) | {"insult"}
_FLAGS = ("contact", "communications", "hostile", "closed_borders", "is_rival", "embassy")
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


def _relations(manager_owner: PDXNode, owner: str) -> dict | None:
    managers = manager_owner.get_all("relations_manager")
    if len(managers) != 1 or not isinstance(managers[0], PDXNode):
        return None
    found = {}
    for relation in managers[0].get_all("relation"):
        if not isinstance(relation, PDXNode):
            return None
        owners, others = relation.get_all("owner"), relation.get_all("country")
        if len(owners) != 1 or str(owners[0]) != owner:
            return None
        if len(others) != 1 or not re.fullmatch(r"0|[1-9][0-9]{0,9}", str(others[0])):
            return None
        key = str(others[0])
        if key in found or key == owner:
            return None
        found[key] = relation
    return found


def _their_side(countries: PDXNode, other: str, us: str) -> dict:
    nodes = countries.get_all(other)
    if len(nodes) != 1 or not isinstance(nodes[0], PDXNode):
        return {"known": False}
    kind = nodes[0].get("type")
    relations = _relations(nodes[0], other) if nodes[0].get_all("relations_manager") else {}
    if relations is None:
        return {"known": False, "country_type": kind if isinstance(kind, str) else None}
    relation = relations.get(us)
    modifiers = []
    if relation is not None:
        for modifier in relation.get_all("modifier"):
            name = modifier.get("modifier") if isinstance(modifier, PDXNode) else None
            if not isinstance(name, str):
                return {"known": False, "country_type": kind if isinstance(kind, str) else None}
            modifiers.append(name)
    return {"known": True, "country_type": kind if isinstance(kind, str) else None,
            "modifiers": sorted(modifiers), "relation": _canonical(relation) if relation is not None else None}


def observe_diplomacy(root: PDXNode, country_id: str = "0") -> dict:
    """Our side of every relation, keyed by the other country's id (its full handle)."""
    result = {"known": False, "by_country": {}}
    countries = root.get_all("country") if isinstance(root, PDXNode) else []
    if len(countries) != 1 or not isinstance(countries[0], PDXNode):
        return result
    own = countries[0].get_all(country_id)
    if len(own) != 1 or not isinstance(own[0], PDXNode):
        return result
    relations = _relations(own[0], country_id)
    if relations is None:
        return result
    found = {}
    for key, relation in relations.items():
        flags = {name: _flag(relation, name) for name in _FLAGS}
        if any(value is None for value in flags.values()):
            return result
        their = _their_side(countries[0], key, country_id)
        found[key] = {"country": key, "country_type": their.get("country_type"), **flags,
                      "relation": _canonical(relation),
                      "their_side": {name: value for name, value in their.items() if name != "country_type"}}
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
    if target == "0":
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


def _observed(state: dict) -> dict:
    if state.get("country_id") != "0":
        raise ValueError("Diplomatic actions have only been verified for country 0")
    diplomacy = state.get("diplomacy")
    if (not isinstance(diplomacy, dict) or diplomacy.get("known") is not True
            or not isinstance(diplomacy.get("by_country"), dict)):
        raise ValueError("Diplomatic relations are not exactly observable in this snapshot")
    return diplomacy["by_country"]


_ALREADY = {"close_borders": "Our borders are already closed to that country",
            "open_borders": "Our borders are already open to that country",
            "make_rival": "That country is already our rival",
            "end_rivalry": "That country is not our rival",
            "recall_embassy": "We have no embassy with that country"}


def resolve_diplomacy_command(state: dict, request: DiplomacyRequest) -> ResolvedDiplomacyCommand:
    request = _again(request)
    relation = _observed(state).get(request.target_country_id)
    if not isinstance(relation, dict) or relation.get("contact") is not True:
        raise ValueError("Select a country we have contact with")
    if request.kind in _FLAG_ACTIONS:
        flag, before, _, _ = _FLAG_ACTIONS[request.kind]
        if relation.get(flag) is not before:
            raise ValueError(_ALREADY[request.kind])
    elif relation.get("their_side", {}).get("known") is not True:
        raise ValueError("The target's opinion of us is not exactly observable")
    return ResolvedDiplomacyCommand(request)


def native_diplomacy_payload(command: ResolvedDiplomacyCommand, token: str) -> dict:
    if type(command) is not ResolvedDiplomacyCommand:
        raise ValueError("A resolved diplomacy command is required")
    request = _again(command.request)
    return {"kind": request.kind, "country_id": "0", "token": token, **request.to_dict()}


def _without(relation: list, drop: frozenset) -> list:
    return [entry for entry in relation if entry[0] not in drop]


def verify_diplomacy_command(before: dict, after: dict, command: ResolvedDiplomacyCommand) -> bool:
    """The controlled change happened on the same paused day; nothing else of ours moved."""
    if type(command) is not ResolvedDiplomacyCommand:
        return False
    try:
        request = _again(command.request)
        if (not isinstance(before.get("date"), str) or _DATE.fullmatch(before["date"]) is None
                or after.get("date") != before["date"] or after.get("country_id") != "0"
                or before.get("metadata", {}).get("campaign_id") != after.get("metadata", {}).get("campaign_id")):
            return False
        first, second = _observed(before), _observed(after)
        if set(first) != set(second):
            return False
        target = request.target_country_id
        old, new = first.get(target), second.get(target)
        if not isinstance(old, dict) or not isinstance(new, dict):
            return False
        if request.kind in _FLAG_ACTIONS:
            flag, was, now, extra = _FLAG_ACTIONS[request.kind]
            if old.get(flag) is not was or new.get(flag) is not now:
                return False
            ignored = _BOOKKEEPING | extra | {flag}
        else:
            ignored = _BOOKKEEPING
            theirs_old, theirs_new = old.get("their_side", {}), new.get("their_side", {})
            if theirs_old.get("known") is not True or theirs_new.get("known") is not True:
                return False
            added = Counter(theirs_new["modifiers"]) - Counter(theirs_old["modifiers"])
            if added.get(INSULT_MODIFIER, 0) < 1:
                return False
        if _without(old["relation"], ignored) != _without(new["relation"], ignored):
            return False
        if any(old.get(name) != new.get(name) for name in _FLAGS if name not in ignored):
            return False
        return all(second[key] == value for key, value in first.items() if key != target)
    except (ValueError, TypeError, KeyError, AttributeError):
        return False
