"""System claims: observation, resolution and verification."""
import copy

import pytest

from stellaris_autopilot import saves
from stellaris_autopilot.claim_commands import (
    claim_request, native_claim_payload, observe_claims, resolve_claim_command, verify_claim_command)
from stellaris_autopilot import extended_native

DUMP = """
galactic_object={
    10={ name="a" claims={ { owner=0 date="2200.12.01" claims=2 } { owner=65 date="2200.11.01" claims=1 } } }
    11={ name="b" }
    12={ name="c" claims={ { owner=65 date="2200.11.01" claims=3 } } }
}
"""


def test_observation_reads_only_our_claims():
    assert observe_claims(saves.loads(DUMP), "0") == {"known": True, "by_system": {
        "10": {"claims": 2, "date": "2200.12.01"}}}
    assert observe_claims(saves.loads(DUMP.replace("claims=2", 'claims="x"')), "0")["known"] is False
    assert observe_claims(saves.loads("x=1"), "0")["known"] is False


def _state():
    return {"date": "2200.12.01", "country_id": "0", "empire_build": {}, "planets_by_id": {}, "fleets_by_id": {},
            "construction": {"known": True}, "resources": {"influence": 300.0, "energy": 100.0},
            "claims": {"known": True, "by_system": {"10": {"claims": 2, "date": "2200.12.01"}}}}


def _after(state, system="11", count=1, influence=40.0):
    after = copy.deepcopy(state)
    after["claims"]["by_system"][system] = {"claims": count, "date": "2200.12.01"}
    after["resources"]["influence"] -= influence
    return after


def test_new_and_stacked_claims_verify_with_influence_only():
    state = _state()
    command = resolve_claim_command(state, claim_request("add_system_claim", system_id="11"))
    assert command.current_claims == 0
    assert verify_claim_command(state, _after(state), command) is True
    assert extended_native.verify_extended(state, _after(state), command) is True
    assert verify_claim_command(state, _after(state, count=2), command) is False
    assert verify_claim_command(state, _after(state, influence=0.0), command) is False
    assert verify_claim_command(state, _after(state, system="12"), command) is False
    wrong = _after(state)
    wrong["resources"]["energy"] -= 1
    assert verify_claim_command(state, wrong, command) is False
    stacked = resolve_claim_command(state, claim_request("add_system_claim", system_id="10"))
    assert stacked.current_claims == 2
    assert verify_claim_command(state, _after(state, system="10", count=3), stacked) is True


def test_requests_and_payload_are_typed():
    with pytest.raises(ValueError):
        claim_request("add_system_claim", system_id="01")
    with pytest.raises(ValueError):
        claim_request("remove_claim", system_id="1")
    unknown = _state()
    unknown["claims"]["known"] = False
    with pytest.raises(ValueError, match="not exactly observable"):
        resolve_claim_command(unknown, claim_request("add_system_claim", system_id="11"))
    command = resolve_claim_command(_state(), claim_request("add_system_claim", system_id="11"))
    assert native_claim_payload(command, "b" * 32) == {"kind": "add_system_claim", "country_id": "0",
                                                       "token": "b" * 32, "system_id": "11"}
    assert extended_native.extended_payload(command, "b" * 32)["system_id"] == "11"


def test_to_dict_matches_what_the_native_worker_echoes():
    # direct_mcp._native_matches compares every to_dict() field with the worker
    # result (the canonical payload); an extra field made live 596 ambiguous.
    from stellaris_autopilot.direct_mcp import _native_matches
    command = resolve_claim_command(_state(), claim_request("add_system_claim", system_id="10"))
    echoed = {**native_claim_payload(command, "c" * 32), "status": "submitted"}
    assert _native_matches(echoed, "c" * 32, command.kind, command.to_dict()) is True
