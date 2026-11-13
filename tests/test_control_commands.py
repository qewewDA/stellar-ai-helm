"""Fleet stance, queue priority and defense platform: observation, resolution, verification."""
import copy

import pytest

from stellaris_autopilot import saves
from stellaris_autopilot.control_commands import (
    ControlRequest, ResolvedControlCommand, control_request, native_control_payload,
    observe_fleet_stances, observe_queue_orders, observe_starbase_queues,
    resolve_control_command, verify_control_command)
from stellaris_autopilot.task_plan import normalize_plan


DUMP = """
fleet={
    3={ name="A" owner=0 }
    4={ name="B" owner=0 fleet_stance=aggressive }
    5={ name="C" owner=0 fleet_stance=evasive }
}
ships={ 10={ fleet=6 } 11={ fleet=9 } }
starbase_mgr={ starbases={
    0={ level="starbase_level_starport" build_queue=2 shipyard_build_queue=3 station=10 }
    1={ level="starbase_level_outpost" build_queue=4 shipyard_build_queue=4294967295 station=11 }
} }
construction={ queue_mgr={ queues={
    2={ owner=0 location={ type=0 id=10 } simultaneous=1 type=starbase }
    3={ owner=0 location={ type=0 id=10 } simultaneous=1 type=ships items={ 21 20 } }
    4={ owner=1 location={ type=0 id=11 } simultaneous=1 type=starbase items={ 30 } }
} } }
"""


def _root(text=DUMP):
    return saves.loads(text)


def test_stance_observation_reads_omitted_default_as_passive():
    assert observe_fleet_stances(_root(), ["3", "4", "5"]) == {
        "known": True, "by_id": {"3": "passive", "4": "aggressive", "5": "evasive"}}


@pytest.mark.parametrize("text", [
    DUMP.replace('owner=0 fleet_stance=evasive', 'owner=0 fleet_stance=hold'),
    DUMP.replace('owner=0 fleet_stance=evasive', 'owner=0 fleet_stance=evasive fleet_stance=evasive'),
])
def test_stance_observation_is_unknown_for_unexpected_shapes(text):
    assert observe_fleet_stances(_root(text), ["3", "4", "5"])["known"] is False


def test_queue_order_observation_keeps_native_order_for_owned_queues_only():
    assert observe_queue_orders(_root(), "0") == {"known": True, "by_id": {"2": [], "3": ["21", "20"]}}
    assert observe_queue_orders(_root(DUMP.replace("items={ 21 20 }", "items={ 21 21 }")), "0")["known"] is False


def test_starbase_queues_join_owned_station_to_its_general_build_queue():
    observed = observe_starbase_queues(_root(), "0", [{"id": "6"}])
    assert observed == {"known": True, "by_id": {"0": {
        "id": "0", "station_id": "10", "fleet_id": "6", "queue_id": "2", "owner": "0",
        "level": "starbase_level_starport"}}}
    wrong = DUMP.replace("type=starbase }\n    3=", "type=ships }\n    3=")
    assert observe_starbase_queues(_root(wrong), "0", [{"id": "6"}])["known"] is False


def _item(item_id, queue_id, progress=0.0):
    return {"id": item_id, "queue_id": queue_id, "paying_country": "0", "progress": progress,
            "progress_needed": 60.0, "resources": {"alloys": 30.0}, "buildable_type": "buildable_ship",
            "buildable": {"ship_design_implementation": {"design": 3057, "upgrade": 4294967295, "growth_stage": 0},
                          "orbitable": {"starbase": 0}}}


def _state():
    items = {"20": _item("20", "3"), "21": _item("21", "3", 5.0)}

    def queue(queue_id, kind, count):
        return {"id": queue_id, "owner": "0", "type": kind, "location": {"type": 0, "id": "10"}, "simultaneous": 1,
                "disabled": None, "known": True, "items_known": True, "item_count": count,
                "items": [items[key] for key in sorted(items) if items[key]["queue_id"] == queue_id],
                "order_known": False}
    return {
        "date": "2200.01.01", "country_id": "0",
        "metadata": {"campaign_id": "c", "game_version": "Cygnus v4.5.2"},
        "resources": {"energy": 200.0, "minerals": 200.0, "alloys": 200.0},
        "empire_build": {"government": "g"}, "country_controls": {"known": True},
        "planets_by_id": {}, "fleets_by_id": {"3": {"id": "3", "mobile": True}, "6": {"id": "6"}},
        "shipbuilding": {"designs_known": True, "designs_by_id": {
            "3058": {"id": "3058", "ship_size": "military_station_small"},
            "3057": {"id": "3057", "ship_size": "corvette"}}},
        "construction": {"known": True, "items_by_id": items,
                         "queues_by_id": {"2": queue("2", "starbase", 0), "3": queue("3", "ships", 2)}},
        "control_state": {
            "fleet_stances": {"known": True, "by_id": {"3": "passive", "6": "passive"}},
            "queue_orders": {"known": True, "by_id": {"2": [], "3": ["21", "20"]}},
            "starbase_queues": {"known": True, "by_id": {"0": {
                "id": "0", "station_id": "10", "fleet_id": "6", "queue_id": "2", "owner": "0", "level": "x"}}}},
    }


def test_stance_resolves_the_current_stance_and_verifies_only_that_change():
    state = _state()
    command = resolve_control_command(state, control_request("set_fleet_stance", fleet_id="3", stance="aggressive"))
    assert command.to_dict() == {"fleet_id": "3", "stance": "aggressive", "current_stance": "passive"}
    after = copy.deepcopy(state)
    after["control_state"]["fleet_stances"]["by_id"]["3"] = "aggressive"
    assert verify_control_command(state, after, command) is True
    for mutate in (lambda s: s["resources"].update(energy=199.0),
                   lambda s: s["control_state"]["fleet_stances"]["by_id"].update({"6": "evasive"}),
                   lambda s: s.update(date="2200.01.02")):
        changed = copy.deepcopy(after)
        mutate(changed)
        assert verify_control_command(state, changed, command) is False
    assert verify_control_command(state, state, command) is False


@pytest.mark.parametrize("fleet_id,stance", [("3", "passive"), ("6", "aggressive"), ("99", "evasive")])
def test_stance_rejects_same_stance_stations_and_unknown_fleets(fleet_id, stance):
    with pytest.raises(ValueError):
        resolve_control_command(_state(), control_request("set_fleet_stance", fleet_id=fleet_id, stance=stance))


def test_queue_move_swaps_exactly_one_adjacent_pair():
    state = _state()
    command = resolve_control_command(state, control_request("move_queue_item", item_id="20", direction="up"))
    assert command.to_dict() == {"item_id": "20", "direction": "up", "queue_id": "3", "neighbor_id": "21"}
    after = copy.deepcopy(state)
    after["control_state"]["queue_orders"]["by_id"]["3"] = ["20", "21"]
    assert verify_control_command(state, after, command) is True
    assert verify_control_command(state, state, command) is False
    changed = copy.deepcopy(after)
    changed["construction"]["items_by_id"]["21"]["progress"] = 6.0
    assert verify_control_command(state, changed, command) is False


@pytest.mark.parametrize("item_id,direction", [("21", "up"), ("20", "down"), ("99", "up")])
def test_queue_move_rejects_queue_ends_and_unknown_items(item_id, direction):
    with pytest.raises(ValueError):
        resolve_control_command(_state(), control_request("move_queue_item", item_id=item_id, direction=direction))


def test_queue_move_rejects_an_order_that_conflicts_with_item_membership():
    state = _state()
    state["control_state"]["queue_orders"]["by_id"]["3"] = ["21"]
    with pytest.raises(ValueError):
        resolve_control_command(state, control_request("move_queue_item", item_id="21", direction="down"))


def _platform_after(state, cost=50.0):
    after = copy.deepcopy(state)
    item = {"id": "22", "queue_id": "2", "paying_country": "0", "progress": 0.0, "progress_needed": 90.0,
            "resources": {"alloys": cost}, "buildable_type": "buildable_defense_platform",
            "buildable": {"ship_design_implementation": {"design": 3058, "upgrade": 4294967295, "growth_stage": 0},
                          "starbase": 0}}
    after["construction"]["items_by_id"]["22"] = item
    after["construction"]["queues_by_id"]["2"].update(item_count=1, items=[item])
    after["control_state"]["queue_orders"]["by_id"]["2"] = ["22"]
    after["resources"]["alloys"] -= cost
    return after


def test_defense_platform_resolves_the_general_starbase_queue_and_verifies_exact_payment():
    state = _state()
    command = resolve_control_command(state, control_request("queue_defense_platform", starbase_id="0", design_id="3058"))
    assert command.to_dict() == {"starbase_id": "0", "design_id": "3058", "station_id": "10", "queue_id": "2"}
    assert verify_control_command(state, _platform_after(state), command) is True
    unpaid = _platform_after(state)
    unpaid["resources"]["alloys"] += 1.0
    assert verify_control_command(state, unpaid, command) is False
    extra = _platform_after(state)
    extra["resources"]["energy"] -= 1.0
    assert verify_control_command(state, extra, command) is False
    wrong = _platform_after(state)
    wrong["construction"]["items_by_id"]["22"]["buildable"]["starbase"] = 1
    assert verify_control_command(state, wrong, command) is False


def test_defense_platform_rejects_non_platform_designs():
    with pytest.raises(ValueError):
        resolve_control_command(_state(), control_request("queue_defense_platform", starbase_id="0", design_id="3057"))


@pytest.mark.parametrize("kind,payload", [
    ("set_fleet_stance", {"fleet_id": "3", "stance": "hold"}),
    ("move_queue_item", {"item_id": "7", "direction": "top"}),
    ("move_queue_item", {"item_id": "7"}),
    ("queue_defense_platform", {"starbase_id": "0", "design_id": "x"}),
    ("set_policy", {"fleet_id": "3", "stance": "passive"}),
])
def test_requests_are_strictly_typed(kind, payload):
    with pytest.raises(ValueError):
        control_request(kind, **payload)


def test_payload_carries_only_resolved_identities():
    command = ResolvedControlCommand(ControlRequest("move_queue_item", item_id="20", direction="up"),
                                     queue_id="3", neighbor_id="21")
    assert native_control_payload(command, "a" * 32) == {
        "kind": "move_queue_item", "country_id": "0", "token": "a" * 32,
        "item_id": "20", "direction": "up", "queue_id": "3", "neighbor_id": "21"}
    with pytest.raises(ValueError):
        native_control_payload(ResolvedControlCommand(ControlRequest("move_queue_item", item_id="20", direction="up"),
                                                      queue_id="3", neighbor_id="20"), "a" * 32)


def test_plans_accept_control_steps_and_stance_does_not_count_as_a_fleet_order():
    steps, _ = normalize_plan([
        {"kind": "set_fleet_stance", "fleet_id": "3", "stance": "aggressive"},
        {"kind": "fleet_stop", "fleet_id": "3"},
        {"kind": "move_queue_item", "item_id": "20", "direction": "down"},
        {"kind": "queue_defense_platform", "starbase_id": "0", "design_id": "3058"},
    ])
    assert [step["kind"] for step in steps][0] == "set_fleet_stance"
    with pytest.raises(ValueError):
        normalize_plan([{"kind": "set_fleet_stance", "fleet_id": "3", "stance": "hold"}])
