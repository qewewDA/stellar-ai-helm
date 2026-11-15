"""Declare war and offer peace through the normal diplomatic-action commands (pinned 4.5.2).

These helpers read snapshots, prepare typed requests and verify outcomes;
they never attach to a process.

Static evidence (4.5.2), see native_war.js: declare_war is action_declare_war
(0x2d52) with a CWarGoal (type/actor/target) wrapped in
diplo_action_propose_command. offer_peace is action_offer_peace (0x2d53) with
a war handle and a terms enum. The game's validator stays authoritative.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from .pdx import PDXNode
from .ship_commands import _id, _one

DECLARE_WAR = "declare_war"
OFFER_PEACE = "offer_peace"
WAR_KINDS = frozenset({DECLARE_WAR, OFFER_PEACE})
PEACE_TERMS = frozenset({"status_quo", "surrender", "demand_surrender"})
_GOAL = re.compile(r"wg_[A-Za-z0-9_]{1,120}$")


def _handle(value) -> str | None:
    try:
        return _id(str(value) if type(value) is int else value)
    except (TypeError, ValueError):
        return None


def _goal_name(value) -> str | None:
    if isinstance(value, str) and _GOAL.fullmatch(value):
        return value
    if isinstance(value, PDXNode):
        name = _one(value, "type")
        if isinstance(name, str) and _GOAL.fullmatch(name):
            return name
    return None


def _participants(value) -> tuple[set[str], bool]:
    if not isinstance(value, PDXNode):
        identifier = _handle(value)
        return ({identifier}, True) if identifier is not None else (set(), False)
    direct = value.get_all("country")
    if direct:
        ids: set[str] = set()
        known = True
        for item in direct:
            found, understood = _participants(item)
            ids.update(found)
            known = known and understood
        return ids, known
    ids = set()
    known = True
    for key, entry in value.entries:
        if key is None or isinstance(entry, PDXNode):
            found, understood = _participants(entry)
            ids.update(found)
            known = known and understood
        else:
            handle = _handle(entry) if key is None else None
            if handle is not None:
                ids.add(handle)
            else:
                known = False
    return ids, known


def observe_wars(root: PDXNode, country_id: str = "0") -> dict:
    """Every war we participate in, with sides and the attacker war goal."""
    result = {"known": False, "by_id": {}}
    collections = root.get_all("war") if isinstance(root, PDXNode) else []
    if not collections:
        return {"known": True, "by_id": {}}
    found: dict[str, dict] = {}
    for collection in collections:
        if not isinstance(collection, PDXNode):
            return result
        for war_id, war in collection.entries:
            if not isinstance(war, PDXNode):
                if war == "none" and _handle(war_id) is not None:
                    continue
                return result
            handle = _handle(war_id)
            if handle is None or handle in found or len(collection.get_all(war_id)) != 1:
                return result
            attackers, attackers_known = set(), True
            defenders, defenders_known = set(), True
            for group, names, bucket in (
                    ("attackers", ("attackers", "attacker"), "attackers"),
                    ("defenders", ("defenders", "defender"), "defenders"),
            ):
                present = False
                ids: set[str] = set()
                known = True
                for name in names:
                    for side in war.get_all(name):
                        present = True
                        found_ids, understood = _participants(side)
                        ids.update(found_ids)
                        known = known and understood
                if bucket == "attackers":
                    attackers, attackers_known, attackers_present = ids, known, present
                else:
                    defenders, defenders_known, defenders_present = ids, known, present
            if not attackers_present or not defenders_present or not attackers_known or not defenders_known:
                return result
            goal = None
            for key in ("attacker_war_goal", "war_goal"):
                values = war.get_all(key)
                if len(values) > 1:
                    return result
                if len(values) == 1:
                    goal = _goal_name(values[0])
                    if goal is None:
                        return result
            if country_id not in attackers and country_id not in defenders:
                continue
            found[handle] = {
                "id": handle,
                "attackers": sorted(attackers, key=lambda item: int(item)),
                "defenders": sorted(defenders, key=lambda item: int(item)),
                "attacker_war_goal": goal,
                "we_attack": country_id in attackers,
            }
    return {"known": True, "by_id": found}


def _wars(state: dict) -> dict:
    observed = state.get("wars")
    if not isinstance(observed, dict) or observed.get("known") is not True or not isinstance(observed.get("by_id"), dict):
        raise ValueError("Our wars are not exactly observable in this snapshot")
    return observed["by_id"]


@dataclass(frozen=True)
class WarRequest:
    kind: str
    target_country_id: str
    war_goal: str | None = None
    terms: str | None = None
    war_id: str | None = None

    def to_dict(self) -> dict:
        payload = {"target_country_id": self.target_country_id}
        if self.kind == DECLARE_WAR:
            payload["war_goal"] = self.war_goal
        else:
            payload["terms"] = self.terms
            payload["war_id"] = self.war_id
        return payload


def war_request(kind: str, *, target_country_id, war_goal=None, terms=None, war_id=None) -> WarRequest:
    if kind not in WAR_KINDS:
        raise ValueError("Unsupported war command")
    target = _id(target_country_id)
    if target == "0":
        raise ValueError("A country cannot target itself")
    if kind == DECLARE_WAR:
        if not isinstance(war_goal, str) or not _GOAL.fullmatch(war_goal) or terms is not None or war_id is not None:
            raise ValueError("Declare war requires a war_goal and no peace fields")
        return WarRequest(kind, target, war_goal=war_goal)
    if not isinstance(terms, str) or terms not in PEACE_TERMS or war_goal is not None:
        raise ValueError("A peace offer requires status_quo, surrender or demand_surrender")
    return WarRequest(kind, target, terms=terms, war_id=_id(war_id))


@dataclass(frozen=True)
class ResolvedWarCommand:
    request: WarRequest

    @property
    def kind(self) -> str:
        return self.request.kind

    def to_dict(self) -> dict:
        return self.request.to_dict()


def resolve_war_command(state: dict, request: WarRequest) -> ResolvedWarCommand:
    if type(request) is not WarRequest:
        raise ValueError("A typed war request is required")
    request = war_request(request.kind, **request.to_dict())
    if state.get("country_id") != "0":
        raise ValueError("War commands are restricted to country 0")
    wars = _wars(state)
    target = request.target_country_id
    if request.kind == DECLARE_WAR:
        for war in wars.values():
            if not isinstance(war, dict):
                raise ValueError("Our wars are not exactly observable in this snapshot")
            sides = set(war.get("attackers") or ()) | set(war.get("defenders") or ())
            if target in sides:
                raise ValueError("We are already at war with that country")
        return ResolvedWarCommand(request)
    war = wars.get(request.war_id)
    if not isinstance(war, dict):
        raise ValueError("That war is not exactly observable in this snapshot")
    sides = set(war.get("attackers") or ()) | set(war.get("defenders") or ())
    if target not in sides or "0" not in sides:
        raise ValueError("That war does not include us and the target")
    return ResolvedWarCommand(request)


def native_war_payload(command: ResolvedWarCommand, token: str) -> dict:
    if type(command) is not ResolvedWarCommand:
        raise ValueError("A resolved war command is required")
    return {"kind": command.kind, "country_id": "0", "token": token, **command.to_dict()}


def _unchanged_date(before: dict, after: dict) -> bool:
    return after.get("date") == before.get("date") and after.get("country_id") == before.get("country_id")


def verify_war_command(before: dict, after: dict, command: ResolvedWarCommand) -> bool:
    if type(command) is not ResolvedWarCommand:
        return False
    try:
        if resolve_war_command(before, command.request) != command or not _unchanged_date(before, after):
            return False
        old, new = _wars(before), _wars(after)
        target = command.request.target_country_id
        if command.kind == DECLARE_WAR:
            added = {key: war for key, war in new.items() if key not in old}
            if len(added) != 1:
                return False
            war = next(iter(added.values()))
            if (not isinstance(war, dict) or war.get("we_attack") is not True
                    or "0" not in (war.get("attackers") or ())
                    or target not in (war.get("defenders") or ())
                    or war.get("attacker_war_goal") != command.request.war_goal):
                return False
            return {key: value for key, value in new.items() if key not in added} == old
        # Peace offers are proposals the other side decides; the war itself stays
        # until they accept. Verified like other diplo proposals: exactly one
        # new pending action_offer_peace in our_proposals, same wars.
        if new != old:
            return False
        before_rel = ((before.get("diplomacy") or {}).get("by_country") or {}).get(target)
        after_rel = ((after.get("diplomacy") or {}).get("by_country") or {}).get(target)
        if not isinstance(before_rel, dict) or not isinstance(after_rel, dict):
            return False
        earlier = before_rel.get("our_proposals")
        proposals = after_rel.get("our_proposals")
        if not isinstance(earlier, list) or not isinstance(proposals, list):
            return False
        added = [entry for entry in proposals if entry not in earlier]
        return len(added) == 1 and added[0].get("action") == "action_offer_peace"
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return False


__all__ = ["DECLARE_WAR", "OFFER_PEACE", "WAR_KINDS", "PEACE_TERMS", "WarRequest", "ResolvedWarCommand",
           "observe_wars", "war_request", "resolve_war_command", "native_war_payload", "verify_war_command"]
