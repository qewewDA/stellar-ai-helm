"""Pending event popups: snapshot reading, text lookup and verification, without a game."""
import copy

import pytest

from stellaris_autopilot.event_commands import (
    EventCatalog, ResolvedEventCommand, describe_pending_events, event_request, native_event_payload,
    observe_pending_events, observed_effects, resolve_event_command, verify_event_command)
from stellaris_autopilot.pdx import loads


SAVE = """
date="2207.01.11"
last_event_id=46
player_event=
{
	id=12
	event="first_contact.1"
	date=	"2207.05.07"
	scope=
	{
		type=first_contact
		id=14
		opener_id=4294967295
		random={ 0 956680299 }
		random_allowed=yes
		from={ type=country id=8 opener_id=4294967295 random={ 0 3575597746 } random_allowed=yes }
	}
	country=0
}
player_event=
{
	id=13
	event="qs_test.1"
	date=	"2207.02.01"
	scope={ type=country id=0 }
	country=0
}
player_event=
{
	id=15
	event="qs_test.1"
	scope={ type=country id=8 }
	country=8
}
open_player_event_selection_history=
{
	selected=
	{

		{
			player_event=1
			human=-1
			option=0
		}
	}
}
"""

def _root(text=SAVE):
    return loads(text)


def _state(text=SAVE):
    return {"date": "2207.01.11", "country_id": "0", "metadata": {"campaign_id": "c"},
            "resources": {"energy": 100.0, "unity": 5.0}, "pending_events": observe_pending_events(_root(text))}


def test_pending_events_follow_the_save_writer_blocks():
    observed = observe_pending_events(_root())
    assert observed["known"] is True and set(observed["by_id"]) == {"12", "13", "15"}
    assert observed["by_id"]["12"] == {"id": "12", "event_id": "first_contact.1", "country": "0",
                                       "auto_resolve_date": "2207.05.07",
                                       "scope": {"type": "first_contact", "id": "14"}}
    assert observed["by_id"]["15"]["auto_resolve_date"] is None and observed["by_id"]["15"]["country"] == "8"
    assert observed["selections"] == [{"player_event": "1", "human": -1, "option": 0}]


@pytest.mark.parametrize("text", [
    SAVE[:SAVE.index("open_player_event_selection_history")],
    SAVE.replace("id=13", "id=12"),
    SAVE.replace('event="qs_test.1"\n\tdate', 'event="bad id"\n\tdate'),
    SAVE.replace("id=13", "id=-1"),
    SAVE.replace("\tcountry=0\n}\nplayer_event=\n{\n\tid=13", "}\nplayer_event=\n{\n\tid=13"),
    SAVE.replace('"2207.02.01"', '"soon"'),
    SAVE.replace("option=0", "option=first"),
])
def test_unprovable_pending_events_stay_unknown(text):
    assert observe_pending_events(_root(text))["known"] is False


def test_no_open_events_is_known_empty_only_after_the_history_section():
    assert observe_pending_events(_root('date="2207.01.11"\nopen_player_event_selection_history={\n}\n')) == {
        "known": True, "by_id": {}, "selections": []}
    assert observe_pending_events(_root('date="2207.01.11"\n'))["known"] is False


@pytest.fixture
def game_dir(tmp_path):
    root = tmp_path / "game"
    (root / "events").mkdir(parents=True)
    (root / "events" / "qs_test.txt").write_text("""
namespace = qs_test
country_event = {
\tid = qs_test.1
\ttitle = qs_test.1.name
\tdesc = qs_test.1.desc
\tdesc = { trigger = { has_country_flag = x } text = qs_test.1.desc.alt }
\tpicture = GFX_evt_ship_in_orbit
\tis_triggered_only = yes
\toption = {
\t\tname = qs_test.1.a
\t\tadd_resource = { energy = 50 }
\t}
\toption = {
\t\tname = qs_test.1.b
\t\ttrigger = { has_country_flag = y }
\t\tcustom_tooltip = qs_test.1.b.tooltip
\t\tadd_resource = { unity = 10 }
\t}
}
""", encoding="utf-8")
    for language, title in (("english", "Signal"), ("simp_chinese", "\u4fe1\u53f7")):
        folder = root / "localisation" / language
        folder.mkdir(parents=True)
        (folder / f"qs_l_{language}.yml").write_text(
            f'\ufeffl_{language}:\n qs_test.1.name:0 "\u00a7Y{title}\u00a7!"\n qs_test.1.desc:0 "Gain $qs_energy$."\n'
            ' qs_energy:0 "\u00a3energy\u00a3 energy"\n qs_test.1.a:0 "Accept"\n', encoding="utf-8")
    (root / "localisation" / "english" / "qs_more_l_english.yml").write_text(
        'l_english:\n qs_test.1.b:0 "Decline"\n qs_test.1.b.tooltip:0 "Costs nothing"\n', encoding="utf-8")
    return root


def test_catalog_resolves_definition_options_and_language(game_dir, tmp_path):
    user = tmp_path / "user"
    user.mkdir()
    (user / "settings.txt").write_text('language="l_simp_chinese"\n', encoding="utf-8")
    catalog = EventCatalog.for_user_dir(game_dir, user)
    assert catalog.language == "simp_chinese"
    detail = catalog.describe("qs_test.1")
    assert detail["definition_found"] is True and detail["event_type"] == "country_event"
    assert detail["title"]["text"] == "\u4fe1\u53f7"
    assert detail["descriptions"][0]["text"] == "Gain energy."
    assert detail["descriptions"][1] == {"key": "qs_test.1.desc.alt", "text": None, "conditional": True}
    assert detail["flags"]["is_triggered_only"] is True and detail["requires_option"] is True
    first, second = detail["options"]
    assert (first["index"], first["name"]["text"], first["conditional"]) == (0, "Accept", False)
    assert first["effect_keys"] == ["add_resource"]
    assert (second["index"], second["name"]["text"], second["conditional"]) == (1, "Decline", True)
    assert second["custom_tooltips"][0]["text"] == "Costs nothing"
    assert catalog.describe("missing.1")["definition_found"] is False


def test_described_pending_events_keep_unknown_definitions_visible(game_dir):
    result = describe_pending_events(_state(), EventCatalog(game_dir))
    assert result["known"] is True and [event["player_event_id"] for event in result["events"]] == ["12", "13"]
    assert result["other_country_event_count"] == 1 and result["country_id"] == "0"
    assert result["events"][0]["definition"]["definition_found"] is False
    assert result["events"][1]["definition"]["title"]["text"] == "Signal"
    assert describe_pending_events({"pending_events": {"known": False}}, None)["known"] is False


@pytest.mark.parametrize("option", [64, -1, "01", "1.0", True, None, "x"])
def test_event_request_rejects_unbounded_or_non_integer_options(option):
    with pytest.raises(ValueError):
        event_request("choose_event_option", player_event_id="12", option_index=option)


def test_event_request_rejects_bad_ids_and_kinds():
    for identifier in ("-1", "012", "4294967296", 12, "2147483648"):
        with pytest.raises(ValueError):
            event_request("choose_event_option", player_event_id=identifier, option_index=0)
    with pytest.raises(ValueError):
        event_request("dismiss_event", player_event_id="12", option_index=0)


def test_resolution_binds_the_observed_event_id_into_the_native_payload():
    request = event_request("choose_event_option", player_event_id="13", option_index=1)
    command = resolve_event_command(_state(), request)
    assert command == ResolvedEventCommand(request, "qs_test.1")
    assert native_event_payload(command, "a" * 32) == {
        "kind": "choose_event_option", "country_id": "0", "token": "a" * 32,
        "player_event_id": "13", "option_index": "1", "event_id": "qs_test.1"}
    with pytest.raises(ValueError):
        resolve_event_command(_state(), event_request("choose_event_option", player_event_id="14", option_index=0))
    with pytest.raises(ValueError, match="another country"):
        resolve_event_command(_state(), event_request("choose_event_option", player_event_id="15", option_index=0))
    unknown = _state()
    unknown["pending_events"]["known"] = False
    with pytest.raises(ValueError):
        resolve_event_command(unknown, request)


def _after(before, removed="13", **changes):
    after = copy.deepcopy(before)
    del after["pending_events"]["by_id"][removed]
    after.update(changes)
    return after


def test_verification_needs_the_chosen_event_gone_on_the_same_day_and_others_unchanged():
    before = _state()
    command = resolve_event_command(before, event_request("choose_event_option", player_event_id="13", option_index=0))
    assert verify_event_command(before, _after(before), command) is True
    follow_up = _after(before)
    follow_up["pending_events"]["by_id"]["14"] = {"id": "14", "event_id": "qs_test.2", "country": "0",
                                                  "auto_resolve_date": None, "scope": None}
    assert verify_event_command(before, follow_up, command) is True
    assert verify_event_command(before, copy.deepcopy(before), command) is False
    assert verify_event_command(before, _after(before, date="2207.01.12"), command) is False
    other_gone = _after(before)
    del other_gone["pending_events"]["by_id"]["12"]
    assert verify_event_command(before, other_gone, command) is False
    other_changed = _after(before)
    other_changed["pending_events"]["by_id"]["12"]["auto_resolve_date"] = "2207.06.01"
    assert verify_event_command(before, other_changed, command) is False
    unknown = _after(before)
    unknown["pending_events"]["known"] = False
    assert verify_event_command(before, unknown, command) is False
    assert verify_event_command(before, _after(before), object()) is False
    recorded = _after(before)
    recorded["pending_events"]["selections"].append({"player_event": "13", "human": 0, "option": 0})
    assert verify_event_command(before, recorded, command) is True
    wrong_option = _after(before)
    wrong_option["pending_events"]["selections"].append({"player_event": "13", "human": 0, "option": 1})
    assert verify_event_command(before, wrong_option, command) is False
    rewritten = _after(before)
    rewritten["pending_events"]["selections"][0]["option"] = 1
    assert verify_event_command(before, rewritten, command) is False


def test_observed_effects_report_resources_follow_ups_and_changed_sections():
    before = _state()
    after = _after(before)
    after["resources"]["energy"] = 150.0
    after["pending_events"]["by_id"]["14"] = {"id": "14", "event_id": "qs_test.2"}
    after["country_controls"] = {"changed": True}
    effects = observed_effects(before, after)
    assert effects == {"resources": {"energy": {"before": 100.0, "after": 150.0}}, "new_selections": [],
                       "new_pending_events": ["14"], "removed_pending_events": ["13"],
                       "changed_sections": ["country_controls"]}


@pytest.mark.parametrize("country", ["8", "16777222", None])
def test_other_countries_events_are_never_chosen(country):
    state = _state()
    entry = state["pending_events"]["by_id"]["12"]
    entry["country"] = country
    with pytest.raises(ValueError, match="another country"):
        resolve_event_command(state, event_request("choose_event_option", player_event_id="12", option_index=0))
    from stellaris_autopilot.extended_native import resolve_extended
    with pytest.raises(ValueError, match="another country"):
        resolve_extended(state, event_request("choose_event_option", player_event_id="12", option_index=0))
    listed = describe_pending_events(state, None)
    assert "12" not in [event["player_event_id"] for event in listed["events"]]
    assert listed["other_country_event_count"] == 2


def test_events_are_refused_when_the_snapshot_is_not_the_local_player():
    state = _state()
    state["country_id"] = "8"
    with pytest.raises(ValueError, match="country"):
        resolve_event_command(state, event_request("choose_event_option", player_event_id="12", option_index=0))
