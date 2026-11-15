"""Bounded typed task intent is validated before acquiring a game connection."""
import copy

import pytest

from stellaris_autopilot.task_plan import dispatch_step, normalize_plan, request_identifier


VALID_STEPS = [
    {"kind": "select_research", "area": "physics", "technology_id": "tech_power_plant_1"},
    {"kind": "queue_district", "planet_id": "1", "district_id": "district_city"},
    {"kind": "queue_building", "planet_id": "1", "zone_id": "2", "building_id": "building_lab_1"},
    {"kind": "queue_ship", "starbase_id": "3", "design_id": "4"},
    {"kind": "queue_colony_ship", "starbase_id": "3", "design_id": "4", "species_id": "5"},
    {"kind": "upgrade_building", "planet_id": "1", "zone_id": "2", "building_id": "6",
     "target_building_id": "building_lab_2"},
    {"kind": "demolish_building", "planet_id": "1", "zone_id": "2", "building_id": "6"},
    {"kind": "convert_zone", "planet_id": "1", "district_id": "2", "slot_index": 1, "target_zone_id": "zone_industrial"},
    {"kind": "cancel_construction", "planet_id": "1", "item_id": "7"},
    {"kind": "survey_planet", "fleet_id": "8", "planet_id": "9"},
    {"kind": "colonize_planet", "fleet_id": "8", "planet_id": "9"},
    {"kind": "build_station", "fleet_id": "8", "planet_id": "9", "station_type": "mining"},
    {"kind": "fleet_move", "fleet_id": "8", "planet_id": "9"},
    {"kind": "fleet_orbit", "fleet_id": "8", "planet_id": "9"},
    {"kind": "fleet_wait", "fleet_id": "8", "days": 1},
    {"kind": "fleet_stop", "fleet_id": "8"},
    {"kind": "set_paused", "paused": True},
]


@pytest.mark.parametrize("step", VALID_STEPS, ids=lambda step: step["kind"])
def test_every_supported_step_is_normalized_without_mutating_input(step):
    original = copy.deepcopy(step)
    normalized, digest = normalize_plan([step])
    assert normalized == [step]
    assert step == original
    assert len(digest) == 64 and all(character in "0123456789abcdef" for character in digest)


@pytest.mark.parametrize("value", ["a", "task:earth-1.2_3", "9" * 96])
def test_request_id_accepts_bounded_ascii(value):
    assert request_identifier(value) == value


@pytest.mark.parametrize("value", [None, True, 1, {}, "", "a" * 97, "../task", "a/b", "a\\b",
                                         "_task", ":task", ".task", "-task", "task\n", "task ",
                                         "任务", "ｔａｓｋ", "a\x00b"])
def test_request_id_rejects_path_tokens_coercion_and_non_ascii(value):
    with pytest.raises(ValueError):
        request_identifier(value)


@pytest.mark.parametrize("steps", [None, {}, (), [], "queue_ship", [None], [{"kind": "unknown"}]])
def test_plan_requires_a_nonempty_typed_list(steps):
    with pytest.raises(ValueError):
        normalize_plan(steps)


@pytest.mark.parametrize("size", [1, 20])
def test_plan_length_includes_both_boundaries(size):
    steps = [{"kind": "queue_ship", "starbase_id": "3", "design_id": "4"} for _ in range(size)]
    assert len(normalize_plan(steps)[0]) == size


def test_twenty_one_steps_rejected():
    with pytest.raises(ValueError):
        normalize_plan([VALID_STEPS[3]] * 21)


@pytest.mark.parametrize("step", VALID_STEPS, ids=lambda step: step["kind"])
def test_unknown_fields_are_rejected_for_every_action(step):
    with pytest.raises(ValueError):
        normalize_plan([{**step, "script": "anything"}])


@pytest.mark.parametrize("value", [True, 1, 1.0, None, "01", "-1", " 1", "1 ", "1\n", "１",
                                         "4294967295", "99999999999", "1;exit"])
@pytest.mark.parametrize("step_index,field", [(1, "planet_id"), (2, "zone_id"), (3, "starbase_id"),
                                             (3, "design_id"), (4, "species_id"), (5, "building_id"),
                                             (8, "item_id"), (9, "fleet_id")])
def test_all_object_handle_roles_require_canonical_ascii(step_index, field, value):
    with pytest.raises(ValueError):
        normalize_plan([{**VALID_STEPS[step_index], field: value}])


@pytest.mark.parametrize("index,field,value", [
    (0, "technology_id", "power_plant"), (0, "area", "all"),
    (1, "district_id", "city"), (2, "building_id", "lab"),
    (5, "target_building_id", "lab"), (7, "target_zone_id", "industrial"),
    (11, "station_type", "any"), (11, "station_type", None),
    (14, "days", True), (14, "days", "10"), (14, "days", 0),
    (14, "days", 366), (14, "days", 1.0), (14, "days", float("nan")),
    (16, "paused", 1), (16, "paused", "true"),
])
def test_invalid_data_keys_literals_and_coerced_scalars_rejected(index, field, value):
    with pytest.raises(ValueError):
        normalize_plan([{**VALID_STEPS[index], field: value}])


@pytest.mark.parametrize("snapshot", [True, 1, "", "a" * 129, "../snapshot", "快照", "before\n"])
def test_snapshot_identifier_is_bounded_and_not_coerced(snapshot):
    with pytest.raises(ValueError):
        normalize_plan([VALID_STEPS[3]], snapshot)


@pytest.mark.parametrize("steps", [
    [{"kind": "set_paused", "paused": False}],
    [VALID_STEPS[3], {"kind": "set_paused", "paused": True}],
    [{"kind": "set_paused", "paused": True}, {"kind": "set_paused", "paused": True}],
])
def test_pause_is_allowed_only_once_as_explicit_first_true(steps):
    with pytest.raises(ValueError):
        normalize_plan(steps)


def test_first_explicit_pause_can_precede_a_write():
    steps = [VALID_STEPS[-1], VALID_STEPS[3]]
    assert normalize_plan(steps)[0] == steps


@pytest.mark.parametrize("first,second", [(9, 9), (9, 12), (12, 15), (11, 10), (14, 13)])
def test_same_fleet_unfinished_orders_cannot_be_chained(first, second):
    steps = [copy.deepcopy(VALID_STEPS[first]), copy.deepcopy(VALID_STEPS[second])]
    with pytest.raises(ValueError, match="fleet"):
        normalize_plan(steps)


def test_explicit_stop_then_move_is_valid_but_does_not_enable_a_third_order():
    steps = [VALID_STEPS[15], VALID_STEPS[12]]
    assert normalize_plan(steps)[0] == steps
    with pytest.raises(ValueError, match="fleet"):
        normalize_plan(steps + [VALID_STEPS[9]])


def test_different_fleets_can_have_independent_unfinished_orders():
    steps = [VALID_STEPS[9], {**VALID_STEPS[9], "fleet_id": "10"}]
    assert normalize_plan(steps)[0] == steps


def test_hash_is_independent_of_dict_order_but_covers_order_payload_and_snapshot():
    first, second = VALID_STEPS[1], VALID_STEPS[3]
    normalized, digest = normalize_plan([first, second])
    reverse_keys = [dict(reversed(list(step.items()))) for step in [first, second]]
    assert normalize_plan(reverse_keys) == (normalized, digest)
    assert normalize_plan([first, second], None)[1] == digest
    assert normalize_plan([second, first])[1] != digest
    assert normalize_plan([first, {**second, "design_id": "5"}])[1] != digest
    assert normalize_plan([first, second], "snapshot-1")[1] != digest


class Calls:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return "existing-api-result"
        return record


@pytest.mark.parametrize("step,expected", [
    (VALID_STEPS[0], ("select_research", ("physics", "tech_power_plant_1"), {})),
    (VALID_STEPS[1], ("queue_district", ("1", "district_city"), {})),
    (VALID_STEPS[2], ("queue_building", ("1", "2", "building_lab_1"), {})),
    (VALID_STEPS[5], ("planet_command", ("upgrade_building",),
                      {"planet_id": "1", "zone_id": "2", "building_id": "6", "target_building": "building_lab_2"})),
    (VALID_STEPS[7], ("planet_command", ("convert_zone",),
                      {"planet_id": "1", "district_id": "2", "slot_index": 1, "target_zone": "zone_industrial"})),
    (VALID_STEPS[9], ("fleet_command", ("survey_planet",), {"fleet_id": "8", "planet_id": "9"})),
    (VALID_STEPS[12], ("issue_order", (VALID_STEPS[12],), {})),
    (VALID_STEPS[-1], ("set_paused", (True,), {})),
])
def test_dispatch_uses_existing_typed_api_and_maps_definition_fields(step, expected):
    connection = Calls()
    assert dispatch_step(connection, copy.deepcopy(step)) == "existing-api-result"
    assert connection.calls == [expected]


def test_event_option_step_is_typed_and_dispatched_to_the_event_command():
    step = {"kind": "choose_event_option", "player_event_id": "12", "option_index": 1}
    normalized, _ = normalize_plan([step])
    assert normalized == [step]
    connection = Calls()
    assert dispatch_step(connection, copy.deepcopy(step)) == "existing-api-result"
    assert connection.calls == [("event_command", ("choose_event_option",),
                                 {"player_event_id": "12", "option_index": 1})]
    for bad in ({**step, "option_index": "1"}, {**step, "option_index": 64}, {**step, "player_event_id": "012"},
                {**step, "script": "x"}):
        with pytest.raises(ValueError):
            normalize_plan([bad])


def test_border_steps_are_typed_and_dispatched_to_the_diplomacy_command():
    for kind in ("close_borders", "open_borders", "insult", "make_rival", "end_rivalry", "recall_embassy",
                 "harm_relations", "improve_relations", "propose_embassy", "propose_non_aggression_pact",
                 "propose_research_agreement", "propose_commercial_pact", "propose_migration_pact"):
        step = {"kind": kind, "target_country_id": "73"}
        normalized, _ = normalize_plan([step])
        assert normalized == [step]
        connection = Calls()
        assert dispatch_step(connection, copy.deepcopy(step)) == "existing-api-result"
        assert connection.calls == [("diplomacy_command", (kind,), {"target_country_id": "73"})]
        for bad in ({**step, "target_country_id": "0"}, {**step, "target_country_id": 73}, {**step, "x": "1"}):
            with pytest.raises(ValueError):
                normalize_plan([bad])


def test_policy_and_edict_steps_need_definition_facts_and_use_the_country_command():
    from stellaris_autopilot.country_commands import CountryDefinition
    steps = [{"kind": "set_policy", "policy_id": "economic_policy", "option_id": "civilian_economy"},
             {"kind": "activate_edict", "edict_id": "capacity_subsidies"},
             {"kind": "deactivate_edict", "edict_id": "capacity_subsidies"}]
    normalized, _ = normalize_plan(steps)
    assert normalized == steps
    connection = Calls()
    blocked = dispatch_step(connection, copy.deepcopy(steps[0]))
    assert blocked["status"] == "blocked" and connection.calls == []
    lookups = []
    definition = object.__new__(CountryDefinition)

    def definitions(kind, category, object_id):
        lookups.append((kind, category, object_id))
        return definition
    for step in steps:
        assert dispatch_step(connection, copy.deepcopy(step), definitions) == "existing-api-result"
    assert lookups == [("set_policy", "policies", "economic_policy"), ("activate_edict", "edicts", "capacity_subsidies")]
    assert connection.calls == [
        ("country_command", ("set_policy", definition), {"policy": "economic_policy", "option": "civilian_economy"}),
        ("country_command", ("activate_edict", definition), {"edict": "capacity_subsidies"}),
        ("country_command", ("deactivate_edict", None), {"edict": "capacity_subsidies"})]
    refused = dispatch_step(Calls(), copy.deepcopy(steps[1]), lambda *_: {"status": "blocked", "reason": "x"})
    assert refused == {"status": "blocked", "reason": "x"}
    for bad in ({**steps[0], "option_id": 1}, {**steps[1], "edict_id": "bad id"}, {**steps[2], "cost": "1"}):
        with pytest.raises(ValueError):
            normalize_plan([bad])


def test_new_single_tools_are_typed_and_dispatched():
    cases = [
        ({"kind": "set_species_right", "species_id": "1", "right": "citizenship_full"},
         ("species_right_command", ("set_species_right", True), {"species_id": "1", "right": "citizenship_full"})),
        ({"kind": "queue_starbase_module", "starbase_id": "2", "slot": "1", "module_id": "shipyard"},
         ("starbase_command", ("queue_starbase_module", True),
          {"starbase_id": "2", "slot": "1", "type_id": "shipyard"})),
        ({"kind": "queue_starbase_building", "starbase_id": "2", "slot": "1", "building_id": "crew_quarters"},
         ("starbase_command", ("queue_starbase_building", True),
          {"starbase_id": "2", "slot": "1", "type_id": "crew_quarters"})),
        ({"kind": "recruit_army", "planet_id": "3", "army_type": "assault_army", "species_id": "1"},
         ("army_command", ("recruit_army", True),
          {"planet_id": "3", "army_type": "assault_army", "species_id": "1"})),
        ({"kind": "add_system_claim", "system_id": "11"},
         ("claim_command", ("add_system_claim",), {"system_id": "11"})),
        ({"kind": "declare_war", "target_country_id": "66", "war_goal": "wg_conquest"},
         ("war_command", ("declare_war",), {"target_country_id": "66", "war_goal": "wg_conquest"})),
        ({"kind": "offer_peace", "target_country_id": "66", "terms": "status_quo", "war_id": "0"},
         ("war_command", ("offer_peace",), {"target_country_id": "66", "terms": "status_quo", "war_id": "0"})),
        ({"kind": "set_ship_design", "ship_id": "3", "design_id": "3057"},
         ("ship_design_command", ("set_ship_design",), {"ship_id": "3", "design_id": "3057"})),
    ]
    for step, expected in cases:
        assert normalize_plan([step])[0] == [step]
        connection = Calls()
        assert dispatch_step(connection, copy.deepcopy(step), lambda *_: True) == "existing-api-result"
        assert connection.calls == [expected]
        with pytest.raises(ValueError):
            normalize_plan([{**step, "script": "x"}])


def test_recruit_armies_at_starbase_step_fans_out_through_army_command():
    step = {"kind": "recruit_armies_at_starbase", "starbase_id": "12", "army_type": "assault_army", "species_id": "1"}
    assert normalize_plan([step])[0] == [step]
    with pytest.raises(ValueError):
        normalize_plan([{**step, "starbase_id": "012"}])

    class Connection:
        def __init__(self):
            self.calls = []

        def observe(self):
            return {"state": {"ready": True}}

        def army_command(self, kind, definition, **payload):
            self.calls.append((kind, definition, payload))
            return {"status": "command_verified"}

    import stellaris_autopilot.task_plan as task_plan
    original = task_plan.starbase_army_planets
    task_plan.starbase_army_planets = lambda state, starbase_id: ["3", "4"]
    try:
        connection = Connection()
        result = dispatch_step(connection, copy.deepcopy(step), lambda *_: True)
    finally:
        task_plan.starbase_army_planets = original
    assert result["status"] == "command_verified" and result["planets"] == ["3", "4"]
    assert connection.calls == [
        ("recruit_army", True, {"planet_id": "3", "army_type": "assault_army", "species_id": "1", "starbase_id": "12"}),
        ("recruit_army", True, {"planet_id": "4", "army_type": "assault_army", "species_id": "1", "starbase_id": "12"}),
    ]


def test_enact_planet_decision_step_uses_definition_facts():
    step = {"kind": "enact_planet_decision", "planet_id": "1", "decision_id": "decision_day"}
    assert normalize_plan([step])[0] == [step]
    blocked = dispatch_step(Calls(), copy.deepcopy(step))
    assert blocked["status"] == "blocked" and blocked["reason"].startswith("Versioned knowledge")
    connection = Calls()
    definition = object()
    assert dispatch_step(connection, copy.deepcopy(step), lambda *_: definition) == "existing-api-result"
    assert connection.calls == [("decision_command", ("enact_planet_decision", definition),
                                 {"planet_id": "1", "decision_id": "decision_day"})]
    with pytest.raises(ValueError):
        normalize_plan([{**step, "planet_id": "01"}])
