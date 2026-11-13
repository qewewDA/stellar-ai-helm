import copy

import pytest

from stellaris_autopilot.decision_commands import (
    DecisionDefinition, ResolvedDecisionCommand, decision_definition, decision_outcome, decision_request,
    list_planet_decisions, native_decision_payload, observe_planet_modifiers, resolve_decision_command,
    verify_decision_command,
)
from stellaris_autopilot.extended_native import extended_payload, resolve_extended, verify_extended
from stellaris_autopilot.pdx import loads

FOOD = {"status": "found", "definition": {"owned_planets_only": "yes", "resources": {"category": "decisions",
                                                                                    "cost": {"food": 1000}}}}
DEF = decision_definition(FOOD)


def _state(modifiers=(), food=4698.7, items=None, date="2210.02.05", others=("x",)):
    return {"date": date, "country_id": "0", "metadata": {"campaign_id": "c1"},
            "resources": {"food": food, "energy": 100.0, "physics_research": 5.0},
            "planets_by_id": {"3": {"id": "3", "name": "Earth"}, "9": {"id": "9"}},
            "planet_modifiers": {"known": True, "by_planet": {"3": sorted(modifiers), "9": list(others)}},
            "construction": {"items_by_id": copy.deepcopy(items or {})}}


def _command(planet="3"):
    request = decision_request("enact_planet_decision", DEF, planet_id=planet, decision_id="decision_planet_food_boost")
    return resolve_decision_command(_state(), request)


def test_definition_needs_an_owned_planet_decision_with_a_fixed_cost():
    assert DEF == DecisionDefinition(cost=(("food", 1000.0),))
    assert decision_definition({"status": "found", "definition": {"owned_planets_only": "yes"}}).cost == ()
    with pytest.raises(ValueError, match="own planets"):
        decision_definition({"status": "found", "definition": {"resources": {}}})
    with pytest.raises(ValueError, match="fixed positive"):
        decision_definition({"status": "found", "definition": {"owned_planets_only": "yes",
                                                               "resources": {"cost": {"food": "value:x"}}}})
    with pytest.raises(ValueError, match="not found"):
        decision_definition({"status": "not_found"})


def test_request_resolution_and_payload_are_exact():
    with pytest.raises(ValueError, match="versioned knowledge"):
        decision_request("enact_planet_decision", None, planet_id="3", decision_id="decision_x")
    with pytest.raises(ValueError, match="Invalid decision"):
        decision_request("enact_planet_decision", DEF, planet_id="3", decision_id="bad key")
    request = decision_request("enact_planet_decision", DEF, planet_id="3", decision_id="decision_planet_food_boost")
    with pytest.raises(ValueError, match="our observed planets"):
        resolve_decision_command(_state(), decision_request("enact_planet_decision", DEF, planet_id="4",
                                                            decision_id="decision_planet_food_boost"))
    with pytest.raises(ValueError, match="cannot pay"):
        resolve_decision_command(_state(food=10.0), request)
    command = resolve_extended(_state(), request)
    assert extended_payload(command, "a" * 32) == native_decision_payload(command, "a" * 32) == {
        "kind": "enact_planet_decision", "country_id": "0", "token": "a" * 32, "planet_id": "3",
        "decision_id": "decision_planet_food_boost"}


def test_immediate_enactment_needs_a_new_modifier_and_only_the_cost_resources_to_fall():
    before, command = _state(), _command()
    after = _state(modifiers=["planet_food_boost"], food=3698.7)
    assert decision_outcome(before, after, command) == "enacted" and verify_extended(before, after, command) is True
    assert verify_decision_command(before, before, command) is False
    assert verify_decision_command(before, _state(food=3698.7), command) is False
    assert verify_decision_command(before, _state(modifiers=["planet_food_boost"]), command) is False
    changed = _state(modifiers=["planet_food_boost"], food=3698.7)
    changed["resources"]["energy"] = 90.0
    assert verify_decision_command(before, changed, command) is False
    # Research stockpiles drift while paused and are ignored.
    drift = _state(modifiers=["planet_food_boost"], food=3698.7)
    drift["resources"]["physics_research"] = 9.0
    assert verify_decision_command(before, drift, command) is True
    assert verify_decision_command(before, _state(modifiers=["planet_food_boost"], food=3698.7, others=()),
                                   command) is False
    assert verify_decision_command(before, _state(modifiers=["planet_food_boost"], food=3698.7,
                                                  date="2210.02.06"), command) is False


def test_queued_enactment_needs_exactly_one_paid_decision_item():
    before, command = _state(), _command()
    item = {"id": "7", "queue_id": "0", "paying_country": "0", "progress": 0, "progress_needed": 30,
            "resources": {"food": 1000}, "buildable_type": "buildable_decision",
            "buildable": {"decision": "decision_planet_food_boost", "carrier": {"type": "planet", "id": 3}}}
    after = _state(food=3698.7, items={"7": item})
    assert decision_outcome(before, after, command) == "queued"
    wrong = copy.deepcopy(item)
    wrong["buildable"]["decision"] = "decision_other"
    assert verify_decision_command(before, _state(food=3698.7, items={"7": wrong}), command) is False
    assert verify_decision_command(before, _state(food=3699.7, items={"7": item}), command) is False
    two = {"7": item, "8": {**item, "id": "8"}}
    assert verify_decision_command(before, _state(food=2698.7, items=two), command) is False


def test_verification_rejects_untyped_commands():
    assert verify_decision_command(_state(), _state(), object()) is False
    assert decision_outcome(_state(), _state(), ResolvedDecisionCommand(object())) is None


def test_planet_modifiers_are_read_from_the_planet_pool():
    root = loads("planets={ planet={ 3={ name=a timed_modifier={ items={ { modifier=\"b_mod\" days=10 } "
                 "{ modifier=\"a_mod\" days=-1 } } } } 9={ name=c } } }")
    assert observe_planet_modifiers(root, ["3", "9"]) == {"known": True, "by_planet": {"3": ["a_mod", "b_mod"],
                                                                                       "9": []}}
    assert observe_planet_modifiers(root, ["4"])["known"] is False
    bad = loads("planets={ planet={ 3={ timed_modifier={ items={ { modifier=5 } } } } } }")
    assert observe_planet_modifiers(bad, ["3"])["known"] is False


def test_listing_marks_decisions_an_observed_modifier_blocks():
    records = [{"id": "decision_planet_food_boost", "names": {"english": "Grow"},
                "definition": {**FOOD["definition"], "allow": {"NOT": {"has_modifier": "planet_food_boost"}}}},
               {"id": "decision_country", "definition": {"resources": {}}}]
    listed = list_planet_decisions(_state(modifiers=["planet_food_boost"]), records)
    assert listed["side_effects"] == "none"
    assert listed["decisions"] == [{"decision_id": "decision_planet_food_boost", "name": "Grow",
                                    "cost": {"food": 1000.0}, "blocked_by_modifier": "planet_food_boost"}]
    assert listed["planets"][0] == {"planet_id": "3", "name": "Earth", "modifiers": ["planet_food_boost"],
                                    "blocked": ["decision_planet_food_boost"]}
