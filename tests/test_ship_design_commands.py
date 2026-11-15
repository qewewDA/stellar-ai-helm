"""Assign a ship design: observation, resolution and verification."""
import copy

import pytest

from stellaris_autopilot import saves
from stellaris_autopilot.ship_design_commands import (
    native_ship_design_payload, observe_ships, resolve_ship_design_command,
    ship_design_request, verify_ship_design_command)
from stellaris_autopilot import extended_native

DUMP = """
ships={
    3={ fleet=3 ship_design_implementation={ design=49 upgrade=3057 growth_stage=0 } }
    1={ fleet=1 ship_design_implementation={ design=3041 upgrade=4294967295 growth_stage=0 } }
}
"""


def test_observation_reads_designs_on_our_fleets():
    fleets = [{"id": "3"}, {"id": "1"}]
    observed = observe_ships(saves.loads(DUMP), "0", fleets)
    assert observed["known"] is True
    assert observed["by_id"]["3"] == {"id": "3", "fleet_id": "3", "design_id": "49", "upgrade_id": "3057"}
    assert observed["by_id"]["1"] == {"id": "1", "fleet_id": "1", "design_id": "3041", "upgrade_id": None}
    assert observe_ships(saves.loads(DUMP), "0", [{"id": "9"}]) == {"known": True, "by_id": {}}


def _state():
    return {
        "date": "2200.12.26", "country_id": "0", "empire_build": {}, "planets_by_id": {},
        "construction": {"known": True},
        "resources": {"alloys": 500.0, "minerals": 200.0, "energy": 100.0, "influence": 50.0, "food": 10.0},
        "shipbuilding": {"designs_known": True, "designs_by_id": {"3057": {"id": "3057", "ship_size": "corvette"}}},
        "ships": {"known": True, "by_id": {
            "3": {"id": "3", "fleet_id": "3", "design_id": "49", "upgrade_id": "3057"},
            "1": {"id": "1", "fleet_id": "1", "design_id": "3041", "upgrade_id": None},
        }},
    }


def test_assigning_a_new_design_verifies_only_that_ship():
    state = _state()
    command = resolve_ship_design_command(state, ship_design_request("set_ship_design", ship_id="3", design_id="3057"))
    assert command.current_design_id == "49" and command.current_upgrade_id == "3057"
    after = copy.deepcopy(state)
    after["ships"]["by_id"]["3"]["design_id"] = "3057"
    after["ships"]["by_id"]["3"]["upgrade_id"] = None
    after["resources"]["alloys"] -= 40.0
    assert verify_ship_design_command(state, after, command) is True
    assert extended_native.verify_extended(state, after, command) is True
    after["resources"]["food"] -= 1
    assert verify_ship_design_command(state, after, command) is False
    with pytest.raises(ValueError, match="already uses"):
        resolve_ship_design_command(after, ship_design_request("set_ship_design", ship_id="3", design_id="3057"))


def test_requests_and_payload_are_typed():
    with pytest.raises(ValueError):
        ship_design_request("set_ship_design", ship_id="03", design_id="3057")
    with pytest.raises(ValueError):
        ship_design_request("auto_ship_design", ship_id="3", design_id="3057")
    command = resolve_ship_design_command(_state(), ship_design_request("set_ship_design", ship_id="3", design_id="3057"))
    assert native_ship_design_payload(command, "b" * 32) == {
        "kind": "set_ship_design", "country_id": "0", "token": "b" * 32, "ship_id": "3", "design_id": "3057",
    }
    from stellaris_autopilot.direct_mcp import _native_matches
    echoed = {**native_ship_design_payload(command, "c" * 32), "status": "submitted"}
    assert _native_matches(echoed, "c" * 32, command.kind, command.to_dict()) is True
