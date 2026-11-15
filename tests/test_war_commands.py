"""Declare war and offer peace: observation, resolution and verification."""
import copy

import pytest

from stellaris_autopilot import saves
from stellaris_autopilot.war_commands import (
    native_war_payload, observe_wars, resolve_war_command, verify_war_command, war_request)
from stellaris_autopilot import extended_native

DUMP = """
war={
    7={
        start_date="2200.06.01"
        attackers={ { country=0 } }
        defenders={ { country=66 } }
        attacker_war_goal={ type=wg_conquest }
    }
}
"""


def test_observation_reads_wars_we_are_in():
    observed = observe_wars(saves.loads(DUMP), "0")
    assert observed["known"] is True
    assert observed["by_id"]["7"] == {
        "id": "7", "attackers": ["0"], "defenders": ["66"],
        "attacker_war_goal": "wg_conquest", "we_attack": True,
    }
    assert observe_wars(saves.loads("x=1"), "0") == {"known": True, "by_id": {}}
    assert observe_wars(saves.loads(DUMP.replace("type=wg_conquest", "type=nope")), "0")["known"] is False


def _state(wars=None, proposals=None):
    return {
        "date": "2200.12.26", "country_id": "0",
        "wars": {"known": True, "by_id": wars or {}},
        "diplomacy": {"known": True, "by_country": {
            "66": {"our_proposals": list(proposals or [])}}},
    }


def test_declare_war_verifies_a_new_matching_war():
    state = _state()
    command = resolve_war_command(state, war_request("declare_war", target_country_id="66", war_goal="wg_conquest"))
    after = copy.deepcopy(state)
    after["wars"]["by_id"]["7"] = {
        "id": "7", "attackers": ["0"], "defenders": ["66"],
        "attacker_war_goal": "wg_conquest", "we_attack": True,
    }
    assert verify_war_command(state, after, command) is True
    assert extended_native.verify_extended(state, after, command) is True
    wrong = copy.deepcopy(after)
    wrong["wars"]["by_id"]["7"]["attacker_war_goal"] = "wg_humiliation"
    assert verify_war_command(state, wrong, command) is False
    already = _state(wars=after["wars"]["by_id"])
    with pytest.raises(ValueError, match="already at war"):
        resolve_war_command(already, war_request("declare_war", target_country_id="66", war_goal="wg_conquest"))


def test_offer_peace_verifies_a_pending_proposal_and_unchanged_wars():
    wars = {"7": {"id": "7", "attackers": ["0"], "defenders": ["66"],
                  "attacker_war_goal": "wg_conquest", "we_attack": True}}
    state = _state(wars=wars)
    command = resolve_war_command(state, war_request(
        "offer_peace", target_country_id="66", terms="status_quo", war_id="7"))
    after = copy.deepcopy(state)
    after["diplomacy"]["by_country"]["66"]["our_proposals"] = [{"action": "action_offer_peace"}]
    assert verify_war_command(state, after, command) is True
    ended = copy.deepcopy(after)
    ended["wars"]["by_id"] = {}
    assert verify_war_command(state, ended, command) is False


def test_requests_and_payload_are_typed():
    with pytest.raises(ValueError):
        war_request("declare_war", target_country_id="01", war_goal="wg_conquest")
    with pytest.raises(ValueError):
        war_request("declare_war", target_country_id="66", war_goal="conquest")
    with pytest.raises(ValueError):
        war_request("offer_peace", target_country_id="66", terms="white_peace", war_id="7")
    command = resolve_war_command(_state(), war_request(
        "declare_war", target_country_id="66", war_goal="wg_humiliation"))
    assert native_war_payload(command, "b" * 32) == {
        "kind": "declare_war", "country_id": "0", "token": "b" * 32,
        "target_country_id": "66", "war_goal": "wg_humiliation",
    }
    from stellaris_autopilot.direct_mcp import _native_matches
    echoed = {**native_war_payload(command, "c" * 32), "status": "submitted"}
    assert _native_matches(echoed, "c" * 32, command.kind, command.to_dict()) is True
