"""Brief observation views omit bulky fields and never invent empty lists."""
import pytest

from stellaris_autopilot.observation_view import apply_observation_view, observation_view


FULL = {
    "snapshot": "abc",
    "modified_at": 1.5,
    "deferred_verification": {"pending": True, "verifications": []},
    "state": {
        "date": "2200.12.26",
        "country_id": "0",
        "resources": {"energy": 1},
        "wars": {"known": True, "by_id": {"0": {"id": "0"}}},
        "ships": {"known": True, "by_id": {"3": {"id": "3"}}},
        "claims": {"known": False, "by_system": {}},
        "diplomacy": {"known": True, "pacts": [], "our_proposals": [{"kind": "action_offer_peace"}],
                      "recent_proposals": []},
        "construction": {"items_by_id": {"7": {"queue_id": "1", "planet_id": "3", "type": "building"}}},
        "control_state": {"fleet_stances": {"3": "passive"}, "queue_orders": {}, "starbase_queues": {},
                          "other_bulky": {"x": 1}},
        "planets": [
            {"id": "3", "name": "Earth", "districts_known": True, "district_levels": {"city": 2},
             "zones_known": True, "zones_by_id": {
                 "10": {"id": "10", "district_id": "1", "type": "urban",
                        "buildings_known": True, "buildings_by_id": {"20": {"id": "20", "type": "capital", "position": 0}}}
             }},
        ],
        "research_status": {
            "known": True,
            "areas": {"physics": {"current": "tech_lasers_1", "alternatives": ["tech_lasers_2"], "queue": []}},
        },
        "pending_events": {"known": True, "by_id": {
            "36": {"id": "36", "event_id": "first_contact.1", "country": "0", "date": "2200.01.01"},
            "40": {"id": "40", "event_id": "ai.1", "country": "65"},
        }},
        "species_rights": {"known": True, "entries": {"primary": {"species_index": "1", "categories": {"migration": "yes"}}}},
        "country_controls": {"known": True, "policies": {"war_philosophy": {"selected": "unrestricted", "date": [2200, 1, 1]}},
                             "edicts": {"capacity_subsidies": {"start_date": [2200, 1, 1]}}},
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
    assert brief["deferred_verification"]["pending"] is True
    state = brief["state"]
    assert state["date"] == "2200.12.26" and state["resources"] == {"energy": 1}
    assert state["wars"]["by_id"]["0"]["id"] == "0"
    assert state["claims"] == {"known": False, "by_system": {}}
    assert state["planets"][0]["planet_id"] == "3"
    assert state["planets"][0]["districts"] == {"city": 2}
    assert state["planets"][0]["zones"][0]["zone_id"] == "10"
    assert state["planets"][0]["zones"][0]["buildings"][0]["building_id"] == "20"
    assert state["planets"][0]["construction_queue"][0]["id"] == "7"
    assert state["research"]["areas"]["physics"]["current"] == "tech_lasers_1"
    assert state["pending_events"]["events"][0]["player_event_id"] == "36"
    assert state["pending_events"]["other_country_event_count"] == 1
    assert state["species_rights"]["primary"]["species_index"] == "1"
    assert state["policies_edicts"]["policies"][0]["policy"] == "war_philosophy"
    assert state["policies_edicts"]["edicts"][0]["edict"] == "capacity_subsidies"
    assert "research_status" not in state
    assert "country_controls" not in state
    assert state["control_state"] == {"fleet_stances": {"3": "passive"}, "queue_orders": {}, "starbase_queues": {}}
    assert "other_bulky" not in state["control_state"]
    assert apply_observation_view(FULL, "full") is FULL or apply_observation_view(FULL, "full") == FULL


def test_brief_view_omits_missing_keys_instead_of_inventing_empty_lists():
    observed = {"snapshot": "s", "state": {"date": "2200.01.01", "resources": {"energy": 2}}}
    brief = apply_observation_view(observed, "brief")
    assert brief == {"snapshot": "s", "state": {"date": "2200.01.01", "resources": {"energy": 2}}}
    assert "wars" not in brief["state"] and "ships" not in brief["state"] and "diplomacy" not in brief["state"]
    assert "planets" not in brief["state"] and "research" not in brief["state"]
    assert "pending_events" not in brief["state"] and "species_rights" not in brief["state"]
    assert "policies_edicts" not in brief["state"]


def test_brief_events_lift_title_and_options_from_catalog_definition():
    observed = {
        "snapshot": "s",
        "state": {
            "date": "2200.01.01",
            "pending_events_described": {
                "known": True,
                "events": [{
                    "id": "1", "event_id": "action.13", "country": "0",
                    "definition": {"title": "Hostile Fleet", "definition_found": True,
                                    "options": [{"index": 0, "name": "OK"}, {"index": 1, "name": "Ignore"}]},
                }],
            },
        },
    }
    events = apply_observation_view(observed, "brief")["state"]["pending_events"]["events"]
    assert events[0]["player_event_id"] == "1"
    assert events[0]["title"] == "Hostile Fleet"
    assert events[0]["options"] == [{"index": 0, "name": "OK"}, {"index": 1, "name": "Ignore"}]
