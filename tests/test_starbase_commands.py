"""Starbase modules and buildings: observation, resolution and verification."""
import copy

import pytest

from stellaris_autopilot import saves
from stellaris_autopilot.starbase_commands import (
    native_starbase_payload, observe_starbase_slots, resolve_starbase_command, starbase_request,
    verify_starbase_command)
from stellaris_autopilot import extended_native

DUMP = """
starbase_mgr={ starbases={
    72={ level="starbase_level_starport" modules={ 0="shipyard" 1="solar_panel_network" } buildings={ 0="crew_quarters" } build_queue=2 station=10 }
    73={ level="starbase_level_outpost" build_queue=4 station=11 }
} }
"""


def test_slot_observation_reads_modules_and_buildings_by_slot():
    root = saves.loads(DUMP)
    assert observe_starbase_slots(root, ["72", "73"]) == {"known": True, "by_id": {
        "72": {"modules": {"0": "shipyard", "1": "solar_panel_network"}, "buildings": {"0": "crew_quarters"}},
        "73": {"modules": {}, "buildings": {}}}}
    assert observe_starbase_slots(root, ["99"])["known"] is False
    assert observe_starbase_slots(saves.loads(DUMP.replace('1="solar', '0="solar')), ["72"])["known"] is False


def _state():
    queue = {"id": "2", "owner": "0", "type": "starbase", "location": {"type": 0, "id": "10"}, "simultaneous": 1,
             "disabled": None, "known": True, "items_known": True, "item_count": 0, "items": [], "order_known": False}
    return {
        "date": "2200.01.01", "country_id": "0",
        "metadata": {"campaign_id": "c", "game_version": "Cygnus v4.5.2"}, "resources": {"energy": 200.0, "minerals": 200.0, "alloys": 200.0},
        "empire_build": {"government": "g"}, "country_controls": {"known": True}, "planets_by_id": {},
        "fleets_by_id": {"6": {"id": "6"}}, "shipbuilding": {"designs_known": True, "designs_by_id": {}},
        "construction": {"known": True, "items_by_id": {}, "queues_by_id": {"2": queue}},
        "control_state": {
            "queue_orders": {"known": True, "by_id": {"2": []}},
            "starbase_queues": {"known": True, "by_id": {"72": {
                "id": "72", "station_id": "10", "fleet_id": "6", "queue_id": "2", "owner": "0", "level": "x"}}}},
        "starbase_slots": {"known": True, "by_id": {"72": {"modules": {"0": "shipyard"}, "buildings": {}}}},
    }


def _after(state, kind="starbase_module", key="trading_hub", slot=1, cost=50.0):
    after = copy.deepcopy(state)
    item = {"id": "22", "queue_id": "2", "paying_country": "0", "progress": 0.0, "progress_needed": 90.0,
            "resources": {"alloys": cost}, "buildable_type": "buildable_" + kind,
            "buildable": {kind: key, "slot": slot, "starbase": 72}}
    after["construction"]["items_by_id"]["22"] = item
    after["construction"]["queues_by_id"]["2"].update(item_count=1, items=[item])
    after["control_state"]["queue_orders"]["by_id"]["2"] = ["22"]
    after["resources"]["alloys"] -= cost
    return after


def test_module_resolves_the_starbase_queue_and_verifies_exact_payment():
    state = _state()
    request = starbase_request("queue_starbase_module", True, starbase_id="72", slot="1", type_id="trading_hub")
    command = resolve_starbase_command(state, request)
    assert command.to_dict() == {"starbase_id": "72", "slot": "1", "module_id": "trading_hub",
                                 "station_id": "10", "queue_id": "2"}
    assert native_starbase_payload(command, "t") == {
        "kind": "queue_starbase_module", "country_id": "0", "token": "t", "starbase_id": "72", "slot": "1",
        "module_id": "trading_hub", "station_id": "10", "queue_id": "2"}
    assert extended_native.resolve_extended(state, request) == command
    assert verify_starbase_command(state, _after(state), command) is True
    assert extended_native.verify_extended(state, _after(state), command) is True
    for mutate in (lambda s: s["resources"].update(energy=199.0),
                   lambda s: s["construction"]["items_by_id"]["22"]["buildable"].update(slot=2),
                   lambda s: s["construction"]["items_by_id"]["22"]["buildable"].update(starbase_module="x"),
                   lambda s: s["construction"]["items_by_id"]["22"].update(buildable_type="buildable_starbase_building"),
                   lambda s: s["starbase_slots"]["by_id"]["72"]["modules"].update({"1": "trading_hub"}),
                   lambda s: s.update(date="2200.01.02")):
        changed = _after(state)
        mutate(changed)
        assert verify_starbase_command(state, changed, command) is False
    assert verify_starbase_command(state, state, command) is False


def test_building_uses_the_building_slots():
    state = _state()
    command = resolve_starbase_command(state, starbase_request(
        "queue_starbase_building", True, starbase_id="72", slot=0, type_id="crew_quarters"))
    assert command.to_dict()["building_id"] == "crew_quarters"
    assert verify_starbase_command(state, _after(state, "starbase_building", "crew_quarters", 0), command) is True
    assert verify_starbase_command(state, _after(state, key="crew_quarters", slot=0), command) is False


@pytest.mark.parametrize("kind,starbase,slot", [
    ("queue_starbase_module", "72", "0"),     # slot built
    ("queue_starbase_module", "99", "1"),     # not owned or observed
])
def test_rejects_built_slots_and_unknown_starbases(kind, starbase, slot):
    with pytest.raises(ValueError):
        resolve_starbase_command(_state(), starbase_request(kind, True, starbase_id=starbase, slot=slot, type_id="trading_hub"))


def test_rejects_a_slot_that_already_has_a_queued_item():
    queued = _after(_state())
    with pytest.raises(ValueError):
        resolve_starbase_command(queued, starbase_request(
            "queue_starbase_module", True, starbase_id="72", slot="1", type_id="trading_hub"))
    resolve_starbase_command(queued, starbase_request(
        "queue_starbase_module", True, starbase_id="72", slot="2", type_id="trading_hub"))


def test_rejects_unknown_slot_observation():
    state = _state()
    state["starbase_slots"]["known"] = False
    with pytest.raises(ValueError):
        resolve_starbase_command(state, starbase_request(
            "queue_starbase_module", True, starbase_id="72", slot="1", type_id="trading_hub"))


@pytest.mark.parametrize("kwargs", [
    {"definition": None, "starbase_id": "72", "slot": "1", "type_id": "trading_hub"},
    {"definition": True, "starbase_id": "72", "slot": "01", "type_id": "trading_hub"},
    {"definition": True, "starbase_id": "72", "slot": "100", "type_id": "trading_hub"},
    {"definition": True, "starbase_id": "72", "slot": True, "type_id": "trading_hub"},
    {"definition": True, "starbase_id": "72", "slot": "1", "type_id": "Trading Hub"},
    {"definition": True, "starbase_id": "x", "slot": "1", "type_id": "trading_hub"},
])
def test_requests_are_strictly_typed(kwargs):
    with pytest.raises(ValueError):
        starbase_request("queue_starbase_module", **kwargs)
    with pytest.raises(ValueError):
        starbase_request("queue_starbase", True, starbase_id="72", slot="1", type_id="trading_hub")
