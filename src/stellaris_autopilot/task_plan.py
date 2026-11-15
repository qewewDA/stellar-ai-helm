"""Bounded precise plans: normalize intent before acquiring any live connection."""
from __future__ import annotations

from typing import Annotated, Literal
import hashlib
import json
import re

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, TypeAdapter

from .direct_orders import parse_order
from .native import building_request, district_request, pause_request, research_selection
from .ship_commands import ship_request
from .colony_ships import colony_ship_request
from .planet_commands import planet_request
from .fleet_native import fleet_request
from .control_commands import control_request
from .country_commands import country_request
from .event_commands import event_request
from .diplomacy_commands import DIPLOMACY_KINDS, diplomacy_request
from .decision_commands import decision_request
from .species_rights_commands import species_right_request
from .starbase_commands import starbase_request
from .army_commands import army_request, starbase_army_planets
from .claim_commands import claim_request
from .war_commands import war_request
from .ship_design_commands import ship_design_request
from .ship_commands import _id


class _Step(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ResearchStep(_Step):
    kind: Literal["select_research"]
    area: Literal["physics", "society", "engineering"]
    technology_id: StrictStr


class DistrictStep(_Step):
    kind: Literal["queue_district"]
    planet_id: StrictStr
    district_id: StrictStr


class BuildingStep(_Step):
    kind: Literal["queue_building"]
    planet_id: StrictStr
    zone_id: StrictStr
    building_id: StrictStr


class ShipStep(_Step):
    kind: Literal["queue_ship"]
    starbase_id: StrictStr
    design_id: StrictStr


class ColonyShipStep(_Step):
    kind: Literal["queue_colony_ship"]
    starbase_id: StrictStr
    design_id: StrictStr
    species_id: StrictStr


class UpgradeStep(_Step):
    kind: Literal["upgrade_building"]
    planet_id: StrictStr
    zone_id: StrictStr
    building_id: StrictStr
    target_building_id: StrictStr


class DemolishStep(_Step):
    kind: Literal["demolish_building"]
    planet_id: StrictStr
    zone_id: StrictStr
    building_id: StrictStr


class ConvertStep(_Step):
    kind: Literal["convert_zone"]
    planet_id: StrictStr
    district_id: StrictStr
    slot_index: StrictInt
    target_zone_id: StrictStr


class CancelConstructionStep(_Step):
    kind: Literal["cancel_construction"]
    planet_id: StrictStr
    item_id: StrictStr


class FleetTargetStep(_Step):
    kind: Literal["survey_planet", "colonize_planet"]
    fleet_id: StrictStr
    planet_id: StrictStr


class StationStep(_Step):
    kind: Literal["build_station"]
    fleet_id: StrictStr
    planet_id: StrictStr
    station_type: Literal["mining", "research", "outpost"]


class FleetMoveStep(_Step):
    kind: Literal["fleet_move", "fleet_orbit"]
    fleet_id: StrictStr
    planet_id: StrictStr


class FleetWaitStep(_Step):
    kind: Literal["fleet_wait"]
    fleet_id: StrictStr
    days: StrictInt


class FleetStopStep(_Step):
    kind: Literal["fleet_stop"]
    fleet_id: StrictStr


class FleetStanceStep(_Step):
    kind: Literal["set_fleet_stance"]
    fleet_id: StrictStr
    stance: Literal["passive", "aggressive", "evasive"]


class QueueMoveStep(_Step):
    kind: Literal["move_queue_item"]
    item_id: StrictStr
    direction: Literal["up", "down"]


class DefensePlatformStep(_Step):
    kind: Literal["queue_defense_platform"]
    starbase_id: StrictStr
    design_id: StrictStr


class EventOptionStep(_Step):
    kind: Literal["choose_event_option"]
    player_event_id: StrictStr
    option_index: StrictInt


class BordersStep(_Step):
    kind: Literal["close_borders", "open_borders", "insult", "make_rival", "end_rivalry", "recall_embassy",
                  "harm_relations", "improve_relations", "propose_embassy", "propose_non_aggression_pact",
                  "propose_research_agreement", "propose_commercial_pact", "propose_migration_pact"]
    target_country_id: StrictStr


class PolicyStep(_Step):
    kind: Literal["set_policy"]
    policy_id: StrictStr
    option_id: StrictStr


class EdictStep(_Step):
    kind: Literal["activate_edict", "deactivate_edict"]
    edict_id: StrictStr


class PauseStep(_Step):
    kind: Literal["set_paused"]
    paused: StrictBool


class SpeciesRightStep(_Step):
    kind: Literal["set_species_right"]
    species_id: StrictStr
    right: StrictStr


class PlanetDecisionStep(_Step):
    kind: Literal["enact_planet_decision"]
    planet_id: StrictStr
    decision_id: StrictStr


class StarbaseModuleStep(_Step):
    kind: Literal["queue_starbase_module"]
    starbase_id: StrictStr
    slot: StrictStr
    module_id: StrictStr


class StarbaseBuildingStep(_Step):
    kind: Literal["queue_starbase_building"]
    starbase_id: StrictStr
    slot: StrictStr
    building_id: StrictStr


class RecruitArmyStep(_Step):
    kind: Literal["recruit_army"]
    planet_id: StrictStr
    army_type: StrictStr
    species_id: StrictStr


class RecruitArmiesAtStarbaseStep(_Step):
    kind: Literal["recruit_armies_at_starbase"]
    starbase_id: StrictStr
    army_type: StrictStr
    species_id: StrictStr


class ClaimStep(_Step):
    kind: Literal["add_system_claim"]
    system_id: StrictStr


class DeclareWarStep(_Step):
    kind: Literal["declare_war"]
    target_country_id: StrictStr
    war_goal: StrictStr


class OfferPeaceStep(_Step):
    kind: Literal["offer_peace"]
    target_country_id: StrictStr
    terms: Literal["status_quo", "surrender", "demand_surrender"]
    war_id: StrictStr


class ShipDesignStep(_Step):
    kind: Literal["set_ship_design"]
    ship_id: StrictStr
    design_id: StrictStr


PlanStep = Annotated[
    ResearchStep | DistrictStep | BuildingStep | ShipStep | ColonyShipStep | UpgradeStep
    | DemolishStep | ConvertStep | CancelConstructionStep | FleetTargetStep | StationStep
    | FleetMoveStep | FleetWaitStep | FleetStopStep | FleetStanceStep | QueueMoveStep
    | DefensePlatformStep | EventOptionStep | BordersStep | PolicyStep | EdictStep | PauseStep
    | SpeciesRightStep | PlanetDecisionStep | StarbaseModuleStep | StarbaseBuildingStep
    | RecruitArmyStep | RecruitArmiesAtStarbaseStep | ClaimStep | DeclareWarStep
    | OfferPeaceStep | ShipDesignStep,
    Field(discriminator="kind"),
]
PlanSteps = Annotated[list[PlanStep], Field(min_length=1, max_length=20)]
_PLAN = TypeAdapter(PlanSteps)
_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}\Z", re.ASCII)


def request_identifier(value: str) -> str:
    if not isinstance(value, str) or not _REQUEST_ID.fullmatch(value):
        raise ValueError("request_id must be 1..96 ASCII letters, digits, dot, underscore, colon or hyphen")
    return value


def _no_definitions(kind: str, category: str, object_id: str):
    return {"status": "blocked", "reason": "Versioned knowledge is not attached; exact definition facts are "
                                           "required before a policy or edict step is submitted"}



def _dispatch_recruit_armies_at_starbase(connection, starbase_id, army_type, species_id):
    observed = connection.observe()
    state = observed.get("state") if isinstance(observed, dict) else None
    if not isinstance(state, dict):
        return {"status": "blocked", "reason": "No current observation"}
    try:
        planets = starbase_army_planets(state, starbase_id)
    except ValueError as error:
        return {"status": "blocked", "reason": str(error)}
    if not planets:
        return {"status": "blocked", "reason": "No owned planet with an idle army queue in that starbase's system"}
    receipts = []
    for planet_id in planets:
        result = connection.army_command("recruit_army", True, planet_id=planet_id,
                                         army_type=army_type, species_id=species_id, starbase_id=starbase_id)
        receipts.append({"planet_id": planet_id, "result": result})
        if not isinstance(result, dict) or result.get("status") not in {"command_verified", "command_accepted"}:
            return {"status": "partial" if len(receipts) > 1 else (result or {}).get("status", "blocked"),
                    "planets": planets, "receipts": receipts}
    status = "command_accepted" if any(item["result"].get("status") == "command_accepted" for item in receipts) else "command_verified"
    return {"status": status, "planets": planets, "receipts": receipts}


def dispatch_step(connection, step: dict, definitions=None):
    """Call the existing typed API; also usable with a validation-only target.

    ``definitions(kind, category, object_id)`` returns the versioned definition
    facts the policy/edict tools also require, or a blocked result.
    """
    kind = step["kind"]
    args = {key: value for key, value in step.items() if key != "kind"}
    if kind in {"set_policy", "activate_edict"}:
        category, object_id = ("policies", args["policy_id"]) if kind == "set_policy" else ("edicts", args["edict_id"])
        definition = (definitions or _no_definitions)(kind, category, object_id)
        if isinstance(definition, dict):
            return definition
        if kind == "set_policy":
            return connection.country_command(kind, definition, policy=args["policy_id"], option=args["option_id"])
        return connection.country_command(kind, definition, edict=args["edict_id"])
    if kind == "deactivate_edict":
        return connection.country_command(kind, None, edict=args["edict_id"])
    if kind in {"set_fleet_stance", "move_queue_item", "queue_defense_platform"}:
        return connection.control_command(kind, **args)
    if kind == "choose_event_option":
        return connection.event_command(kind, **args)
    if kind in DIPLOMACY_KINDS:
        return connection.diplomacy_command(kind, **args)
    if kind == "set_species_right":
        species_right_request(kind, True, **args)
        checked = (definitions or _no_definitions)(kind, "species_rights", args["right"])
        if isinstance(checked, dict):
            return checked
        if checked is None:
            return None
        return connection.species_right_command(kind, True, **args)
    if kind == "enact_planet_decision":
        _id(args["planet_id"])
        checked = (definitions or _no_definitions)(kind, "decisions", args["decision_id"])
        if isinstance(checked, dict):
            return checked
        if checked is None:
            return None
        return connection.decision_command(kind, checked, **args)
    if kind in {"queue_starbase_module", "queue_starbase_building"}:
        type_key = "module_id" if kind == "queue_starbase_module" else "building_id"
        category = "starbase_modules" if kind == "queue_starbase_module" else "starbase_buildings"
        starbase_request(kind, True, starbase_id=args["starbase_id"], slot=args["slot"], type_id=args[type_key])
        checked = (definitions or _no_definitions)(kind, category, args[type_key])
        if isinstance(checked, dict):
            return checked
        if checked is None:
            return None
        return connection.starbase_command(kind, True, starbase_id=args["starbase_id"], slot=args["slot"],
                                           type_id=args[type_key])
    if kind == "recruit_army":
        army_request(kind, True, **args)
        checked = (definitions or _no_definitions)(kind, "armies", args["army_type"])
        if isinstance(checked, dict):
            return checked
        if checked is None:
            return None
        return connection.army_command(kind, True, **args)
    if kind == "recruit_armies_at_starbase":
        army_request("recruit_army", True, planet_id="1", army_type=args["army_type"], species_id=args["species_id"])
        _id(args["starbase_id"])
        checked = (definitions or _no_definitions)(kind, "armies", args["army_type"])
        if isinstance(checked, dict):
            return checked
        if checked is None:
            return None
        return _dispatch_recruit_armies_at_starbase(connection, **args)
    if kind == "add_system_claim":
        return connection.claim_command(kind, **args)
    if kind in {"declare_war", "offer_peace"}:
        return connection.war_command(kind, **args)
    if kind == "set_ship_design":
        return connection.ship_design_command(kind, **args)
    if kind.startswith("fleet_"):
        return connection.issue_order(step)
    if kind in {"survey_planet", "colonize_planet", "build_station"}:
        return connection.fleet_command(kind, **args)
    if kind in {"upgrade_building", "demolish_building", "convert_zone", "cancel_construction"}:
        if "target_building_id" in args:
            args["target_building"] = args.pop("target_building_id")
        if "target_zone_id" in args:
            args["target_zone"] = args.pop("target_zone_id")
        return connection.planet_command(kind, **args)
    if kind == "select_research":
        return connection.select_research(args["area"], args["technology_id"])
    if kind == "queue_district":
        return connection.queue_district(args["planet_id"], args["district_id"])
    if kind == "queue_building":
        return connection.queue_building(args["planet_id"], args["zone_id"], args["building_id"])
    if kind == "set_paused":
        return connection.set_paused(args["paused"])
    return getattr(connection, kind)(**args)


class _Validate:
    issue_order = staticmethod(parse_order)
    fleet_command = staticmethod(fleet_request)
    planet_command = staticmethod(planet_request)
    control_command = staticmethod(control_request)
    country_command = staticmethod(country_request)
    event_command = staticmethod(event_request)
    diplomacy_command = staticmethod(diplomacy_request)
    decision_command = staticmethod(decision_request)
    species_right_command = staticmethod(species_right_request)
    starbase_command = staticmethod(starbase_request)
    army_command = staticmethod(army_request)
    claim_command = staticmethod(claim_request)
    war_command = staticmethod(war_request)
    ship_design_command = staticmethod(ship_design_request)
    select_research = staticmethod(research_selection)
    queue_district = staticmethod(district_request)
    queue_building = staticmethod(building_request)
    queue_ship = staticmethod(ship_request)
    queue_colony_ship = staticmethod(colony_ship_request)
    set_paused = staticmethod(pause_request)


def normalize_plan(steps, expected_snapshot: str | None = None) -> tuple[list[dict], str]:
    if (expected_snapshot is not None and (not isinstance(expected_snapshot, str)
            or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", expected_snapshot, re.ASCII))):
        raise ValueError("expected_snapshot must be a bounded snapshot identifier")
    normalized = [step.model_dump() for step in _PLAN.validate_python(steps)]
    fleets = set()
    for index, step in enumerate(normalized):
        dispatch_step(_Validate(), step, definitions=lambda *_: None)
        if step["kind"] == "set_paused" and (index != 0 or step["paused"] is not True):
            raise ValueError("A plan may explicitly pause only as its first step; time advancement is separate")
        # A stance change is not an order; it never conflicts with one.
        fleet = step.get("fleet_id") if step["kind"] != "set_fleet_stance" else None
        if fleet in fleets:
            raise ValueError("This fleet already has an unfinished plan order; use separate natural-completion tasks")
        if fleet is not None and step["kind"] != "fleet_stop":
            fleets.add(fleet)
    canonical = json.dumps({"steps": normalized, "expected_snapshot": expected_snapshot},
                           sort_keys=True, separators=(",", ":"), allow_nan=False)
    return normalized, hashlib.sha256(canonical.encode("utf-8")).hexdigest()
