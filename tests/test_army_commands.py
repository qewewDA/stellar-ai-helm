"""Army recruitment: observation, resolution and verification (planet and starbase paths)."""
import copy

import pytest

from stellaris_autopilot import saves
from stellaris_autopilot.army_commands import (
    army_request, native_army_payload, observe_army_queues, resolve_army_command, starbase_army_planets,
    verify_army_command)
from stellaris_autopilot import extended_native

DUMP = """
planets={ planet={
    3={ owner=0 colony=0 build_queue=0 coordinate={ x=1 y=2 origin=92 } }
    4={ owner=0 colony=5 build_queue=6 coordinate={ x=1 y=2 origin=92 } }
    9={ owner=7 colony=8 coordinate={ origin=12 } }
} }
colony={
    0={ carrier={ type=planet reference=3 } army_build_queue=1 army={ 20 21 } }
    5={ carrier={ type=planet reference=4 } army_build_queue=7 }
}
construction={ queue_mgr={ queues={
    0={ owner=0 location={ type=2 id=3 } simultaneous=1 type=planet }
    1={ owner=0 location={ type=2 id=3 } simultaneous=1 type=army }
    6={ owner=0 location={ type=2 id=4 } simultaneous=1 type=planet }
    7={ owner=0 location={ type=2 id=4 } simultaneous=1 type=army }
} } item_mgr={ items={
    30={ queue=7 paying_country=0 progress=0 progress_needed=90 resources={ minerals=50 }
         buildable_army={ army_type="defensive_army" species=2 planet=5 starbase=4294967295 } }
} } }
"""


def test_observation_joins_colony_army_queues():
    root = saves.loads(DUMP)
    assert observe_army_queues(root, "0") == {"known": True, "by_id": {
        "3": {"planet_id": "3", "colony_id": "0", "queue_id": "1", "system_id": "92", "army_ids": ["20", "21"]},
        "4": {"planet_id": "4", "colony_id": "5", "queue_id": "7", "system_id": "92", "army_ids": []}}}
    assert observe_army_queues(saves.loads(DUMP.replace("type=army }\n    6=", "type=planet }\n    6=")), "0")["known"] is False


def test_construction_accepts_army_items_on_the_colony_army_queue():
    construction = saves._construction(saves.loads(DUMP), "0", country_known=True)
    assert construction["known"] is True
    assert construction["items_by_id"]["30"]["target"] == {"planet_id": "4", "colony_id": "5"}
    broken = saves._construction(saves.loads(DUMP.replace("army_build_queue=7", "army_build_queue=1")), "0",
                                 country_known=True)
    assert broken["known"] is False


def _state(busy=False):
    def queue(qid, planet):
        return {"id": qid, "owner": "0", "type": "army", "location": {"type": 2, "id": planet}, "simultaneous": 1,
                "disabled": None, "known": True, "items_known": True, "item_count": 0, "items": [], "order_known": True}
    state = {
        "date": "2200.01.01", "country_id": "0",
        "metadata": {"campaign_id": "c", "game_version": "Cygnus v4.5.2"},
        "resources": {"energy": 200.0, "minerals": 200.0},
        "empire_build": {"government": "g"}, "country_controls": {"known": True}, "planets_by_id": {},
        "fleets_by_id": {"6": {"id": "6", "system_id": "92"}}, "shipbuilding": {},
        "construction": {"known": True, "items_by_id": {}, "queues_by_id": {"1": queue("1", "3"), "7": queue("7", "4")}},
        "control_state": {
            "queue_orders": {"known": True, "by_id": {"1": [], "7": []}},
            "starbase_queues": {"known": True, "by_id": {"72": {
                "id": "72", "station_id": "10", "fleet_id": "6", "queue_id": "2", "owner": "0", "level": "x"}}}},
        "army_queues": {"known": True, "by_id": {
            "3": {"planet_id": "3", "colony_id": "0", "queue_id": "1", "system_id": "92", "army_ids": []},
            "4": {"planet_id": "4", "colony_id": "5", "queue_id": "7", "system_id": "92", "army_ids": []}}},
    }
    if busy:
        item = {"id": "30", "queue_id": "7", "paying_country": "0", "progress": 0.0, "progress_needed": 90.0,
                "resources": {"minerals": 50.0}, "buildable_type": "buildable_army", "buildable": {}}
        state["construction"]["items_by_id"]["30"] = item
        state["construction"]["queues_by_id"]["7"].update(item_count=1, items=[item], order_known=False)
        state["control_state"]["queue_orders"]["by_id"]["7"] = ["30"]
    return state


def _after(state, starbase=4294967295, cost=50.0, species=2):
    after = copy.deepcopy(state)
    item = {"id": "22", "queue_id": "1", "paying_country": "0", "progress": 0.0, "progress_needed": 90.0,
            "resources": {"minerals": cost}, "buildable_type": "buildable_army",
            "buildable": {"army_type": "defensive_army", "species": species, "planet": 0, "starbase": starbase},
            "target": {"planet_id": "3", "colony_id": "0"}}
    after["construction"]["items_by_id"]["22"] = item
    after["construction"]["queues_by_id"]["1"].update(item_count=1, items=[item], order_known=False)
    after["control_state"]["queue_orders"]["by_id"]["1"] = ["22"]
    after["resources"]["minerals"] -= cost
    return after


def test_planet_army_resolves_and_verifies_exact_item_and_payment():
    state = _state()
    request = army_request("recruit_army", True, planet_id="3", army_type="defensive_army", species_id="2")
    command = resolve_army_command(state, request)
    assert command.to_dict() == {"planet_id": "3", "army_type": "defensive_army", "species_id": "2",
                                 "starbase_id": "4294967295", "colony_id": "0", "queue_id": "1"}
    assert verify_army_command(state, _after(state), command) is True
    assert extended_native.verify_extended(state, _after(state), command) is True
    assert verify_army_command(state, _after(state, species=3), command) is False
    assert verify_army_command(state, _after(state, starbase=72), command) is False
    wrong = _after(state)
    wrong["resources"]["energy"] -= 1
    assert verify_army_command(state, wrong, command) is False
    assert verify_army_command(state, state, command) is False


def test_busy_planet_and_unknown_definitions_are_rejected():
    with pytest.raises(ValueError, match="already has an army"):
        resolve_army_command(_state(busy=True), army_request("recruit_army", True, planet_id="4",
                                                             army_type="defensive_army", species_id="2"))
    with pytest.raises(ValueError, match="versioned game knowledge"):
        army_request("recruit_army", None, planet_id="3", army_type="defensive_army", species_id="2")
    with pytest.raises(ValueError):
        army_request("recruit_army", True, planet_id="3", army_type="Bad-Type", species_id="2")
    with pytest.raises(ValueError, match="army build queue"):
        resolve_army_command(_state(), army_request("recruit_army", True, planet_id="9",
                                                    army_type="defensive_army", species_id="2"))


def test_starbase_path_lists_idle_system_planets_and_verifies_rally_starbase():
    assert starbase_army_planets(_state(), "72") == ["3", "4"]
    assert starbase_army_planets(_state(busy=True), "72") == ["3"]
    with pytest.raises(ValueError, match="owned starbase"):
        starbase_army_planets(_state(), "73")
    state = _state()
    request = army_request("recruit_army", True, planet_id="3", army_type="defensive_army", species_id="2",
                           starbase_id="72")
    command = resolve_army_command(state, request)
    assert verify_army_command(state, _after(state, starbase=72), command) is True
    assert verify_army_command(state, _after(state), command) is False
    other = copy.deepcopy(state)
    other["army_queues"]["by_id"]["3"]["system_id"] = "93"
    with pytest.raises(ValueError, match="starbase's system"):
        resolve_army_command(other, request)


def test_payload_is_typed_and_tokenized():
    state = _state()
    command = resolve_army_command(state, army_request("recruit_army", True, planet_id="3",
                                                       army_type="defensive_army", species_id="2"))
    assert native_army_payload(command, "a" * 32) == {
        "kind": "recruit_army", "country_id": "0", "token": "a" * 32, "planet_id": "3",
        "army_type": "defensive_army", "species_id": "2", "starbase_id": "4294967295",
        "colony_id": "0", "queue_id": "1"}
    assert extended_native.extended_payload(command, "a" * 32)["kind"] == "recruit_army"
