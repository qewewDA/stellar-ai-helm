import copy

import pytest

from stellaris_autopilot.diplomacy_commands import (
    diplomacy_request, native_diplomacy_payload, observe_diplomacy, resolve_diplomacy_command,
    verify_diplomacy_command,
)
from stellaris_autopilot.extended_native import extended_payload, resolve_extended, verify_extended
from stellaris_autopilot.pdx import loads

SAVE = """
country={
  0={
    name="Us"
    type=default
    relations_manager={
      relation={ owner=0 country=8 contact=yes relation_current=-500 }
      relation={ owner=0 country=73 contact=yes communications=yes hostile=yes relation_current=-1000
        diplo_action_dates={ } }
    }
  }
  8={ type=swarm }
  73={ type=nice_faction relations_manager={ relation={ owner=73 country=0 contact=yes closed_borders=yes } } }
}
"""


def _state(text=SAVE, date="2209.02.06"):
    return {"date": date, "country_id": "0", "metadata": {"campaign_id": "c1"},
            "diplomacy": observe_diplomacy(loads(text))}


def _closed(text=SAVE):
    return text.replace("hostile=yes relation_current", "hostile=yes closed_borders=yes relation_current")


def test_observation_reads_only_our_side_of_each_relation():
    diplomacy = _state()["diplomacy"]
    assert diplomacy["known"] is True and set(diplomacy["by_country"]) == {"8", "73"}
    fellowship = diplomacy["by_country"]["73"]
    assert fellowship["country_type"] == "nice_faction" and fellowship["communications"] is True
    assert fellowship["hostile"] is True and fellowship["closed_borders"] is False
    assert diplomacy["by_country"]["8"]["communications"] is False
    assert _state(_closed())["diplomacy"]["by_country"]["73"]["closed_borders"] is True


@pytest.mark.parametrize("bad", [
    SAVE.replace("owner=0 country=8", "owner=1 country=8"),
    SAVE.replace("country=8 contact=yes", "country=8 contact=maybe"),
    SAVE.replace("country=8 contact=yes", "country=73 contact=yes"),
    SAVE.replace("country=8 contact=yes", "country=0 contact=yes"),
    SAVE.replace("relations_manager={\n      relation", "relations_manager={} relations_manager={\n      relation"),
])
def test_ambiguous_relations_are_not_observable(bad):
    assert _state(bad)["diplomacy"]["known"] is False


@pytest.mark.parametrize("target", ["0", "012", "-1", "16777216", "7\neffect cash=1", True, 7.0])
def test_requests_need_a_canonical_other_country_id(target):
    with pytest.raises(ValueError):
        diplomacy_request("close_borders", target_country_id=target)


def test_only_the_two_border_actions_are_supported():
    with pytest.raises(ValueError):
        diplomacy_request("declare_war", target_country_id="73")


def test_resolve_needs_contact_and_a_real_change():
    state = _state()
    command = resolve_extended(state, diplomacy_request("close_borders", target_country_id="73"))
    assert extended_payload(command, "a" * 32) == {"kind": "close_borders", "country_id": "0", "token": "a" * 32,
                                               "target_country_id": "73"}
    assert native_diplomacy_payload(command, "t1")["target_country_id"] == "73"
    with pytest.raises(ValueError, match="already open"):
        resolve_diplomacy_command(state, diplomacy_request("open_borders", target_country_id="73"))
    with pytest.raises(ValueError, match="already closed"):
        resolve_diplomacy_command(_state(_closed()), diplomacy_request("close_borders", target_country_id="73"))
    with pytest.raises(ValueError, match="contact"):
        resolve_diplomacy_command(state, diplomacy_request("close_borders", target_country_id="99"))
    with pytest.raises(ValueError, match="country 0"):
        resolve_diplomacy_command({**state, "country_id": "1"},
                                  diplomacy_request("close_borders", target_country_id="73"))
    with pytest.raises(ValueError, match="observable"):
        resolve_diplomacy_command({**state, "diplomacy": {"known": False}},
                                  diplomacy_request("close_borders", target_country_id="73"))


def test_verification_needs_exactly_our_flag_flipped_on_the_same_day():
    before, after = _state(), _state(_closed())
    close = resolve_diplomacy_command(before, diplomacy_request("close_borders", target_country_id="73"))
    assert verify_extended(before, after, close) is True
    bookkeeping = _state(_closed().replace("diplo_action_dates={ }", "diplo_action_dates={ close_borders=\"2209.02.06\" }"))
    assert verify_diplomacy_command(before, bookkeeping, close) is True
    assert verify_diplomacy_command(before, before, close) is False
    assert verify_diplomacy_command(before, _state(_closed(), date="2209.02.07"), close) is False
    other = _state(_closed().replace("relation_current=-500", "relation_current=-400"))
    assert verify_diplomacy_command(before, other, close) is False
    drift = _state(_closed().replace("relation_current=-1000", "relation_current=-990"))
    assert verify_diplomacy_command(before, drift, close) is False
    campaign = copy.deepcopy(after)
    campaign["metadata"]["campaign_id"] = "c2"
    assert verify_diplomacy_command(before, campaign, close) is False
    gone = _state(_closed().replace("relation={ owner=0 country=8 contact=yes relation_current=-500 }", ""))
    assert verify_diplomacy_command(before, gone, close) is False
    reopen = resolve_diplomacy_command(after, diplomacy_request("open_borders", target_country_id="73"))
    assert verify_diplomacy_command(after, before, reopen) is True
    assert verify_diplomacy_command(before, after, reopen) is False
    assert verify_diplomacy_command(before, after, object()) is False
