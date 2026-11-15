"""System claims through the normal add_system_claim_command (pinned 4.5.2).

These helpers read snapshots, prepare typed requests and verify outcomes;
they never attach to a process.

Static evidence (4.5.2), see native_claim.js: the command carries our country,
the target system, a claim count (the UI always sends 1) and the current date.
Claims are saved per system as
``galactic_object.<id>.claims = { { owner=<country> date="Y.M.D" claims=<n> } }``
(writer 0x888c30). The game's validator decides claimability and influence cost.
"""
from __future__ import annotations

from dataclasses import dataclass

from .control_commands import _stockpiles, _unchanged
from .pdx import PDXNode
from .ship_commands import _id, _one

CLAIM_KIND = "add_system_claim"


def _claim_entries(node) -> list | None:
    if node is None:
        return []
    if not isinstance(node, PDXNode):
        return None
    entries = []
    for key, value in node.entries:
        if not isinstance(value, PDXNode):
            return None
        entries.append(value)
    return entries


def observe_claims(root: PDXNode, country_id: str) -> dict:
    """Our claim count (and date) on every system that carries one."""
    result = {"known": False, "by_system": {}}
    systems = _one(root, "galactic_object")
    if not isinstance(systems, PDXNode):
        return result
    found = {}
    for system_id, system in systems.entries:
        if not isinstance(system, PDXNode):
            continue
        blocks = system.get_all("claims")
        if not blocks:
            continue
        entries = _claim_entries(blocks[0]) if len(blocks) == 1 else None
        if entries is None:
            return result
        for entry in entries:
            owner = _one(entry, "owner")
            if str(owner) != country_id:
                continue
            count = _one(entry, "claims")
            if type(count) is not int or count < 0 or system_id in found:
                return result
            found[system_id] = {"claims": count, "date": _one(entry, "date")}
    result.update(known=True, by_system=found)
    return result


@dataclass(frozen=True)
class ClaimRequest:
    system_id: str
    kind: str = CLAIM_KIND

    def to_dict(self) -> dict:
        return {"system_id": self.system_id}


def claim_request(kind: str, *, system_id) -> ClaimRequest:
    if kind != CLAIM_KIND:
        raise ValueError("Unsupported claim command")
    return ClaimRequest(_id(system_id))


@dataclass(frozen=True)
class ResolvedClaimCommand:
    request: ClaimRequest
    current_claims: int

    @property
    def kind(self) -> str:
        return self.request.kind

    def to_dict(self) -> dict:
        # Exactly the fields the native worker echoes back; the worker result is
        # matched against this dict, so observation-only facts stay out of it.
        return self.request.to_dict()


def _claims(state: dict) -> dict:
    observed = state.get("claims")
    if not isinstance(observed, dict) or observed.get("known") is not True or not isinstance(observed.get("by_system"), dict):
        raise ValueError("Our system claims are not exactly observable in this snapshot")
    return observed["by_system"]


def resolve_claim_command(state: dict, request: ClaimRequest) -> ResolvedClaimCommand:
    if type(request) is not ClaimRequest:
        raise ValueError("A typed claim request is required")
    request = claim_request(request.kind, system_id=request.system_id)
    if state.get("country_id") != "0":
        raise ValueError("Claims are restricted to country 0")
    entry = _claims(state).get(request.system_id)
    count = entry.get("claims") if isinstance(entry, dict) else 0
    if type(count) is not int or count < 0:
        raise ValueError("Our claim on that system is not exactly observable")
    return ResolvedClaimCommand(request, count)


def native_claim_payload(command: ResolvedClaimCommand, token: str) -> dict:
    if type(command) is not ResolvedClaimCommand:
        raise ValueError("A resolved claim command is required")
    return {"kind": command.kind, "country_id": "0", "token": token, "system_id": _id(command.request.system_id)}


def verify_claim_command(before: dict, after: dict, command: ResolvedClaimCommand) -> bool:
    """Exactly one more claim on exactly that system, paid with influence only, same date."""
    if type(command) is not ResolvedClaimCommand:
        return False
    try:
        if resolve_claim_command(before, command.request) != command:
            return False
        if after.get("date") != before.get("date") or not _unchanged(
                before, after, "country_id", "empire_build", "planets_by_id", "fleets_by_id", "construction"):
            return False
        old, new = _claims(before), _claims(after)
        system_id = command.request.system_id
        entry = new.get(system_id)
        if (not isinstance(entry, dict) or entry.get("claims") != command.current_claims + 1
                or {key: value for key, value in new.items() if key != system_id}
                != {key: value for key, value in old.items() if key != system_id}):
            return False
        first, second = _stockpiles(before), _stockpiles(after)
        if first is None or second is None or set(first) != set(second):
            return False
        if not second.get("influence", 0) < first.get("influence", 0):
            return False
        return all(second[key] == value for key, value in first.items() if key != "influence")
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return False


__all__ = ["CLAIM_KIND", "ClaimRequest", "ResolvedClaimCommand", "observe_claims", "claim_request",
           "resolve_claim_command", "native_claim_payload", "verify_claim_command"]
