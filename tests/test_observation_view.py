"""Brief observation views omit bulky fields and never invent empty lists."""
import pytest

from stellaris_autopilot.observation_view import apply_observation_view, observation_view


FULL = {
    "snapshot": "abc",
    "modified_at": 1.5,
    "state": {
        "date": "2200.12.26",
        "country_id": "0",
        "resources": {"energy": 1},
        "wars": {"known": True, "by_id": {"0": {"id": "0"}}},
        "ships": {"known": True, "by_id": {"3": {"id": "3"}}},
        "claims": {"known": False, "by_system": {}},
        "diplomacy": {"known": True, "pacts": [], "our_proposals": [{"kind": "action_offer_peace"}],
                      "recent_proposals": []},
        "construction": {"items_by_id": {"7": {"queue_id": "1"}}},
        "control_state": {"fleet_stances": {"3": "passive"}, "queue_orders": {}, "starbase_queues": {},
                          "other_bulky": {"x": 1}},
        "planets": {"lots": True},
        "research_status": {"known": False},
    },
}


def test_default_view_is_brief_and_rejects_other_tokens():
    assert observation_view(None) == "brief"
    assert observation_view("full") == "full"
    with pytest.raises(ValueError):
        observation_view("summary")


def test_brief_view_keeps_planning_fields_and_omits_the_rest():
    brief = apply_observation_view(FULL, "brief")
    assert brief["snapshot"] == "abc"
    state = brief["state"]
    assert state["date"] == "2200.12.26" and state["resources"] == {"energy": 1}
    assert state["wars"]["by_id"]["0"]["id"] == "0"
    assert state["claims"] == {"known": False, "by_system": {}}
    assert "planets" not in state and "research_status" not in state
    assert state["control_state"] == {"fleet_stances": {"3": "passive"}, "queue_orders": {}, "starbase_queues": {}}
    assert "other_bulky" not in state["control_state"]
    assert apply_observation_view(FULL, "full") is FULL or apply_observation_view(FULL, "full") == FULL


def test_brief_view_omits_missing_keys_instead_of_inventing_empty_lists():
    observed = {"snapshot": "s", "state": {"date": "2200.01.01", "resources": {"energy": 2}}}
    brief = apply_observation_view(observed, "brief")
    assert brief == {"snapshot": "s", "state": {"date": "2200.01.01", "resources": {"energy": 2}}}
    assert "wars" not in brief["state"] and "ships" not in brief["state"] and "diplomacy" not in brief["state"]
