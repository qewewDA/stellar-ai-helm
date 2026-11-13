"""Turn a structured goal list into proposed execute_plan steps, offline.

``propose_plan`` never touches the game: it maps each goal to one existing
plan step kind, validates the steps with the same typed request builders the
tools use, and, when supplied, checks them against an already observed state
and the versioned knowledge bundle. It returns a proposal; only
``execute_plan`` can act on it.
"""
from __future__ import annotations

from collections.abc import Callable

from .country_commands import definition_from_record
from .diplomacy_commands import DIPLOMACY_KINDS
from .direct_orders import Order, validate_order
from .extended_native import REQUEST_TYPES, resolve_extended
from .native import (BuildingRequest, DistrictRequest, PauseRequest, ResearchSelection, resolve_building,
                     resolve_district, validate_research)
from .task_plan import _Validate, dispatch_step, normalize_plan

MAX_GOALS = 20

# goal name -> (plan step kind, {accepted goal field: step field})
_PLANET = {"planet": "planet_id", "planet_id": "planet_id"}
_FLEET = {"fleet": "fleet_id", "fleet_id": "fleet_id"}
_ZONE = {"zone": "zone_id", "zone_id": "zone_id"}
_BUILDING = {"building": "building_id", "building_id": "building_id"}
_STARBASE = {"starbase": "starbase_id", "starbase_id": "starbase_id", "design": "design_id", "design_id": "design_id"}
_TARGET = {"target": "target_country_id", "country": "target_country_id", "target_country_id": "target_country_id"}
_GOALS: dict[str, tuple[str, dict[str, str]]] = {
    "research": ("select_research", {"area": "area", "technology": "technology_id", "tech": "technology_id",
                                     "technology_id": "technology_id"}),
    "build_district": ("queue_district", {**_PLANET, "district": "district_id", "district_id": "district_id"}),
    "build_building": ("queue_building", {**_PLANET, **_ZONE, **_BUILDING}),
    "build_ship": ("queue_ship", _STARBASE),
    "build_colony_ship": ("queue_colony_ship", {**_STARBASE, "species": "species_id", "species_id": "species_id"}),
    "upgrade_building": ("upgrade_building", {**_PLANET, **_ZONE, **_BUILDING, "to": "target_building_id",
                                              "target_building_id": "target_building_id"}),
    "demolish_building": ("demolish_building", {**_PLANET, **_ZONE, **_BUILDING}),
    "convert_zone": ("convert_zone", {**_PLANET, **_ZONE, "to": "target_zone_id", "target_zone_id": "target_zone_id"}),
    "cancel_construction": ("cancel_construction", {**_PLANET, "item": "item_id", "item_id": "item_id"}),
    "survey": ("survey_planet", {**_FLEET, **_PLANET}),
    "colonize": ("colonize_planet", {**_FLEET, **_PLANET}),
    "build_station": ("build_station", {**_FLEET, **_PLANET, "station": "station_type", "station_type": "station_type"}),
    "move_fleet": ("fleet_move", {**_FLEET, "to_planet": "planet_id", **_PLANET}),
    "orbit": ("fleet_orbit", {**_FLEET, **_PLANET}),
    "wait": ("fleet_wait", {**_FLEET, "days": "days"}),
    "stop_fleet": ("fleet_stop", dict(_FLEET)),
    "fleet_stance": ("set_fleet_stance", {**_FLEET, "stance": "stance"}),
    "reorder_queue": ("move_queue_item", {"item": "item_id", "item_id": "item_id", "direction": "direction"}),
    "defense_platform": ("queue_defense_platform", _STARBASE),
    "event_option": ("choose_event_option", {"event": "player_event_id", "player_event_id": "player_event_id",
                                             "option": "option_index", "option_index": "option_index"}),
    "set_policy": ("set_policy", {"policy": "policy_id", "policy_id": "policy_id", "option": "option_id",
                                  "option_id": "option_id"}),
    "activate_edict": ("activate_edict", {"edict": "edict_id", "edict_id": "edict_id"}),
    "deactivate_edict": ("deactivate_edict", {"edict": "edict_id", "edict_id": "edict_id"}),
    "pause": ("set_paused", {}),
    **{kind: (kind, dict(_TARGET)) for kind in sorted(DIPLOMACY_KINDS)},
}
# Step kind names are accepted as goal names too, with exact step fields.
_STEP_FIELDS = {kind: sorted(set(fields.values())) for kind, fields in _GOALS.values()}
GOAL_NAMES = tuple(sorted(set(_GOALS) | set(_STEP_FIELDS)))


def _goal_step(goal) -> dict:
    if not isinstance(goal, dict):
        raise ValueError("Each goal must be an object with a 'goal' name")
    name = goal.get("goal")
    if not isinstance(name, str) or name not in GOAL_NAMES:
        raise ValueError("Unknown goal; use one of: " + ", ".join(GOAL_NAMES))
    kind, aliases = _GOALS[name] if name in _GOALS else (name, {field: field for field in _STEP_FIELDS[name]})
    step = {"kind": kind}
    for key, value in goal.items():
        if key == "goal":
            continue
        field = aliases.get(key)
        if field is None:
            raise ValueError(f"Goal '{name}' has no field '{key}'")
        if field in step:
            raise ValueError(f"Goal '{name}' sets '{field}' twice")
        step[field] = value
    if kind == "set_paused":
        step["paused"] = True
    return step


def _definitions(knowledge) -> Callable:
    def lookup(kind, category, object_id):
        if knowledge is None:
            return None
        return definition_from_record(kind, knowledge.lookup(category, object_id, ["option", "resources"]))
    return lookup


def _knowledge_problems(step: dict, knowledge) -> list[str]:
    checks = {
        "select_research": [("technology", step.get("technology_id"))],
        "queue_district": [("districts", step.get("district_id"))],
        "queue_building": [("buildings", step.get("building_id"))],
        "upgrade_building": [("buildings", step.get("target_building_id"))],
        "convert_zone": [("zones", step.get("target_zone_id"))],
    }.get(step["kind"], [])
    problems = []
    for category, object_id in checks:
        record = knowledge.lookup(category, object_id)
        if record.get("status") != "found":
            problems.append(f"{category}:{object_id} is {record.get('status', 'unknown')} in versioned knowledge")
    if step["kind"] in {"set_policy", "activate_edict"}:
        category, object_id = (("policies", step["policy_id"]) if step["kind"] == "set_policy"
                               else ("edicts", step["edict_id"]))
        definition = definition_from_record(step["kind"], knowledge.lookup(category, object_id, ["option", "resources"]))
        if step["kind"] == "set_policy" and step["option_id"] not in definition.options:
            problems.append(f"{step['option_id']} is not an option of {step['policy_id']}")
    return problems


def _observation_problems(request, state: dict) -> None:
    if isinstance(request, REQUEST_TYPES):
        resolve_extended(state, request)
    elif isinstance(request, ResearchSelection):
        validate_research(state, request)
    elif isinstance(request, DistrictRequest):
        resolve_district(state, request)
    elif isinstance(request, BuildingRequest):
        resolve_building(state, request)
    elif isinstance(request, Order):
        validate_order(state, request)
    elif not isinstance(request, PauseRequest):
        raise ValueError("No observation check exists for this step")


def propose_plan(goals, *, state: dict | None = None, snapshot: str | None = None, knowledge=None) -> dict:
    """Map goals to execute_plan steps and report every check; never acts on the game."""
    if not isinstance(goals, list) or not 1 <= len(goals) <= MAX_GOALS:
        raise ValueError(f"Give 1..{MAX_GOALS} goals")
    steps, checks = [], []
    for index, goal in enumerate(goals):
        check = {"index": index, "goal": goal.get("goal") if isinstance(goal, dict) else None, "problems": []}
        checks.append(check)
        try:
            step = _goal_step(goal)
            check["kind"] = step["kind"]
            normalize_plan([step])
            check["step"] = step
            check["schema"] = "ok"
        except ValueError as error:
            check.update(schema="error", observation="skipped", knowledge="skipped")
            check["problems"].append(_message(error))
            continue
        steps.append(step)
        request = None
        if knowledge is None:
            check["knowledge"] = "skipped"
        else:
            try:
                problems = _knowledge_problems(step, knowledge)
                check["knowledge"] = "error" if problems else "ok"
                check["problems"] += problems
            except ValueError as error:
                check["knowledge"] = "error"
                check["problems"].append(_message(error))
        if state is None:
            check["observation"] = "skipped"
            continue
        try:
            request = dispatch_step(_Validate(), step, _definitions(knowledge))
            _observation_problems(request, state)
            check["observation"] = "ok"
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            check["observation"] = "error"
            check["problems"].append(_message(error))
    plan_problems = []
    plan_hash = None
    if steps and len(steps) == len(goals):
        try:
            steps, plan_hash = normalize_plan(steps, snapshot)
        except ValueError as error:
            plan_problems.append(_message(error))
    ok = (not plan_problems and len(steps) == len(goals)
          and all(check["problems"] == [] for check in checks))
    if any(step["kind"] in {"set_policy", "activate_edict"} for step in steps) and knowledge is None:
        plan_problems.append("Policy and edict steps need versioned knowledge; execute_plan will block them without it")
        ok = False
    return {
        "status": "proposed" if ok else "needs_changes",
        "side_effects": "none",
        "steps": steps,
        "checks": checks,
        "plan_problems": plan_problems,
        "expected_snapshot": snapshot if state is not None else None,
        "plan_hash": plan_hash,
        "observation_checked": state is not None,
        "knowledge_checked": knowledge is not None,
        "next": ("Call execute_plan(request_id, steps, expected_snapshot) to act; nothing was sent"
                 if ok else "Fix the reported problems; nothing was sent"),
        "notes": ["execute_plan needs a verified pause; add the 'pause' goal first only when the game is not "
                  "already paused (each step reads a fresh dump)"]
        if steps and steps[0]["kind"] != "set_paused" else [],
    }


def _message(error: Exception) -> str:
    details = getattr(error, "errors", None)
    if callable(details):
        try:
            text = "; ".join(".".join(str(part) for part in item.get("loc", ())[-1:]) + ": " + item.get("msg", "")
                             for item in details())
        except (TypeError, AttributeError):
            text = str(error)
    else:
        text = str(error).strip() or type(error).__name__
    return text if len(text) <= 300 else text[:297] + "..."
