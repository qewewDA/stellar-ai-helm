import asyncio

import pytest

from stellaris_autopilot.intent_plan import GOAL_NAMES, propose_plan
from stellaris_autopilot.task_plan import normalize_plan

import test_diplomacy_commands as diplomacy
from test_direct_mcp import config_path as _config_fixture


@pytest.fixture
def config_path(tmp_path):
    return _config_fixture.__wrapped__(tmp_path)


class Knowledge:
    bundle = {"game_version": "4.test"}
    RECORDS = {
        "technology:tech_lasers_1": {"definition": {}},
        "districts:district_city": {"definition": {}},
        "buildings:building_research_lab_1": {"definition": {}},
        "policies:economic_policy": {"definition": {"option": [{"name": "civilian_economy"},
                                                              {"name": "militarist_economy"}]}},
        "edicts:capacity_subsidies": {"definition": {"resources": {"cost": {"energy": 50}}}},
    }

    def __init__(self):
        self.lookups = []

    def lookup(self, category, object_id, sections=None):
        self.lookups.append((category, object_id))
        record = self.RECORDS.get(category + ":" + object_id)
        return {"status": "not_found"} if record is None else {"status": "found", **record}


GOALS = [
    ({"goal": "research", "area": "physics", "technology": "tech_lasers_1"},
     {"kind": "select_research", "area": "physics", "technology_id": "tech_lasers_1"}),
    ({"goal": "build_district", "planet": "3", "district": "district_city"},
     {"kind": "queue_district", "planet_id": "3", "district_id": "district_city"}),
    ({"goal": "build_building", "planet": "3", "zone": "4", "building": "building_research_lab_1"},
     {"kind": "queue_building", "planet_id": "3", "zone_id": "4", "building_id": "building_research_lab_1"}),
    ({"goal": "build_ship", "starbase": "12", "design": "7"}, {"kind": "queue_ship", "starbase_id": "12", "design_id": "7"}),
    ({"goal": "survey", "fleet": "1", "planet": "104"}, {"kind": "survey_planet", "fleet_id": "1", "planet_id": "104"}),
    ({"goal": "wait", "fleet": "2", "days": 30}, {"kind": "fleet_wait", "fleet_id": "2", "days": 30}),
    ({"goal": "fleet_stance", "fleet": "1", "stance": "evasive"},
     {"kind": "set_fleet_stance", "fleet_id": "1", "stance": "evasive"}),
    ({"goal": "reorder_queue", "item": "31", "direction": "up"}, {"kind": "move_queue_item", "item_id": "31", "direction": "up"}),
    ({"goal": "defense_platform", "starbase": "0", "design": "3058"},
     {"kind": "queue_defense_platform", "starbase_id": "0", "design_id": "3058"}),
    ({"goal": "event_option", "event": "71", "option": 1},
     {"kind": "choose_event_option", "player_event_id": "71", "option_index": 1}),
    ({"goal": "set_policy", "policy": "economic_policy", "option": "civilian_economy"},
     {"kind": "set_policy", "policy_id": "economic_policy", "option_id": "civilian_economy"}),
    ({"goal": "activate_edict", "edict": "capacity_subsidies"}, {"kind": "activate_edict", "edict_id": "capacity_subsidies"}),
    ({"goal": "close_borders", "target": "65"}, {"kind": "close_borders", "target_country_id": "65"}),
    ({"goal": "make_rival", "country": "16777218"}, {"kind": "make_rival", "target_country_id": "16777218"}),
    ({"goal": "pause"}, {"kind": "set_paused", "paused": True}),
    ({"goal": "queue_ship", "starbase_id": "12", "design_id": "7"}, {"kind": "queue_ship", "starbase_id": "12", "design_id": "7"}),
]


@pytest.mark.parametrize("goal,step", GOALS)
def test_each_goal_maps_to_one_existing_plan_step(goal, step):
    result = propose_plan([goal], knowledge=Knowledge())
    assert result["steps"] == [step] and result["side_effects"] == "none"
    assert result["checks"][0]["schema"] == "ok" and result["checks"][0]["knowledge"] == "ok"
    assert result["checks"][0]["observation"] == "skipped" and result["status"] == "proposed"
    assert normalize_plan(result["steps"])[0] == [step]


def test_every_plan_step_kind_is_reachable():
    kinds = {propose_plan([goal])["steps"][0]["kind"] for goal, _ in GOALS}
    assert {"select_research", "set_policy", "choose_event_option", "close_borders"} <= kinds
    assert {"set_fleet_stance", "move_queue_item", "queue_defense_platform", "insult", "recall_embassy"} <= set(GOAL_NAMES)


@pytest.mark.parametrize("goal,problem", [
    ({"goal": "declare_war", "target": "65"}, "Unknown goal"),
    ({"goal": "close_borders", "target": "65", "script": "x"}, "no field"),
    ({"goal": "close_borders", "target": "65", "country": "66"}, "twice"),
    ({"goal": "close_borders", "target": "0"}, "another country"),
    ({"goal": "wait", "fleet": "2", "days": "30"}, "valid integer"),
    ("close borders", "object"),
])
def test_bad_goals_are_reported_and_never_proposed(goal, problem):
    result = propose_plan([goal])
    assert result["status"] == "needs_changes" and result["steps"] == []
    assert result["checks"][0]["schema"] == "error" and problem in " ".join(result["checks"][0]["problems"])


def test_goal_count_is_bounded():
    with pytest.raises(ValueError):
        propose_plan([])
    with pytest.raises(ValueError):
        propose_plan([{"goal": "pause"}] * 21)


def test_knowledge_rejects_unknown_ids_and_policy_options():
    knowledge = Knowledge()
    result = propose_plan([{"goal": "research", "area": "physics", "technology": "tech_missing"},
                           {"goal": "set_policy", "policy": "economic_policy", "option": "unknown_economy"},
                           {"goal": "activate_edict", "edict": "missing_edict"}], knowledge=knowledge)
    assert result["status"] == "needs_changes" and [check["knowledge"] for check in result["checks"]] == ["error"] * 3
    assert "not_found" in result["checks"][0]["problems"][0]
    assert "not an option" in result["checks"][1]["problems"][0]


def test_policy_steps_without_knowledge_are_flagged():
    result = propose_plan([{"goal": "set_policy", "policy": "economic_policy", "option": "civilian_economy"}])
    assert result["status"] == "needs_changes" and result["knowledge_checked"] is False
    assert "versioned knowledge" in result["plan_problems"][0]


def test_observation_resolves_each_step_against_the_supplied_state():
    state = diplomacy._state()
    result = propose_plan([{"goal": "close_borders", "target": "73"}, {"goal": "open_borders", "target": "73"},
                           {"goal": "insult", "target": "99"}], state=state, snapshot="s1")
    assert [check["observation"] for check in result["checks"]] == ["ok", "error", "error"]
    assert "already open" in result["checks"][1]["problems"][0]
    assert "contact" in result["checks"][2]["problems"][0]
    assert result["status"] == "needs_changes" and result["observation_checked"] is True
    good = propose_plan([{"goal": "close_borders", "target": "73"}], state=state, snapshot="s1")
    assert good["status"] == "proposed" and good["expected_snapshot"] == "s1" and good["plan_hash"]


def test_plan_level_rules_are_applied():
    result = propose_plan([{"goal": "survey", "fleet": "1", "planet": "104"},
                           {"goal": "move_fleet", "fleet": "1", "planet": "105"}])
    assert result["status"] == "needs_changes" and "already has an unfinished plan order" in result["plan_problems"][0]
    later_pause = propose_plan([{"goal": "close_borders", "target": "65"}, {"goal": "pause"}])
    assert later_pause["status"] == "needs_changes" and "first step" in later_pause["plan_problems"][0]


class _ObserveOnly:
    def __init__(self, observed):
        self.observed, self.calls = observed, 0

    def observe(self):
        self.calls += 1
        return self.observed




def test_plan_intent_tool_is_read_only_and_only_reads_the_latest_observation(config_path):
    from stellaris_autopilot import direct_mcp as direct
    from test_direct_mcp import invoke
    state = diplomacy._state()
    connection = _ObserveOnly({"status": "observed", "snapshot": "s1", "state": state})
    server = direct.build_direct_server(config_path, connection=connection)
    tool = {tool.name: tool for tool in asyncio.run(server.list_tools())}["plan_intent"]
    assert tool.annotations.readOnlyHint is True and set(tool.inputSchema["properties"]) == {"goals", "snapshot"}
    offline = invoke(server, "plan_intent", {"goals": [{"goal": "close_borders", "target": "73"}]})
    assert offline["status"] == "proposed" and offline["observation_checked"] is False and connection.calls == 0
    checked = invoke(server, "plan_intent", {"goals": [{"goal": "close_borders", "target": "73"}], "snapshot": "s1"})
    assert checked["status"] == "proposed" and checked["checks"][0]["observation"] == "ok" and connection.calls == 1
    stale = invoke(server, "plan_intent", {"goals": [{"goal": "close_borders", "target": "73"}], "snapshot": "s0"})
    assert stale["status"] == "stale" and stale["side_effects"] == "none"
    bounded = invoke(server, "plan_intent", {"goals": []})
    assert bounded["status"] == "blocked"
    assert not direct._configuration(config_path).state_dir.exists()
