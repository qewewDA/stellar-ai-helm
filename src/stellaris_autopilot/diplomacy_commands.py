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

Envoy missions are not proposals: proposing ``action_harm_relation`` alone
was observed live (2210.02.05) to only log a ``last_diplo_actions`` entry. The
diplomacy view assigns an envoy with ``assign_leader_command`` (factory
0xbe92f0, vtable 0x23cae08, see native_diplomacy.js): leader handle plus a
location ``{type=empire assignment=harm_relations|improve_relations
id=<target> position=0}``. Resolution picks our lowest-id idle envoy
(``leaders`` entries with ``country=0 class="envoy"``); verified when exactly
that envoy gains that location and nothing else of ours changes.

Proposals that need the recipient's acceptance (embassy, non-aggression
pact, research agreement, commercial pact, migration pact) use the same
proposal command. The AI recipient decides later: a pending proposal is
saved in the recipient's ``incoming_actions`` as ``action_x = { type=propose
actor=0 recipient=<target> id=... timeout=<date> }`` (seen for AI-to-AI
embassy proposals in the 2210.02.05 dump; ``AUTO_DECLINE_DIPLOMACY_DAYS``
180). Verified as ``pending`` when exactly that entry appears, or as
``accepted`` when our relation gains the pact flag (``embassy``,
``non_aggression_pledge``, ``research_agreement``, ``commercial_pact``,
``migration_access``, per relation writer 0x66a7d0). A later decline is
observed as the pending entry gone with the flag still unset; it is never
proposed again automatically.
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
# kind -> envoy assignment written to the leader's location
ENVOY_MISSIONS = {"harm_relations": "harm_relations", "improve_relations": "improve_relations"}
# kind -> (game action key, our relation flag set on acceptance)
PROPOSALS = {
    "propose_embassy": ("action_embassy", "embassy"),
    "propose_non_aggression_pact": ("action_form_non_aggression_pact", "non_aggression_pledge"),
    "propose_research_agreement": ("action_form_research_agreement", "research_agreement"),
    "propose_commercial_pact": ("action_form_commercial_pact", "commercial_pact"),
    "propose_migration_pact": ("action_form_migration_pact", "migration_access"),
}
PACT_FLAGS = ("non_aggression_pledge", "research_agreement", "commercial_pact", "migration_access",
              "defensive_pact", "alliance")
DIPLOMACY_KINDS = frozenset(_FLAG_ACTIONS) | {"insult"} | frozenset(ENVOY_MISSIONS) | frozenset(PROPOSALS)
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


def _pacts(relation: PDXNode) -> dict:
    """Pact flags on our side; a malformed value is reported as None for that pact only."""
    found = {}
    for name in PACT_FLAGS:
        values = relation.get_all(name)
        found[name] = (False if not values else None if len(values) != 1 or not isinstance(values[0], str)
                       or values[0] not in {"yes", "no"} else values[0] == "yes")
    return found


def _scalar(node: PDXNode, key: str) -> str | None:
    values = node.get_all(key)
    if len(values) != 1 or isinstance(values[0], (PDXNode, bool)) or values[0] is None:
        return None
    return str(values[0])


def _our_proposals(country: PDXNode, us: str) -> list | None:
    """Pending proposals from us in one country's ``incoming_actions``; None when unreadable."""
    blocks = country.get_all("incoming_actions")
    if not blocks:
        return []
    if len(blocks) != 1 or not isinstance(blocks[0], PDXNode):
        return None
    found = []
    for item in blocks[0].values():
        if not isinstance(item, PDXNode):
            return None
        for action, body in item.items():
            if not isinstance(body, PDXNode):
                return None
            if _scalar(body, "actor") != us:
                continue
            found.append({"action": action, "type": _scalar(body, "type"), "recipient": _scalar(body, "recipient"),
                          "id": _scalar(body, "id"), "timeout": _scalar(body, "timeout")})
    return sorted(found, key=lambda entry: (entry["action"], str(entry["id"])))


_PROPOSAL_FLAGS = {action: flag for action, flag in PROPOSALS.values()}


def _last_proposals(own: PDXNode) -> dict | None:
    """Our latest proposal of each kind per target from ``ai.last_diplo_actions``.

    The game writes no rejection notice into the save (live 2210.06.22 dump);
    this is the only record that a proposal was made once it has left the
    recipient's ``incoming_actions``. None when unreadable.
    """
    found: dict = {}
    ais = own.get_all("ai")
    if not ais:
        return found
    if len(ais) != 1 or not isinstance(ais[0], PDXNode):
        return None
    blocks = ais[0].get_all("last_diplo_actions")
    if not blocks:
        return found
    if len(blocks) != 1 or not isinstance(blocks[0], PDXNode):
        return None
    for item in blocks[0].values():
        if not isinstance(item, PDXNode):
            return None
        action, target, date = _scalar(item, "action"), _scalar(item, "country"), _scalar(item, "creation_date")
        if action not in _PROPOSAL_FLAGS or target is None:
            continue
        previous = found.setdefault(target, {}).get(action)
        if previous is None or (date or "") > (previous or ""):
            found[target][action] = date
    return found


def _recent_proposals(last: dict | None, target: str, pacts: dict, pending: list | None) -> list | None:
    """Status of our latest proposals to one country: pending, accepted or declined_or_expired."""
    if last is None or pending is None:
        return None
    entries = []
    for action, date in sorted(last.get(target, {}).items()):
        flag = _PROPOSAL_FLAGS[action]
        status = ("pending" if any(entry["action"] == action for entry in pending)
                  else "accepted" if pacts.get(flag) is True
                  else "unknown" if pacts.get(flag) is None
                  else "declined_or_expired")
        entries.append({"action": action, "proposed_on": date, "status": status})
    return entries


def observe_envoys(root: PDXNode, country_id: str = "0") -> dict:
    """Our envoys and their current assignment, keyed by leader id."""
    result = {"known": False, "by_id": {}}
    leaders = root.get_all("leaders") if isinstance(root, PDXNode) else []
    if len(leaders) != 1 or not isinstance(leaders[0], PDXNode):
        return result
    found = {}
    for key, leader in leaders[0].items():
        if not isinstance(leader, PDXNode) or _scalar(leader, "country") != country_id:
            continue
        if _scalar(leader, "class") != "envoy":
            continue
        if key in found or not re.fullmatch(r"0|[1-9][0-9]{0,9}", key):
            return result
        locations = leader.get_all("location")
        if len(locations) > 1 or (locations and not isinstance(locations[0], PDXNode)):
            return result
        location = None
        if locations:
            location = {name: _scalar(locations[0], name) for name in ("type", "assignment", "id")}
        found[key] = {"id": key, "location": location,
                      "idle": location is None or location.get("assignment") in {None, "none"}}
    result.update(known=True, by_id=found)
    return result


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
    last = _last_proposals(own[0])
    for key, relation in relations.items():
        flags = {name: _flag(relation, name) for name in _FLAGS}
        if any(value is None for value in flags.values()):
            return result
        their = _their_side(countries[0], key, country_id)
        others = countries[0].get_all(key)
        proposals = (_our_proposals(others[0], country_id)
                     if len(others) == 1 and isinstance(others[0], PDXNode) else [])
        found[key] = {"country": key, "country_type": their.get("country_type"), **flags,
                      "pacts": _pacts(relation), "our_proposals": proposals,
                      "recent_proposals": _recent_proposals(last, key, {**_pacts(relation), **flags}, proposals),
                      "relation": _canonical(relation),
                      "their_side": {name: value for name, value in their.items() if name != "country_type"}}
    result.update(known=True, by_country=found, envoys=observe_envoys(root, country_id))
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
    leader_id: str | None = None

    @property
    def kind(self) -> str:
        return self.request.kind

    def to_dict(self) -> dict:
        return {**self.request.to_dict(), **({"leader_id": self.leader_id} if self.leader_id is not None else {})}


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


def _envoys(state: dict) -> dict:
    envoys = state.get("diplomacy", {}).get("envoys")
    if not isinstance(envoys, dict) or envoys.get("known") is not True or not isinstance(envoys.get("by_id"), dict):
        raise ValueError("Our envoys are not exactly observable in this snapshot")
    return envoys["by_id"]


def resolve_diplomacy_command(state: dict, request: DiplomacyRequest) -> ResolvedDiplomacyCommand:
    request = _again(request)
    relation = _observed(state).get(request.target_country_id)
    if not isinstance(relation, dict) or relation.get("contact") is not True:
        raise ValueError("Select a country we have contact with")
    if request.kind in ENVOY_MISSIONS:
        envoys = _envoys(state)
        mission = ENVOY_MISSIONS[request.kind]
        if any(envoy.get("location") and envoy["location"].get("assignment") == mission
               and envoy["location"].get("id") == request.target_country_id for envoy in envoys.values()):
            raise ValueError("An envoy of ours already has that mission with that country")
        idle = sorted((key for key, envoy in envoys.items() if envoy.get("idle") is True), key=int)
        if not idle:
            raise ValueError("No idle envoy of ours is observed")
        return ResolvedDiplomacyCommand(request, _id(idle[0]))
    elif request.kind in PROPOSALS:
        action, flag = PROPOSALS[request.kind]
        if relation.get("communications") is not True:
            raise ValueError("Proposals need communications with that country")
        current = relation.get("embassy") if flag == "embassy" else relation.get("pacts", {}).get(flag)
        if current is not False:
            raise ValueError("That agreement already exists or is not exactly observable")
        if not isinstance(relation.get("our_proposals"), list):
            raise ValueError("Pending proposals to that country are not exactly observable")
        if any(entry.get("action") == action for entry in relation["our_proposals"]):
            raise ValueError("That proposal is already pending; wait for the recipient's answer")
    elif request.kind in _FLAG_ACTIONS:
        flag, before, _, _ = _FLAG_ACTIONS[request.kind]
        if relation.get(flag) is not before:
            raise ValueError(_ALREADY[request.kind])
    elif relation.get("their_side", {}).get("known") is not True:
        raise ValueError("The target's opinion of us is not exactly observable")
    return ResolvedDiplomacyCommand(request)


def _leader(command: ResolvedDiplomacyCommand) -> str | None:
    """The envoy an envoy mission assigns; other kinds carry none."""
    if command.request.kind in ENVOY_MISSIONS:
        if not isinstance(command.leader_id, str):
            raise ValueError("An envoy mission needs the observed idle envoy")
        return _id(command.leader_id)
    if command.leader_id is not None:
        raise ValueError("Only envoy missions assign a leader")
    return None


def native_diplomacy_payload(command: ResolvedDiplomacyCommand, token: str) -> dict:
    if type(command) is not ResolvedDiplomacyCommand:
        raise ValueError("A resolved diplomacy command is required")
    request = _again(command.request)
    leader = _leader(command)
    return {"kind": request.kind, "country_id": "0", "token": token, **request.to_dict(),
            **({"leader_id": leader} if leader is not None else {})}


def _without(relation: list, drop: frozenset) -> list:
    return [entry for entry in relation if entry[0] not in drop]


def _envoy_assigned(before: dict, after: dict, request: DiplomacyRequest, leader: str) -> bool:
    first, second = _envoys(before), _envoys(after)
    if set(first) != set(second):
        return False
    changed = [key for key, value in second.items() if first[key] != value]
    if changed != [leader] or first[leader].get("idle") is not True:
        return False
    location = second[leader].get("location") or {}
    return (location.get("assignment") == ENVOY_MISSIONS[request.kind]
            and location.get("id") == request.target_country_id and location.get("type") == "empire")


def proposal_outcome(before: dict, after: dict, command: ResolvedDiplomacyCommand) -> str | None:
    """``accepted`` or ``pending`` for one verified proposal; None for anything else."""
    if type(command) is not ResolvedDiplomacyCommand or command.kind not in PROPOSALS:
        return None
    try:
        request = _again(command.request)
        action, flag = PROPOSALS[request.kind]
        old = _observed(before).get(request.target_country_id)
        new = _observed(after).get(request.target_country_id)
        if not isinstance(old, dict) or not isinstance(new, dict):
            return None

        def has(relation):
            return relation.get("embassy") if flag == "embassy" else relation.get("pacts", {}).get(flag)
        if has(old) is not False:
            return None
        added = [entry for entry in new.get("our_proposals") or [] if entry not in (old.get("our_proposals") or [])]
        removed = [entry for entry in old.get("our_proposals") or [] if entry not in (new.get("our_proposals") or [])]
        if removed:
            return None
        if has(new) is True and not added:
            return "accepted"
        if (has(new) is False and len(added) == 1 and added[0]["action"] == action
                and added[0]["type"] == "propose" and added[0]["recipient"] == request.target_country_id):
            return "pending"
        return None
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def verify_diplomacy_command(before: dict, after: dict, command: ResolvedDiplomacyCommand) -> bool:
    """The controlled change happened on the same paused day; nothing else of ours moved."""
    if type(command) is not ResolvedDiplomacyCommand:
        return False
    try:
        request = _again(command.request)
        leader = _leader(command)
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
        if request.kind in ENVOY_MISSIONS:
            if not _envoy_assigned(before, after, request, leader):
                return False
            ignored = _BOOKKEEPING
        elif request.kind in PROPOSALS:
            outcome = proposal_outcome(before, after, command)
            if outcome is None:
                return False
            flag = PROPOSALS[request.kind][1]
            ignored = _BOOKKEEPING | ({flag} if outcome == "accepted" else set())
        elif request.kind in _FLAG_ACTIONS:
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
        if any(old.get("pacts", {}).get(name) != new.get("pacts", {}).get(name)
               for name in PACT_FLAGS if name not in ignored):
            return False
        if request.kind not in PROPOSALS and old.get("our_proposals") != new.get("our_proposals"):
            return False
        if request.kind not in ENVOY_MISSIONS:
            envoys_before = before.get("diplomacy", {}).get("envoys")
            envoys_after = after.get("diplomacy", {}).get("envoys")
            if envoys_before != envoys_after:
                return False
        return all(second[key] == value for key, value in first.items() if key != target)
    except (ValueError, TypeError, KeyError, AttributeError):
        return False
