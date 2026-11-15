"""Offline checks for typed policy/edict requests, observation and verification."""
from copy import deepcopy

import pytest

from stellaris_autopilot import country_commands as cc
from stellaris_autopilot.extended_native import (RESOLVED_TYPES, REQUEST_TYPES, extended_payload,
                                                 resolve_extended, verify_extended)
from stellaris_autopilot.direct import NATIVE_ACTIONS
from stellaris_autopilot.pdx import loads


COUNTRY = """
c={
    active_policies={
        { policy="diplomatic_stance" selected="diplo_stance_expansionist" }
        { policy="economic_policy" selected="economic_policy_balanced" }
        { policy="trade_policy" selected="trade_policy_default" date="2195.01.01" }
    }
    policy_flags={ "diplo_stance_expansionist" }
    %s
}
"""
EDICTS = 'edicts={ { edict="farming_subsidies" date="2201.01.01" start_date="2199.06.01" } }'
POLICY_RECORD = {"status": "found", "definition": {"option": [
    {"name": "economic_policy_civilian"}, {"name": "economic_policy_balanced"}, {"name": "economic_policy_military"}]}}
EDICT_RECORD = {"status": "found", "definition": {"resources": {
    "category": "edicts", "cost": {"unity": 10, "multiplier": "value:edict_size_effect"},
    "upkeep": {"unity": 10, "multiplier": "value:edict_size_effect"}}}}
TOKEN = "c" * 32


def _controls(extra=""):
    return cc.observe_country_controls(loads(COUNTRY % extra).get("c"))


def _state(extra=EDICTS, date="2200.01.01", **resources):
    stock = {"energy": 200.0, "minerals": 300.0, "influence": 100.0, "unity": 50.0}
    stock.update(resources)
    return {"country_id": "0", "date": date, "resources": stock, "country_controls": _controls(extra),
            "empire_build": {"government": {"known": True, "value": "gov"}, "ethos": {"known": True, "value": "e"},
                             "traditions": {"known": False, "value": None},
                             "ascension_perks": {"known": False, "value": None}}}


def _policy(option="economic_policy_civilian", policy="economic_policy"):
    return cc.country_request("set_policy", cc.definition_from_record("set_policy", POLICY_RECORD),
                              policy=policy, option=option)


def _edict(kind="activate_edict", edict="fortify_the_border"):
    definition = cc.definition_from_record(kind, EDICT_RECORD) if kind == "activate_edict" else None
    return cc.country_request(kind, definition, edict=edict)


# --------------------------------------------------------------------------- observation


def test_observation_reads_policies_dates_and_edicts():
    controls = _controls(EDICTS)
    assert controls["known"] is True
    assert controls["policies"]["economic_policy"] == {"selected": "economic_policy_balanced", "date": None}
    assert controls["policies"]["trade_policy"]["date"] == "2195.01.01"
    assert controls["edicts"] == {"farming_subsidies": {"date": "2201.01.01", "start_date": "2199.06.01",
                                                        "perpetual": False}}


def test_missing_edicts_block_is_empty_only_when_policies_were_serialized():
    assert _controls("")["edicts"] == {} and _controls("")["known"] is True
    assert cc.observe_country_controls(loads("c={ name=x }").get("c"))["known"] is False


@pytest.mark.parametrize("extra", [
    EDICTS + " " + EDICTS,
    'edicts={ { edict="a" date="2201.01.01" } }',
    'edicts={ { edict="a" date="2201.01.01" start_date="2200.01.01" bogus=1 } }',
    'edicts={ { edict="a" date="2201.01.01" start_date="2200.01.01" } { edict="a" date="2201.01.01" start_date="2200.01.01" } }',
    'edicts={ }',
    'active_policies={ }',
])
def test_ambiguous_shapes_are_unknown(extra):
    assert _controls(extra)["known"] is False


def test_duplicate_policy_entry_is_unknown():
    text = COUNTRY.replace('{ policy="trade_policy"', '{ policy="economic_policy" selected="x" } { policy="trade_policy"')
    assert cc.observe_country_controls(loads(text % "").get("c"))["known"] is False


# --------------------------------------------------------------------------- requests


def test_registry_includes_the_three_typed_actions():
    assert {"set_policy", "activate_edict", "deactivate_edict"} <= NATIVE_ACTIONS
    assert cc.CountryRequest in REQUEST_TYPES and cc.ResolvedCountryCommand in RESOLVED_TYPES


@pytest.mark.parametrize("kind,payload", [
    ("set_policy", {"policy": "economic_policy"}),
    ("set_policy", {"policy": "Economic", "option": "x"}),
    ("activate_edict", {"edict": "a b"}),
    ("activate_edict", {"edict": "x", "planet_id": "1"}),
    ("adopt_tradition", {"edict": "x"}),
])
def test_malformed_requests_are_rejected(kind, payload):
    with pytest.raises(ValueError):
        cc.country_request(kind, **payload)


def test_definition_facts_come_from_exact_knowledge_records():
    assert cc.definition_from_record("set_policy", POLICY_RECORD).options[0] == "economic_policy_civilian"
    edict = cc.definition_from_record("activate_edict", EDICT_RECORD)
    assert edict.base_cost == (("unity", 10.0),) and edict.scaled is True
    for record in ({"status": "not_found"}, {"status": "found", "definition": {"resources": {"cost": {"unity": "@x"}}}},
                   {"status": "found", "definition": {"resources": {"cost": {"unity": 5, "multiplier": "value:other"}}}}):
        with pytest.raises(ValueError):
            cc.definition_from_record("activate_edict", record)
    with pytest.raises(ValueError):
        cc.country_request("set_policy", {"options": ("x",)}, policy="a", option="x")


# --------------------------------------------------------------------------- resolution (pre-submit rejection)


def test_policy_resolution_and_payload_match_the_native_canonical_request():
    command = resolve_extended(_state(), _policy())
    assert command.current_option == "economic_policy_balanced"
    assert extended_payload(command, TOKEN) == {
        "kind": "set_policy", "country_id": "0", "token": TOKEN, "policy": "economic_policy",
        "option": "economic_policy_civilian", "current_option": "economic_policy_balanced"}


@pytest.mark.parametrize("request_factory,match", [
    (lambda: _policy(option="economic_policy_balanced"), "already selected"),
    (lambda: _policy(option="diplo_stance_expansionist"), "not a known option"),
    (lambda: cc.country_request("set_policy", None, policy="economic_policy", option="economic_policy_civilian"),
     "not a known option"),
    (lambda: _policy(policy="slavery", option="economic_policy_civilian"), "not among"),
])
def test_illegal_policy_changes_are_rejected_before_submission(request_factory, match):
    with pytest.raises(ValueError, match=match):
        resolve_extended(_state(), request_factory())


def test_policy_cooldown_uses_ten_game_years():
    definition = cc.CountryDefinition(options=("trade_policy_default", "trade_policy_wealth_creation"))
    request = cc.country_request("set_policy", definition, policy="trade_policy", option="trade_policy_wealth_creation")
    with pytest.raises(ValueError, match="10 years"):
        resolve_extended(_state(date="2204.12.30"), request)
    assert resolve_extended(_state(date="2205.01.01"), request).current_option == "trade_policy_default"


def test_edict_resolution_rejects_active_inactive_unaffordable_and_unknown_cost():
    assert extended_payload(resolve_extended(_state(), _edict()), TOKEN) == {
        "kind": "activate_edict", "country_id": "0", "token": TOKEN, "edict": "fortify_the_border"}
    with pytest.raises(ValueError, match="already active"):
        resolve_extended(_state(), _edict(edict="farming_subsidies"))
    with pytest.raises(ValueError, match="not active"):
        resolve_extended(_state(), _edict("deactivate_edict"))
    state = _state()
    state["resources"].pop("unity")
    with pytest.raises(ValueError, match="unobservable"):
        resolve_extended(state, _edict())
    with pytest.raises(ValueError, match="base cost"):
        resolve_extended(_state(), cc.country_request("activate_edict", None, edict="fortify_the_border"))
    state = _state()
    state["country_controls"] = {"known": False, "policies": {}, "edicts": {}}
    with pytest.raises(ValueError, match="not exactly observable"):
        resolve_extended(state, _edict())
    state = _state()
    state["country_id"] = "1"
    with pytest.raises(ValueError, match="country 0"):
        resolve_extended(state, _edict())


# --------------------------------------------------------------------------- verification




def _after_policy(date="2200.01.01", option="economic_policy_civilian", **resources):
    state = _state(**resources)
    state["country_controls"]["policies"]["economic_policy"] = {"selected": option, "date": date}
    return state


def test_policy_verification_requires_exact_selection_date_and_no_other_change():
    before = _state()
    command = resolve_extended(before, _policy())
    assert verify_extended(before, _after_policy(), command) is True
    assert verify_extended(before, _after_policy(date=None), command) is False
    assert verify_extended(before, _after_policy(option="economic_policy_military"), command) is False
    assert verify_extended(before, _after_policy(influence=99.0), command) is False
    other = _after_policy()
    other["country_controls"]["policies"]["diplomatic_stance"]["selected"] = "diplo_stance_belligerent"
    assert verify_extended(before, other, command) is False
    edicts = _after_policy()
    edicts["country_controls"]["edicts"] = {}
    assert verify_extended(before, edicts, command) is False
    later = _after_policy()
    later["date"] = "2200.01.02"
    assert verify_extended(before, later, command) is False
    choices = _after_policy()
    choices["empire_build"]["ethos"] = {"known": True, "value": "changed"}
    assert verify_extended(before, choices, command) is False
    assert verify_extended(before, deepcopy(before), command) is False


def _after_edict(unity=40.0, start="2200.01.01", **resources):
    state = _state(unity=unity, **resources)
    state["country_controls"]["edicts"]["fortify_the_border"] = {
        "date": "2200.01.01", "start_date": start, "perpetual": False}
    return state


def test_edict_activation_requires_exact_scaled_unity_payment():
    before = _state()
    command = resolve_extended(before, _edict())
    assert verify_extended(before, _after_edict(), command) is True
    assert verify_extended(before, _after_edict(unity=39.0), command) is True  # size factor 1.1
    assert verify_extended(before, _after_edict(unity=45.0), command) is True  # size factor 0.5 (live 4.5.2)
    assert verify_extended(before, _after_edict(unity=50.0), command) is False  # no payment
    assert verify_extended(before, _after_edict(influence=90.0), command) is False  # wrong resource
    assert verify_extended(before, _after_edict(energy=210.0), command) is False  # a gain
    assert verify_extended(before, _after_edict(start="2199.12.30"), command) is False
    missing = _after_edict()
    missing["country_controls"]["edicts"].pop("farming_subsidies")
    assert verify_extended(before, missing, command) is False


def test_unscaled_edict_cost_must_match_exactly():
    definition = cc.CountryDefinition(base_cost=(("unity", 10.0),), scaled=False)
    assert cc.edict_cost_matches({"unity": 10.0}, definition) is True
    assert cc.edict_cost_matches({"unity": 11.0}, definition) is False
    assert cc.edict_cost_matches({}, definition) is False
    assert cc.edict_cost_matches({"unity": 10.0}, None) is False


def test_edict_deactivation_requires_removal_without_refund():
    before = _state()
    command = resolve_extended(before, _edict("deactivate_edict", "farming_subsidies"))
    after = _state()
    after["country_controls"]["edicts"] = {}
    assert verify_extended(before, after, command) is True
    refunded = _state(unity=60.0)
    refunded["country_controls"]["edicts"] = {}
    assert verify_extended(before, refunded, command) is False
    assert verify_extended(before, _state(), command) is False


def test_payload_rejects_tampered_resolved_commands():
    command = resolve_extended(_state(), _policy())
    with pytest.raises(ValueError):
        extended_payload(cc.ResolvedCountryCommand(command.request, current_option="economic_policy_civilian"), TOKEN)
    with pytest.raises(ValueError):
        extended_payload(command, "not-a-token")
    with pytest.raises(ValueError):
        extended_payload(cc.ResolvedCountryCommand(_edict(), current_option="x"), TOKEN)


# The exact block written by the live 4.5.2 game after activating fortify_the_border
# (2200.01.01 United Nations of Earth start, size factor 0.5: unity 50 -> 45).
LIVE_PERPETUAL = (
    'edicts=\n\t\t{\n\t\t\t\n\t\t\t{\n\t\t\t\tedict="fortify_the_border"\n'
    '\t\t\t\tdate=\t\t\t\t"-5070.07.21"\n\t\t\t\tperpetual=yes\n'
    '\t\t\t\tstart_date=\t\t\t\t"2200.01.01"\n\t\t\t}\n \n\t\t}'
)


def test_live_perpetual_edict_with_sentinel_date_is_observed():
    controls = _controls(LIVE_PERPETUAL)
    assert controls["known"] is True and len(controls["policies"]) == 3
    assert controls["edicts"] == {"fortify_the_border": {
        "date": "-5070.07.21", "start_date": "2200.01.01", "perpetual": True}}


@pytest.mark.parametrize("entry", [
    'edicts={ { edict="a" date="-5070.07.21" start_date="2200.01.01" } }',  # sentinel needs perpetual
    'edicts={ { edict="a" date="-5070.07.21" perpetual=yes start_date="-2200.01.01" } }',
    'edicts={ { edict="a" date="-5070.13.21" perpetual=yes start_date="2200.01.01" } }',
    'edicts={ { edict="a" date="-5070.07.21" perpetual=no start_date="2200.01.01" } }',
    'edicts={ { edict="a" date="--5070.07.21" perpetual=yes start_date="2200.01.01" } }',
])
def test_sentinel_dates_are_accepted_only_for_perpetual_end_dates(entry):
    assert _controls(entry)["known"] is False


def test_negative_policy_dates_stay_unobservable():
    text = COUNTRY.replace('date="2195.01.01"', 'date="-5070.07.21"')
    assert cc.observe_country_controls(loads(text % "").get("c"))["known"] is False


def test_live_activation_with_half_size_factor_verifies_and_deactivation_resolves():
    before = _state(extra="")
    command = resolve_extended(before, _edict())
    after = _state(extra=LIVE_PERPETUAL, unity=45.0)
    assert verify_extended(before, after, command) is True
    assert verify_extended(before, _state(extra=LIVE_PERPETUAL, unity=45.0, influence=99.0), command) is False
    assert verify_extended(before, _state(extra=LIVE_PERPETUAL, unity=45.0, energy=201.0), command) is False
    removal = resolve_extended(after, _edict("deactivate_edict"))
    assert removal.kind == "deactivate_edict"
    cleared = _state(extra="", unity=45.0)
    assert verify_extended(after, cleared, removal) is True
    assert verify_extended(after, _state(extra="", unity=50.0), removal) is False


def test_size_factor_must_be_shared_by_every_cost_key():
    definition = cc.CountryDefinition(base_cost=(("energy", 20.0), ("unity", 10.0)), scaled=True)
    assert cc.edict_cost_matches({"energy": 10.0, "unity": 5.0}, definition) is True
    assert cc.edict_cost_matches({"energy": 20.0, "unity": 5.0}, definition) is False
    assert cc.edict_cost_matches({"unity": 5.0}, definition) is False
    assert cc.edict_cost_matches({"energy": 0.0, "unity": 0.0}, definition) is False


TRADE_POLICY = {"status": "found", "definition": {"option": [
    {"name": "trade_policy_default", "potential": {"is_worker_coop_empire": "no"}},
    {"name": "trade_policy_consumer_goods", "potential": {
        "OR": {"has_federation": "no"}, "has_tradition": "tr_mercantile_adaptive_economic_policies",
        "is_worker_coop_empire": "no"}},
    {"name": "trade_policy_other", "potential": {"NOT": {"has_tradition": "tr_x"}}},
]}}


def test_trade_policy_options_list_their_required_traditions():
    from stellaris_autopilot.country_commands import list_policies, policy_options
    assert policy_options(TRADE_POLICY) == [
        {"option": "trade_policy_default", "requires_traditions": []},
        {"option": "trade_policy_consumer_goods", "requires_traditions": ["tr_mercantile_adaptive_economic_policies"]},
        {"option": "trade_policy_other", "requires_traditions": []}]
    state = {"date": "2211.01.25", "country_controls": {"known": True, "policies": {
        "trade_policy": {"selected": "trade_policy_default", "date": None}}, "edicts": {}}}
    listed = list_policies(state, {"trade_policy": TRADE_POLICY})
    assert listed["status"] == "listed"
    assert listed["policies"][0]["policy"] == "trade_policy" and listed["policies"][0]["cooldown_elapsed"] is True
    assert listed["policies"][0]["options"][1]["requires_traditions"] == ["tr_mercantile_adaptive_economic_policies"]
    assert list_policies({"country_controls": {"known": False}}, {})["status"] == "blocked"
