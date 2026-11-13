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
      relation={ owner=0 country=16777222 contact=yes communications=yes OURS relation_current=-60
        diplo_action_dates={ } }
    }
  }
  8={ type=swarm }
  73={ type=nice_faction relations_manager={ relation={ owner=73 country=0 contact=yes closed_borders=yes } } }
  16777222={ type=default relations_manager={ relation={ owner=16777222 country=0 contact=yes communications=yes
    THEIRS trust=0 } } }
}
"""


def _text(ours="", theirs=""):
    return SAVE.replace("OURS", ours).replace("THEIRS", theirs)


def _state(text=None, date="2209.02.06"):
    return {"date": date, "country_id": "0", "metadata": {"campaign_id": "c1"},
            "diplomacy": observe_diplomacy(loads(_text() if text is None else text))}


def _closed(text=None):
    return (_text() if text is None else text).replace("hostile=yes relation_current",
                                                       "hostile=yes closed_borders=yes relation_current")


def test_observation_reads_our_side_and_the_targets_opinion_of_us():
    diplomacy = _state()["diplomacy"]
    assert diplomacy["known"] is True and set(diplomacy["by_country"]) == {"8", "73", "16777222"}
    fellowship = diplomacy["by_country"]["73"]
    assert fellowship["country_type"] == "nice_faction" and fellowship["communications"] is True
    assert fellowship["hostile"] is True and fellowship["closed_borders"] is False
    assert fellowship["is_rival"] is False and fellowship["embassy"] is False
    assert diplomacy["by_country"]["8"]["communications"] is False
    assert diplomacy["by_country"]["8"]["their_side"] == {"known": True, "modifiers": [], "relation": None}
    assert _state(_closed())["diplomacy"]["by_country"]["73"]["closed_borders"] is True
    empire = _state(_text("is_rival=yes embassy=yes",
                          "modifier={ modifier=opinion_insult start_date=2209.02.06 value=-50 decay=yes }"))
    entry = empire["diplomacy"]["by_country"]["16777222"]
    assert entry["is_rival"] is True and entry["embassy"] is True and entry["country_type"] == "default"
    assert entry["their_side"]["modifiers"] == ["opinion_insult"]


@pytest.mark.parametrize("bad", [
    SAVE.replace("owner=0 country=8", "owner=1 country=8"),
    SAVE.replace("country=8 contact=yes", "country=8 contact=maybe"),
    SAVE.replace("country=8 contact=yes", "country=73 contact=yes"),
    SAVE.replace("country=8 contact=yes", "country=0 contact=yes"),
    SAVE.replace("relations_manager={\n      relation", "relations_manager={} relations_manager={\n      relation"),
    SAVE.replace("OURS", "is_rival=maybe"),
])
def test_ambiguous_relations_are_not_observable(bad):
    assert _state(bad)["diplomacy"]["known"] is False


@pytest.mark.parametrize("target", ["0", "012", "-1", "4294967295", "7\neffect cash=1", True, 7.0])
def test_requests_need_a_canonical_other_country_handle(target):
    with pytest.raises(ValueError):
        diplomacy_request("close_borders", target_country_id=target)


def test_full_generation_handles_are_accepted():
    assert diplomacy_request("insult", target_country_id="16777222").target_country_id == "16777222"


@pytest.mark.parametrize("kind", ["declare_war", "embassy", "form_non_aggression_pact", "offer_peace"])
def test_only_auto_accepted_actions_are_supported(kind):
    with pytest.raises(ValueError):
        diplomacy_request(kind, target_country_id="73")


def test_resolve_needs_contact_and_a_real_change():
    state = _state()
    command = resolve_extended(state, diplomacy_request("close_borders", target_country_id="73"))
    assert extended_payload(command, "a" * 32) == {"kind": "close_borders", "country_id": "0", "token": "a" * 32,
                                                   "target_country_id": "73"}
    assert native_diplomacy_payload(command, "a" * 32)["target_country_id"] == "73"
    with pytest.raises(ValueError, match="already open"):
        resolve_diplomacy_command(state, diplomacy_request("open_borders", target_country_id="73"))
    with pytest.raises(ValueError, match="already closed"):
        resolve_diplomacy_command(_state(_closed()), diplomacy_request("close_borders", target_country_id="73"))
    with pytest.raises(ValueError, match="not our rival"):
        resolve_diplomacy_command(state, diplomacy_request("end_rivalry", target_country_id="16777222"))
    with pytest.raises(ValueError, match="already our rival"):
        resolve_diplomacy_command(_state(_text("is_rival=yes")),
                                  diplomacy_request("make_rival", target_country_id="16777222"))
    with pytest.raises(ValueError, match="no embassy"):
        resolve_diplomacy_command(state, diplomacy_request("recall_embassy", target_country_id="16777222"))
    resolve_diplomacy_command(state, diplomacy_request("insult", target_country_id="16777222"))
    with pytest.raises(ValueError, match="contact"):
        resolve_diplomacy_command(state, diplomacy_request("close_borders", target_country_id="99"))
    with pytest.raises(ValueError, match="country 0"):
        resolve_diplomacy_command({**state, "country_id": "1"},
                                  diplomacy_request("close_borders", target_country_id="73"))
    with pytest.raises(ValueError, match="observable"):
        resolve_diplomacy_command({**state, "diplomacy": {"known": False}},
                                  diplomacy_request("close_borders", target_country_id="73"))


def test_border_verification_needs_exactly_our_flag_flipped_on_the_same_day():
    before, after = _state(), _state(_closed())
    close = resolve_diplomacy_command(before, diplomacy_request("close_borders", target_country_id="73"))
    assert verify_extended(before, after, close) is True
    bookkeeping = _state(_closed().replace("diplo_action_dates={ }", 'diplo_action_dates={ close_borders="2209.02.06" }', 1))
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


def test_rivalry_verification_allows_only_the_rival_flag_and_its_date():
    before = _state()
    rival = resolve_diplomacy_command(before, diplomacy_request("make_rival", target_country_id="16777222"))
    after = _state(_text("is_rival=yes rival_date=2209.02.06"))
    assert verify_diplomacy_command(before, after, rival) is True
    assert verify_diplomacy_command(before, _state(_text("rival_date=2209.02.06")), rival) is False
    assert verify_diplomacy_command(before, _state(_text("is_rival=yes embassy=yes")), rival) is False
    assert verify_diplomacy_command(before, _state(_closed(_text("is_rival=yes"))), rival) is False
    end = resolve_diplomacy_command(after, diplomacy_request("end_rivalry", target_country_id="16777222"))
    assert verify_diplomacy_command(after, before, end) is True
    assert verify_diplomacy_command(after, after, end) is False


def test_embassy_recall_verification_needs_our_embassy_gone():
    before = _state(_text("embassy=yes"))
    recall = resolve_diplomacy_command(before, diplomacy_request("recall_embassy", target_country_id="16777222"))
    assert verify_diplomacy_command(before, _state(), recall) is True
    assert verify_diplomacy_command(before, before, recall) is False
    assert verify_diplomacy_command(before, _state(_text("is_rival=yes")), recall) is False


def test_insult_verification_needs_a_new_insult_opinion_and_no_change_of_ours():
    before = _state()
    insult = resolve_diplomacy_command(before, diplomacy_request("insult", target_country_id="16777222"))
    modifier = "modifier={ modifier=opinion_insult start_date=2209.02.06 value=-50 decay=yes }"
    after = _state(_text(theirs=modifier).replace("THEIRS", "").replace("trust=0", "trust=-5"))
    assert verify_diplomacy_command(before, after, insult) is True
    assert verify_diplomacy_command(before, before, insult) is False
    other = _state(_text(theirs="modifier={ modifier=opinion_rivalry start_date=2209.02.06 value=-50 }"))
    assert verify_diplomacy_command(before, other, insult) is False
    ours = _state(_text("is_rival=yes", modifier))
    assert verify_diplomacy_command(before, ours, insult) is False
    already = _state(_text(theirs=modifier))
    twice = resolve_diplomacy_command(already, diplomacy_request("insult", target_country_id="16777222"))
    assert verify_diplomacy_command(already, already, twice) is False
    assert verify_diplomacy_command(already, _state(_text(theirs=modifier + " " + modifier)), twice) is True
