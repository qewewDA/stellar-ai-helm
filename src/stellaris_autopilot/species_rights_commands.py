"""Species rights of our primary species through the normal command (pinned 4.5.2).

These helpers read snapshots, prepare typed requests and verify outcomes;
they never attach to a process.

Static evidence (4.5.2), see native_species_rights.js:
``set_species_right_command`` (token 0x3832, factory 0x6d29d0) carries the
country (+0x20), the species (+0x24) and a full rights block (+0x28, the
same layout the country's ``standard_species_rights_module`` keeps per
species). Only one category of the observed rights is changed; the game's
validator and executor apply potential/allow, costs and cooldowns.

Saved as ``modules.standard_species_rights_module.primary = { species_index=<id>
citizenship="..." living_standard="..." ... former_*=... last_changed_*=... }``.
Verification needs exactly the requested category changed on the primary
entry, every other current category unchanged and no other entry changed
apart from its ``former_*``/``last_changed_*`` bookkeeping.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from .pdx import PDXNode
from .ship_commands import _id

SPECIES_RIGHT_KINDS = frozenset({"set_species_right"})
# Category -> offset inside the native rights block (writer 0x399920).
CATEGORIES = {
    "citizenship": 0x10, "living_standard": 0x18, "military_service": 0x20, "slavery": 0x28,
    "purge": 0x30, "population_control": 0x38, "colonization_control": 0x40, "migration_control": 0x48,
}
_KEY = re.compile(r"[a-z][a-z0-9_]{0,120}\Z", re.ASCII)
_ENTRIES = ("default", "primary", "built_species")


def category_of(right: str) -> str | None:
    """The category a right key belongs to, by its vanilla prefix."""
    if not isinstance(right, str) or not _KEY.fullmatch(right):
        return None
    matches = [name for name in CATEGORIES if right.startswith(name + "_")]
    return max(matches, key=len) if matches else None


def _entry(node) -> dict | None:
    if not isinstance(node, PDXNode):
        return None
    found = {}
    for key, value in node.entries:
        if key is None or isinstance(value, PDXNode) or isinstance(value, bool) or value is None:
            return None
        if key in found:
            return None
        found[key] = str(value)
    if "species_index" not in found or not all(isinstance(found.get(name), str) for name in CATEGORIES):
        return None
    return found


def observe_species_rights(root: PDXNode, country_id: str = "0") -> dict:
    """Our species rights module: entry name -> {species_index, categories, bookkeeping}."""
    result = {"known": False, "entries": {}}
    try:
        countries = root.get_all("country")
        own = countries[0].get_all(country_id) if len(countries) == 1 and isinstance(countries[0], PDXNode) else []
        modules = own[0].get_all("modules") if len(own) == 1 and isinstance(own[0], PDXNode) else []
        rights = (modules[0].get_all("standard_species_rights_module")
                  if len(modules) == 1 and isinstance(modules[0], PDXNode) else [])
    except AttributeError:
        return result
    if len(rights) != 1 or not isinstance(rights[0], PDXNode) or rights[0].get_all("enabled") != ["yes"]:
        return result
    found = {}
    for name in _ENTRIES:
        nodes = rights[0].get_all(name)
        if not nodes:
            continue
        entry = _entry(nodes[0]) if len(nodes) == 1 else None
        if entry is None:
            return result
        found[name] = entry
    if "primary" not in found:
        return result
    result.update(known=True, entries=found)
    return result


@dataclass(frozen=True)
class SpeciesRightRequest:
    kind: str
    species_id: str
    right: str

    @property
    def category(self) -> str:
        return category_of(self.right)

    def to_dict(self) -> dict:
        return {"species_id": self.species_id, "category": self.category, "right": self.right}


def species_right_request(kind: str, definition=None, *, species_id, right) -> SpeciesRightRequest:
    if kind not in SPECIES_RIGHT_KINDS:
        raise ValueError("Unsupported species rights command")
    if definition is not True:
        raise ValueError("The right must be found in versioned game knowledge")
    if category_of(right) is None:
        raise ValueError("Unknown species right category")
    return SpeciesRightRequest(kind, _id(species_id), right)


@dataclass(frozen=True)
class ResolvedSpeciesRightCommand:
    request: SpeciesRightRequest
    current_right: str

    @property
    def kind(self) -> str:
        return self.request.kind

    def to_dict(self) -> dict:
        return {**self.request.to_dict(), "current_right": self.current_right}


def _again(request: SpeciesRightRequest) -> SpeciesRightRequest:
    if type(request) is not SpeciesRightRequest:
        raise ValueError("A typed species rights request is required")
    return species_right_request(request.kind, True, species_id=request.species_id, right=request.right)


def _rights(state: dict) -> dict:
    observed = state.get("species_rights")
    if not isinstance(observed, dict) or observed.get("known") is not True:
        raise ValueError("Species rights are not exactly observable in this snapshot")
    return observed["entries"]


def resolve_species_right(state: dict, request: SpeciesRightRequest) -> ResolvedSpeciesRightCommand:
    request = _again(request)
    if state.get("country_id") != "0":
        raise ValueError("Species rights have only been verified for country 0")
    primary = _rights(state)["primary"]
    if primary["species_index"] != request.species_id:
        raise ValueError("Only our primary species' rights are supported")
    current = primary[request.category]
    if current == request.right:
        raise ValueError("That right is already selected")
    return ResolvedSpeciesRightCommand(request, current)


def native_species_right_payload(command: ResolvedSpeciesRightCommand, token: str) -> dict:
    if type(command) is not ResolvedSpeciesRightCommand:
        raise ValueError("A resolved species rights command is required")
    request = _again(command.request)
    if category_of(command.current_right) != request.category:
        raise ValueError("The current right does not belong to the requested category")
    return {"kind": request.kind, "country_id": "0", "token": token, **request.to_dict(),
            "current_right": command.current_right}


def _bookkeeping(key: str) -> bool:
    return key.startswith(("former_", "last_changed_"))


def verify_species_right(before: dict, after: dict, command: ResolvedSpeciesRightCommand) -> bool:
    if type(command) is not ResolvedSpeciesRightCommand:
        return False
    try:
        request = _again(command.request)
        if (after.get("country_id") != "0" or after.get("date") != before.get("date")
                or before.get("metadata", {}).get("campaign_id") != after.get("metadata", {}).get("campaign_id")):
            return False
        first, second = _rights(before), _rights(after)
        if set(first) != set(second):
            return False
        for name, old in first.items():
            new = second[name]
            if set(old) - {k for k in old if _bookkeeping(k)} != set(new) - {k for k in new if _bookkeeping(k)}:
                return False
            for key, value in old.items():
                if _bookkeeping(key):
                    continue
                expected = request.right if (name == "primary" and key == request.category) else value
                if name == "primary" and key == request.category and value != command.current_right:
                    return False
                if new.get(key) != expected:
                    return False
        return True
    except (ValueError, TypeError, KeyError, AttributeError):
        return False
