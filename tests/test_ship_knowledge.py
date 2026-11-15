"""Ship knowledge list tools never invent numeric values."""
from stellaris_autopilot.ship_knowledge import (
    compile_ship_reference, list_ship_components, list_ship_sections, list_ship_sizes, list_live_ship_designs,
)


BUNDLE = {
    "schema": "qs-game-knowledge-v1",
    "game_version": "Cygnus v4.5.2",
    "bundle_revision": 2,
    "wiki_pages": [],
    "records": {
        "ship_sizes:corvette": {
            "category": "ship_sizes", "id": "corvette",
            "definition": {
                "class": "shipclass_military",
                "section_slots": {"mid": {"locator": "part1"}},
                "required_component_set": ["power_core", "ftl_components"],
                "prerequisites": ["tech_corvettes"],
                "max_hitpoints": 300,
                "max_speed": "@speed_very_fast",
            },
            "names": {"english": "Corvette"},
            "sources": ["common/ship_sizes/00_ship_sizes.txt"],
        },
        "section_templates:CORVETTE_MID_S3": {
            "category": "section_templates", "id": "CORVETTE_MID_S3",
            "definition": {
                "key": "CORVETTE_MID_S3",
                "ship_size": "corvette",
                "component_slot": [
                    {"name": "small_gun_01", "slot_size": "small", "template": "small_turret", "locator": "part1"},
                    {"name": "small_gun_02", "slot_size": "small", "template": "small_turret"},
                ],
            },
            "names": {},
            "sources": ["common/section_templates/corvette.txt"],
        },
        "component_templates:SMALL_RED_LASER": {
            "category": "component_templates", "id": "SMALL_RED_LASER",
            "definition": {
                "key": "SMALL_RED_LASER", "size": "small", "type": "instant",
                "prerequisites": ["tech_lasers_1"], "component_set": "RED_LASER",
                "tags": ["weapon_type_energy", "s_slot"],
                "resources": {"cost": {"alloys": 10}, "upkeep": {"energy": 0.2}},
            },
            "names": {"english": "Red Laser"},
            "sources": ["common/component_templates/00_weapons_energy.txt"],
        },
        "component_templates:SHIP_AURA_RANGE": {
            "category": "component_templates", "id": "SHIP_AURA_RANGE",
            "definition": {
                "key": "SHIP_AURA_RANGE", "size": "aux", "type": "utility",
                "prerequisites": ["tech_hidden"],
                "modifier": {"ship_weapon_range_mult": 0.1},
            },
            "names": {},
            "sources": [],
        },
    },
}


def test_list_ship_sizes_keeps_numbers_and_marks_symbolic_fields_unknown():
    listed = list_ship_sizes(BUNDLE)
    assert listed["status"] == "listed" and listed["bundle_revision"] == 2
    size = listed["ship_sizes"][0]
    assert size["id"] == "corvette" and size["max_hitpoints"] == 300
    assert size["max_speed"] is None and size["max_speed_known"] is False
    assert size["section_slots"] == [{"slot": "mid", "locator": "part1"}]
    assert size["required_component_sets"] == ["power_core", "ftl_components"]
    assert size["prerequisites"] == ["tech_corvettes"]


def test_list_ship_sections_and_components_include_effects_and_filters():
    sections = list_ship_sections(BUNDLE, "corvette")
    assert sections["status"] == "listed"
    assert sections["sections"][0]["component_slots"][0]["slot_size"] == "small"
    lasers = list_ship_components(BUNDLE, slot_size="small", query="laser")
    assert lasers["count"] == 1 and lasers["components"][0]["id"] == "SMALL_RED_LASER"
    assert lasers["components"][0]["resources"]["cost"]["alloys"] == 10
    assert lasers["components"][0]["prerequisites"] == ["tech_lasers_1"]
    # Without CSV, weapon combat stats stay unknown rather than invented.
    assert lasers["components"][0].get("weapon_stats_known") is False
    assert "min_damage" not in lasers["components"][0]
    aux = list_ship_components(BUNDLE, component_type="utility")
    assert aux["components"][0]["modifier"]["effects"]["ship_weapon_range_mult"] == 0.1


def test_weapon_csv_stats_are_merged_and_not_invented():
    stats = {"SMALL_RED_LASER": {"key": "SMALL_RED_LASER", "min_damage": 6.0, "max_damage": 16.0,
                                 "range": 40.0, "accuracy": 0.9, "weapon_stats_known": True,
                                 "source": "weapon_components.csv"}}
    bundle = {**BUNDLE, "ship_reference": compile_ship_reference(BUNDLE, weapon_stats=stats)}
    item = list_ship_components(bundle, query="SMALL_RED_LASER")["components"][0]
    assert item["min_damage"] == 6.0 and item["range"] == 40.0 and item["weapon_stats_known"] is True


def test_list_ship_designs_reports_unlocks_and_unknown_research():
    observation = {
        "snapshot": "abc",
        "state": {
            "date": "2200.01.01",
            "shipbuilding": {
                "designs_known": True,
                "designs_by_id": {
                    "3057": {
                        "id": "3057", "ship_size": "corvette",
                        "definition": {
                            "growth_stages": {"0": {
                                "ship_size": "corvette",
                                "section": {"template": "CORVETTE_MID_S3",
                                            "small_gun_01": "SMALL_RED_LASER"},
                            }},
                        },
                    },
                },
            },
            "research_status": {"completed": ["tech_lasers_1", "tech_corvettes"]},
        },
    }
    listed = list_live_ship_designs(observation, BUNDLE)
    assert listed["known"] is True and listed["designs"][0]["ship_size"] == "corvette"
    assert listed["designs"][0]["sections_known"] is True
    assert listed["designs"][0]["sections"][0]["slots"][0]["component"] == "SMALL_RED_LASER"
    unlocked = {item["id"] for item in listed["unlocked_components"]}
    locked = {item["id"] for item in listed["locked_components"]}
    assert "SMALL_RED_LASER" in unlocked
    assert "SHIP_AURA_RANGE" in locked
    unknown = list_live_ship_designs({"state": {"shipbuilding": {"designs_known": True, "designs_by_id": {}}}}, BUNDLE)
    assert unknown["unlocked_components_known"] is False
    assert unknown["unlocked_components"] == []
