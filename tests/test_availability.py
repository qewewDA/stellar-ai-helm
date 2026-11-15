"""Availability is taken from game validator results, not static guesses."""
from stellaris_autopilot.availability import (
    build_candidates, shape_availability, _never, _handle, planet_identities,
)
from stellaris_autopilot.observation_view import apply_observation_view


def _bundle(*records):
    return {"records": {f"{record['category']}:{record['id']}": record for record in records}}


def _decision(decision_id, *, always_no=False, cost=None):
    definition = {"owned_planets_only": "yes", "key": decision_id}
    if always_no:
        definition["potential"] = {"always": "no"}
    if cost is not None:
        definition["resources"] = {"cost": cost}
    else:
        definition["resources"] = {"cost": {"influence": 50}}
    return {"category": "decisions", "id": decision_id, "definition": definition}


def _policy(policy_id, options):
    return {"category": "policies", "id": policy_id,
            "definition": {"option": [{"name": name} for name in options]}}


def _edict(edict_id, *, always_no=False, cost=None):
    definition = {"key": edict_id}
    if always_no:
        definition["potential"] = {"always": "no"}
    if cost is not None:
        definition["resources"] = {"cost": cost}
    return {"category": "edicts", "id": edict_id, "definition": definition}


def test_never_detects_always_no_anywhere():
    assert _never({"always": "no"}) is True
    assert _never({"OR": [{"always": "no"}]}) is True
    assert _never({"has_technology": "tech_x"}) is False


def test_candidates_omit_always_no_and_stay_under_the_limit():
    state = {
        "planets": [{"id": "3", "name": "Earth", "colony": "1", "build_queue": "2"}],
        "country_controls": {"known": True, "policies": {"war_philosophy": {"selected": "unrestricted"}},
                             "edicts": {}},
    }
    bundle = _bundle(
        _decision("decision_ok"),
        _decision("decision_dead", always_no=True),
        _policy("war_philosophy", ["unrestricted", "defensive", "pacifism"]),
        _edict("capacity_subsidies", cost={"energy": 100}),
        _edict("dead_edict", always_no=True),
    )
    candidates, skipped = build_candidates(state, bundle)
    kinds = {(item["kind"], item.get("decision_id") or item.get("option") or item.get("edict")) for item in candidates}
    assert ("enact_planet_decision", "decision_ok") in kinds
    assert ("enact_planet_decision", "decision_dead") not in kinds
    assert ("set_policy", "defensive") in kinds
    assert ("set_policy", "unrestricted") not in kinds  # already selected
    assert ("activate_edict", "capacity_subsidies") in kinds
    assert ("activate_edict", "dead_edict") not in kinds
    assert "truncated" not in skipped


def test_shape_marks_enactable_and_keeps_the_games_failure_reason():
    state = {
        "planets": [{"id": "3", "name": "Earth"}],
        "country_controls": {"known": True,
                             "policies": {"war_philosophy": {"selected": "unrestricted"}},
                             "edicts": {}},
        "planet_modifiers": {"known": True, "by_planet": {"3": ["decision_awe"]}},
    }
    bundle = _bundle(
        _decision("decision_ok", cost={"influence": 50}),
        _decision("decision_poor", cost={"influence": 200}),
        _decision("decision_dead", always_no=True),
        _policy("war_philosophy", ["unrestricted", "defensive"]),
        _edict("map_the_stars", cost={"energy": 200}),
    )
    results = [
        {"kind": "enact_planet_decision", "planet_id": "3", "decision_id": "decision_ok",
         "allowed": True, "enactable": True, "known": True, "reason": None},
        {"kind": "enact_planet_decision", "planet_id": "3", "decision_id": "decision_poor",
         "allowed": False, "enactable": False, "known": True,
         "reason": "Normal native command validation rejected: trigger_no R缺少!Rinfluence|1 200!"},
        {"kind": "enact_planet_decision", "planet_id": "3", "decision_id": "decision_hidden",
         "allowed": False, "enactable": False, "known": True, "error": "precondition",
         "reason": "Database object unavailable"},
        {"kind": "set_policy", "policy": "war_philosophy", "option": "defensive",
         "allowed": False, "enactable": False, "known": True, "reason": "trigger_no"},
        {"kind": "activate_edict", "edict": "map_the_stars",
         "allowed": True, "enactable": True, "known": True, "reason": None},
    ]
    shaped = shape_availability(state, bundle, results, queried=True)
    assert shaped["known"] is True
    planet = shaped["planets"][0]
    ids = {item["decision_id"]: item for item in planet["available_decisions"]}
    assert ids["decision_ok"] == {"decision_id": "decision_ok", "enactable": True, "cost": {"influence": 50}}
    assert ids["decision_poor"]["enactable"] is False
    assert ids["decision_poor"]["reason"] == "trigger_no R缺少!Rinfluence|1 200!"
    assert "decision_dead" not in ids
    assert "decision_hidden" not in ids
    assert planet["active_decisions"]["modifiers"] == ["decision_awe"]
    policies = {item["policy"]: item for item in shaped["policies"]["policies"]}
    options = {item["option"]: item for item in policies["war_philosophy"]["options"]}
    assert options["unrestricted"]["selected"] is True and options["unrestricted"]["enactable"] is False
    assert options["defensive"]["enactable"] is False and options["defensive"]["reason"] == "trigger_no"
    edicts = {item["edict"]: item for item in shaped["edicts"]["activatable"]}
    assert edicts["map_the_stars"]["enactable"] is True
    assert edicts["map_the_stars"]["cost"] == {"energy": 200}


def test_ambiguous_results_are_known_false_not_treated_as_rejected():
    state = {"planets": [{"id": "3"}], "country_controls": {"known": True, "policies": {}, "edicts": {}}}
    bundle = _bundle(_decision("decision_ok"))
    results = [{"kind": "enact_planet_decision", "planet_id": "3", "decision_id": "decision_ok",
                "allowed": False, "known": False, "error": "ambiguous", "reason": None}]
    shaped = shape_availability(state, bundle, results, queried=True)
    assert shaped["planets"][0]["available_decisions_known"] is False
    assert shaped["planets"][0]["available_decisions"] is None


def test_brief_view_folds_validated_decisions_policies_and_edicts():
    observed = {
        "snapshot": "s",
        "state": {
            "date": "2200.12.26",
            "planets": [{"id": "3", "name": "Earth", "districts_known": True, "district_levels": {}}],
            "availability": {
                "known": True,
                "planets": [{
                    "planet_id": "3",
                    "available_decisions_known": True,
                    "available_decisions": [
                        {"decision_id": "decision_ok", "enactable": True, "cost": {"influence": 50}},
                        {"decision_id": "decision_poor", "enactable": False, "reason": "trigger_no", "cost": {"influence": 200}},
                    ],
                    "active_decisions": {"queued": [], "modifiers": ["decision_awe"], "modifiers_known": True},
                }],
                "policies": {"known": True, "policies": [
                    {"policy": "war_philosophy", "selected": "unrestricted",
                     "options": [{"option": "unrestricted", "enactable": False, "selected": True},
                                 {"option": "defensive", "enactable": False, "reason": "trigger_no", "selected": False}]},
                ]},
                "edicts": {"known": True, "active": [],
                           "activatable": [{"edict": "map_the_stars", "enactable": True, "cost": {"energy": 200}}]},
                "species_rights": {"known": True, "entries": [
                    {"entry": "primary", "species_id": "1", "options_known": True,
                     "categories": {"citizenship": {"selected": "citizenship_full_citizenship",
                                                    "options_known": True,
                                                    "options": [{"right": "citizenship_full_citizenship",
                                                                 "enactable": False, "selected": True}]}}}
                ]},
                "war_goals": {"known": True, "by_country": [
                    {"country_id": "5", "war_goals": [
                        {"war_goal": "wg_conquest", "enactable": True},
                        {"war_goal": "wg_humiliation", "enactable": False, "reason": "trigger_no"},
                    ]}
                ]},
            },
        },
    }
    brief = apply_observation_view(observed, "brief")["state"]
    planet = brief["planets"][0]
    assert planet["available_decisions"][0]["enactable"] is True
    assert planet["available_decisions"][1]["reason"] == "trigger_no"
    assert planet["active_decisions"]["modifiers"] == ["decision_awe"]
    assert brief["policies_edicts"]["policies"][0]["options"][1]["reason"] == "trigger_no"
    assert brief["policies_edicts"]["edicts_activatable"][0]["edict"] == "map_the_stars"
    assert brief["species_rights"]["entries"][0]["species_id"] == "1"
    assert brief["availability"]["known"] is True
    assert brief["availability"]["war_goals"]["by_country"][0]["war_goals"] == [
        {"war_goal": "wg_conquest", "enactable": True}
    ]


def test_unqueried_availability_is_marked_known_false():
    state = {"planets": [{"id": "3"}]}
    shaped = shape_availability(state, _bundle(_decision("decision_ok")), None, queried=False)
    assert shaped["known"] is False
    assert shaped["planets"][0]["available_decisions_known"] is False


def test_handle_zero_is_a_valid_colony_and_queue_id():
    assert _handle(0) == "0"
    assert _handle("0") == "0"
    assert _handle(4294967295) is None
    state = {
        "planets": [{"id": 3, "name": "Earth", "colony": 0, "build_queue": 0}],
        "construction": {"queues_by_id": {
            "0": {"type": "planet", "owner": 0, "location": {"type": 2, "id": 3}},
            "1": {"type": "army", "owner": 0, "location": {"type": 2, "id": 3}},
        }},
    }
    assert planet_identities(state) == [
        {"planet_id": "3", "name": "Earth", "colony_id": "0", "queue_id": "0"}]


def test_planet_queue_falls_back_to_the_unique_planet_queue():
    state = {
        "planets_by_id": {"3": {"id": "3", "name": "Earth", "colony": 0}},
        "construction": {"queues_by_id": {
            "0": {"type": "planet", "owner": "0", "location": {"type": 2, "id": "3"}},
        }},
    }
    assert planet_identities(state)[0]["queue_id"] == "0"
    assert planet_identities(state)[0]["colony_id"] == "0"


def test_construction_candidates_include_zero_handles_and_zones():
    state = {
        "planets": [{"id": "3", "name": "Earth", "colony": 0, "build_queue": 0,
                     "zones_by_id": {"0": {"id": 0}, "1": {"id": 1}}}],
        "construction": {"queues_by_id": {
            "0": {"type": "planet", "owner": 0, "location": {"type": 2, "id": 3}},
        }},
    }
    bundle = _bundle(
        {"category": "districts", "id": "district_city",
         "definition": {"key": "district_city", "resources": {"cost": {"minerals": 300}}}},
        {"category": "buildings", "id": "building_capital",
         "definition": {"key": "building_capital"}},
    )
    candidates, skipped = build_candidates(state, bundle)
    districts = [item for item in candidates if item["kind"] == "queue_district"]
    buildings = [item for item in candidates if item["kind"] == "queue_building"]
    assert districts and districts[0]["colony_id"] == "0" and districts[0]["queue_id"] == "0"
    assert {item["zone_id"] for item in buildings} == {"0", "1"}
    assert "construction" not in skipped


def test_shape_construction_keeps_enactable_reason_and_cost():
    state = {
        "planets": [{"id": "3", "name": "Earth", "colony": 0, "build_queue": 0,
                     "zones_by_id": {"0": {"id": "0"}}}],
        "construction": {"queues_by_id": {
            "0": {"type": "planet", "owner": 0, "location": {"type": 2, "id": 3}},
        }},
    }
    bundle = _bundle(
        {"category": "districts", "id": "district_city",
         "definition": {"key": "district_city", "resources": {"cost": {"minerals": 300}}}},
        {"category": "buildings", "id": "building_capital",
         "definition": {"key": "building_capital", "resources": {"cost": {"minerals": 400}}}},
    )
    results = [
        {"kind": "queue_district", "planet_id": "3", "district": "district_city",
         "allowed": True, "enactable": True, "known": True},
        {"kind": "queue_building", "planet_id": "3", "zone_id": "0", "building": "building_capital",
         "allowed": False, "enactable": False, "known": True, "reason": "trigger_no"},
    ]
    shaped = shape_availability(state, bundle, results, queried=True)
    planet = shaped["construction"]["planets"][0]
    assert planet["available_districts"][0] == {
        "district_id": "district_city", "enactable": True, "cost": {"minerals": 300}}
    building = planet["zones"][0]["available_buildings"][0]
    assert building["building_id"] == "building_capital"
    assert building["enactable"] is False and building["reason"] == "trigger_no"
    observed = {"snapshot": "s", "state": {"date": "2200.01.01", "planets": state["planets"],
                                           "availability": shaped}}
    brief = apply_observation_view(observed, "brief")["state"]
    assert brief["planets"][0]["available_districts"][0]["district_id"] == "district_city"
    assert brief["planets"][0]["available_buildings"][0]["available_buildings"][0]["reason"] == "trigger_no"
