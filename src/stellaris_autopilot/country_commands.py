"""Typed empire policy and edict commands for the pinned 4.5.2 build.

These helpers observe snapshots, prepare typed requests and verify outcomes.
They never attach to a process or execute native code. The game's normal
command validator stays authoritative; these checks only reject requests the
snapshot already proves illegal, before anything is submitted. Submission is
never proof of success: verification needs the exact changed entry, the
expected payment and otherwise unchanged empire choices and stockpiles.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re

from .pdx import PDXNode


KINDS = frozenset({"set_policy", "activate_edict", "deactivate_edict"})
# Vanilla 4.5.2 defines POLICY_YEARS = 10 (read by the cooldown check at
# 0x6d7d10 from 0x3115674). The native validator remains authoritative.
POLICY_YEARS = 10
_KEY = re.compile(r"[a-z][a-z0-9_]{0,119}\Z", re.ASCII)
_DATE = re.compile(r"(-?\d{1,5})\.(\d{2})\.(\d{2})\Z", re.ASCII)
_UNKNOWN = {"known": False, "policies": {}, "edicts": {}}


def _key(value, label: str) -> str:
    if not isinstance(value, str) or _KEY.fullmatch(value) is None:
        raise ValueError(f"{label} must be a bounded lowercase ASCII data identifier")
    return value


def _date(value, *, sentinel: bool = False) -> tuple[int, int, int] | None:
    """A game date; negative years are accepted only where a sentinel is expected."""
    match = _DATE.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        return None
    year, month, day = (int(part) for part in match.groups())
    if year < 0 and not sentinel:
        return None
    return (year, month, day) if 1 <= month <= 12 and 1 <= day <= 30 else None


def _fields(entry, allowed: set[str], required: set[str]) -> dict | None:
    if not isinstance(entry, PDXNode):
        return None
    seen = {}
    for key, value in entry.entries:
        if key is None or key not in allowed or key in seen or isinstance(value, PDXNode):
            return None
        seen[key] = value
    return seen if required <= set(seen) else None


def observe_country_controls(country: PDXNode | None) -> dict:
    """Read active policies and edicts of one country.

    The 4.5.2 writer (0x6e1d90) emits ``active_policies`` and ``edicts`` only
    when their arrays are non-empty, so a missing ``edicts`` block is read as
    no active edict only when the same country block proves it was serialized
    by containing its policy list. Any duplicate or unexpected shape is unknown.
    """
    if not isinstance(country, PDXNode):
        return dict(_UNKNOWN)
    policy_blocks = country.get_all("active_policies")
    edict_blocks = country.get_all("edicts")
    if len(policy_blocks) != 1 or not isinstance(policy_blocks[0], PDXNode) or len(edict_blocks) > 1:
        return dict(_UNKNOWN)
    policies = {}
    for key, entry in policy_blocks[0].entries:
        fields = _fields(entry, {"policy", "selected", "date"}, {"policy", "selected"}) if key is None else None
        if (fields is None or not isinstance(fields["policy"], str) or not isinstance(fields["selected"], str)
                or _KEY.fullmatch(fields["policy"]) is None or _KEY.fullmatch(fields["selected"]) is None
                or fields["policy"] in policies or ("date" in fields and _date(fields["date"]) is None)):
            return dict(_UNKNOWN)
        policies[fields["policy"]] = {"selected": fields["selected"], "date": fields.get("date")}
    edicts = {}
    if edict_blocks:
        if not isinstance(edict_blocks[0], PDXNode) or not edict_blocks[0].entries:
            return dict(_UNKNOWN)
        for key, entry in edict_blocks[0].entries:
            fields = (_fields(entry, {"edict", "date", "perpetual", "start_date"}, {"edict", "date", "start_date"})
                      if key is None else None)
            if (fields is None or not isinstance(fields["edict"], str) or _KEY.fullmatch(fields["edict"]) is None
                    or fields["edict"] in edicts or fields.get("perpetual", "yes") != "yes"
                    # Perpetual (length -1) edicts carry a negative sentinel end
                    # date, e.g. date="-5070.07.21" perpetual=yes (4.5.2 dump).
                    or _date(fields["date"], sentinel="perpetual" in fields) is None
                    or _date(fields["start_date"]) is None):
                return dict(_UNKNOWN)
            edicts[fields["edict"]] = {"date": fields["date"], "start_date": fields["start_date"],
                                       "perpetual": "perpetual" in fields}
    return {"known": True, "policies": policies, "edicts": edicts}


@dataclass(frozen=True)
class CountryDefinition:
    """Facts from the versioned knowledge bundle; never supplied by the model."""
    options: tuple[str, ...] | None = None
    base_cost: tuple[tuple[str, float], ...] | None = None
    scaled: bool = False


def definition_from_record(kind: str, record: dict) -> CountryDefinition:
    """Extract the exact option list or base edict cost from a knowledge lookup."""
    if not isinstance(record, dict) or record.get("status") != "found":
        raise ValueError("The definition was not found unambiguously in versioned game knowledge")
    definition = record.get("definition")
    if not isinstance(definition, dict):
        raise ValueError("The knowledge record has no definition")
    if kind == "set_policy":
        options = definition.get("option")
        options = [options] if isinstance(options, dict) else options
        names = tuple(item.get("name") for item in options) if isinstance(options, list) and all(
            isinstance(item, dict) for item in options) else ()
        if not names or len(set(names)) != len(names) or not all(
                isinstance(name, str) and _KEY.fullmatch(name) for name in names):
            raise ValueError("The policy option list is not exactly known")
        return CountryDefinition(options=names)
    if kind == "activate_edict":
        resources = definition.get("resources")
        cost = resources.get("cost") if isinstance(resources, dict) else None
        if not isinstance(cost, dict):
            raise ValueError("The edict base cost is not exactly known")
        multiplier = cost.get("multiplier")
        values = {key: value for key, value in cost.items() if key != "multiplier"}
        if (not values or multiplier not in (None, "value:edict_size_effect")
                or not all(_KEY.fullmatch(key) and type(value) in (int, float) and math.isfinite(value)
                           and value > 0 for key, value in values.items())):
            raise ValueError("The edict base cost is not a fixed positive resource cost")
        return CountryDefinition(base_cost=tuple(sorted((key, float(value)) for key, value in values.items())),
                                 scaled=multiplier is not None)
    if kind == "deactivate_edict":
        return CountryDefinition()
    raise ValueError("Unsupported country command")


@dataclass(frozen=True)
class CountryRequest:
    kind: str
    policy: str | None = None
    option: str | None = None
    edict: str | None = None
    definition: CountryDefinition | None = None

    def to_dict(self) -> dict:
        if self.kind == "set_policy":
            return {"policy": self.policy, "option": self.option}
        return {"edict": self.edict}


def country_request(kind: str, definition: CountryDefinition | None = None, **payload) -> CountryRequest:
    if definition is not None and type(definition) is not CountryDefinition:
        raise ValueError("Definition facts must come from versioned knowledge")
    if kind == "set_policy" and set(payload) == {"policy", "option"}:
        return CountryRequest(kind, policy=_key(payload["policy"], "Policy"),
                              option=_key(payload["option"], "Policy option"), definition=definition)
    if kind in {"activate_edict", "deactivate_edict"} and set(payload) == {"edict"}:
        return CountryRequest(kind, edict=_key(payload["edict"], "Edict"), definition=definition)
    raise ValueError("Unsupported country command or unexpected fields")


@dataclass(frozen=True)
class ResolvedCountryCommand:
    request: CountryRequest
    current_option: str | None = None

    @property
    def kind(self) -> str:
        return self.request.kind

    def to_dict(self) -> dict:
        values = self.request.to_dict()
        if self.kind == "set_policy":
            values["current_option"] = self.current_option
        return values


def _request_again(request: CountryRequest) -> CountryRequest:
    if type(request) is not CountryRequest:
        raise ValueError("A typed country request is required")
    return country_request(request.kind, request.definition, **request.to_dict())


def _controls(state: dict) -> dict:
    if state.get("country_id") != "0":
        raise ValueError("Native country commands have only been verified for country 0")
    controls = state.get("country_controls")
    if not isinstance(controls, dict) or controls.get("known") is not True:
        raise ValueError("Active policies and edicts are not exactly observable in this snapshot")
    return controls


def _now(state: dict) -> tuple[int, int, int]:
    now = _date(state.get("date"))
    if now is None:
        raise ValueError("The snapshot date is unobservable")
    return now


def policy_cooldown_elapsed(entry: dict, now: tuple[int, int, int]) -> bool:
    if entry.get("date") is None:
        return True  # The writer omits a zero date: never changed since game start.
    changed = _date(entry["date"])
    if changed is None:
        return False
    return (changed[0] + POLICY_YEARS, changed[1], changed[2]) <= now


def resolve_country_command(state: dict, request: CountryRequest) -> ResolvedCountryCommand:
    request = _request_again(request)
    controls = _controls(state)
    now = _now(state)
    definition = request.definition
    if request.kind == "set_policy":
        if definition is None or definition.options is None or request.option not in definition.options:
            raise ValueError("The option is not a known option of this policy in versioned game knowledge")
        entry = controls["policies"].get(request.policy)
        if entry is None:
            raise ValueError("The policy is not among this empire's observed active policies")
        if entry["selected"] == request.option:
            raise ValueError("The policy option is already selected")
        if not policy_cooldown_elapsed(entry, now):
            raise ValueError("The policy was changed within the last %d years" % POLICY_YEARS)
        return ResolvedCountryCommand(request, current_option=entry["selected"])
    resources = state.get("resources")
    if (not isinstance(resources, dict) or not resources
            or not all(type(value) in (int, float) and math.isfinite(value) for value in resources.values())):
        raise ValueError("Resource stockpiles are unobservable")
    if request.kind == "activate_edict" and (definition is None or not definition.base_cost):
        raise ValueError("Activating an edict needs its exact base cost from versioned game knowledge")
    if request.kind == "activate_edict" and any(key not in resources for key, _ in definition.base_cost):
        raise ValueError("The edict's cost resources are unobservable")
    active = request.edict in controls["edicts"]
    if request.kind == "activate_edict" and active:
        raise ValueError("The edict is already active")
    if request.kind == "deactivate_edict" and not active:
        raise ValueError("The edict is not active")
    return ResolvedCountryCommand(request)


def native_country_payload(command: ResolvedCountryCommand, token: str) -> dict:
    if type(command) is not ResolvedCountryCommand:
        raise ValueError("A resolved country command is required")
    request = _request_again(command.request)
    if request.kind == "set_policy":
        _key(command.current_option, "Current policy option")
        if command.current_option == request.option:
            raise ValueError("The policy option is already selected")
    elif command.current_option is not None:
        raise ValueError("Edict commands carry no policy option")
    return {"kind": request.kind, "country_id": "0", "token": token, **command.to_dict()}


def _stockpiles(state: dict) -> dict | None:
    resources = state.get("resources")
    if (not isinstance(resources, dict) or not resources
            or not all(type(value) in (int, float) and math.isfinite(value) for value in resources.values())):
        return None
    return resources


def _same_choices(before: dict, after: dict) -> bool:
    keys = ("government", "ethos", "traditions", "ascension_perks")
    first, second = before.get("empire_build"), after.get("empire_build")
    return (isinstance(first, dict) and isinstance(second, dict)
            and all(first.get(key) == second.get(key) for key in keys))


def edict_payment(before: dict, after: dict) -> dict | None:
    """Resources actually spent, if only decreases occurred (no gain, no new key)."""
    first, second = _stockpiles(before), _stockpiles(after)
    if first is None or second is None or set(first) != set(second):
        return None
    spent = {}
    for key, value in first.items():
        delta = value - second[key]
        if abs(delta) <= 1e-6:
            continue
        if delta < 0:
            return None
        spent[key] = round(delta, 6)
    return spent


def edict_cost_matches(spent: dict, definition: CountryDefinition | None) -> bool:
    """Paid exactly the base cost times one common positive size multiplier."""
    if definition is None or not definition.base_cost or not spent:
        return False
    base = dict(definition.base_cost)
    if set(spent) != set(base):
        return False
    ratios = [spent[key] / base[key] for key in base]
    ratio = ratios[0]
    if any(abs(other - ratio) > 1e-4 * max(1.0, ratio) for other in ratios):
        return False
    if not definition.scaled:
        return abs(ratio - 1.0) <= 1e-4
    # value:edict_size_effect is runtime-scaled (observed 0.5 in a 4.5.2 start),
    # so any positive factor is accepted when it is shared by every cost key.
    return 1e-4 < ratio <= 100.0


def verify_country_command(before: dict, after: dict, command: ResolvedCountryCommand) -> bool:
    """Require exactly one intended change on the same (paused) game day.

    Policies cost nothing in 4.5.2 (no cost block, no payment call in the
    native setter), so every stockpile must stay exact. Activating an edict
    must spend exactly its known base cost times one positive size multiplier
    shared by every cost key, and nothing else. Deactivation must not refund or charge anything.
    """
    if type(command) is not ResolvedCountryCommand:
        return False
    try:
        request = _request_again(command.request)
        first, second = _controls(before), _controls(after)
        today = _now(before)
        if _now(after) != today or not _same_choices(before, after):
            return False
    except (ValueError, TypeError, KeyError):
        return False
    resources_before, resources_after = _stockpiles(before), _stockpiles(after)
    if resources_before is None or resources_after is None:
        return False
    if request.kind == "set_policy":
        old = first["policies"].get(request.policy)
        new = second["policies"].get(request.policy)
        expected_policies = {**first["policies"], request.policy: {"selected": request.option,
                                                                   "date": before["date"]}}
        return (old is not None and old["selected"] == command.current_option
                and new == expected_policies[request.policy]
                and second["policies"] == expected_policies
                and second["edicts"] == first["edicts"]
                and resources_after == resources_before)
    if second["policies"] != first["policies"]:
        return False
    if request.kind == "activate_edict":
        added = second["edicts"].get(request.edict)
        spent = edict_payment(before, after)
        others = {key: value for key, value in second["edicts"].items() if key != request.edict}
        return (request.edict not in first["edicts"] and added is not None
                and added["start_date"] == before["date"] and others == first["edicts"]
                and spent is not None and edict_cost_matches(spent, request.definition))
    remaining = {key: value for key, value in first["edicts"].items() if key != request.edict}
    return (request.edict in first["edicts"] and second["edicts"] == remaining
            and resources_after == resources_before)
