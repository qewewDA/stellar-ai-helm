import pytest

from stellaris_autopilot.pdx import loads
from stellaris_autopilot.species_rights_commands import (
    ResolvedSpeciesRightCommand, category_of, native_species_right_payload, observe_species_rights,
    resolve_species_right, species_right_request, verify_species_right)

ENTRY = """species_index=%s living_standard="%s" citizenship="citizenship_full"
    military_service="military_service_full" slavery="slavery_indentured" purge="purge_displacement"
    population_control="population_control_no" colonization_control="colonization_control_no"
    migration_control="migration_control_no" subspecies_integration="no_integration"
    former_living_standard="%s" last_changed_living_standard="%s" """
SAVE = """country={ 0={ modules={ standard_species_rights_module={ enabled=yes
    default={ %s } primary={ %s } built_species={ %s } } } } }"""
SPECIES = "2348810241"


def _root(primary="living_standard_normal", former="living_standard_stratified", changed="0.01.01"):
    return loads(SAVE % (ENTRY % ("0", "living_standard_normal", "living_standard_stratified", "0.01.01"),
                         ENTRY % (SPECIES, primary, former, changed),
                         ENTRY % ("4294967295", "living_standard_normal", "living_standard_stratified", "0.01.01")))


def _state(root, date="2211.01.25"):
    return {"country_id": "0", "date": date, "metadata": {"campaign_id": "c"},
            "species_rights": observe_species_rights(root, "0")}


def _command(right="living_standard_utopian"):
    request = species_right_request("set_species_right", True, species_id=SPECIES, right=right)
    return resolve_species_right(_state(_root()), request)


def test_observation_reads_every_entry_of_the_rights_module():
    observed = observe_species_rights(_root(), "0")
    assert observed["known"] is True and set(observed["entries"]) == {"default", "primary", "built_species"}
    primary = observed["entries"]["primary"]
    assert primary["species_index"] == SPECIES and primary["living_standard"] == "living_standard_normal"
    assert observe_species_rights(loads("country={ 0={ } }"), "0") == {"known": False, "entries": {}}
    broken = loads(SAVE.replace("primary", "primarx") % ("a=1", "a=1", "a=1"))
    assert observe_species_rights(broken, "0")["known"] is False


def test_category_comes_from_the_vanilla_key_prefix():
    assert category_of("living_standard_utopian") == "living_standard"
    assert category_of("colonization_control_yes") == "colonization_control"
    assert category_of("purge_normal") == "purge"
    assert category_of("no_integration") is None and category_of("Bad Key") is None


def test_requests_need_knowledge_and_a_known_category():
    with pytest.raises(ValueError, match="knowledge"):
        species_right_request("set_species_right", None, species_id=SPECIES, right="living_standard_utopian")
    with pytest.raises(ValueError, match="category"):
        species_right_request("set_species_right", True, species_id=SPECIES, right="no_integration")
    with pytest.raises(ValueError, match="Unsupported"):
        species_right_request("set_pop_right", True, species_id=SPECIES, right="living_standard_utopian")


def test_resolution_targets_only_the_primary_species_and_a_new_right():
    command = _command()
    assert command.current_right == "living_standard_normal"
    payload = native_species_right_payload(command, "a" * 32)
    assert payload == {"kind": "set_species_right", "country_id": "0", "token": "a" * 32, "species_id": SPECIES,
                       "category": "living_standard", "right": "living_standard_utopian",
                       "current_right": "living_standard_normal"}
    with pytest.raises(ValueError, match="already"):
        _command("living_standard_normal")
    other = species_right_request("set_species_right", True, species_id="7", right="living_standard_utopian")
    with pytest.raises(ValueError, match="primary"):
        resolve_species_right(_state(_root()), other)
    with pytest.raises(ValueError, match="observable"):
        resolve_species_right({"country_id": "0", "species_rights": {"known": False}}, other)


def test_verification_needs_exactly_the_requested_category_changed():
    command = _command()
    before = _state(_root())
    assert verify_species_right(before, _state(_root("living_standard_utopian", "living_standard_normal",
                                                     "2211.01.25")), command) is True
    assert verify_species_right(before, _state(_root()), command) is False
    assert verify_species_right(before, _state(_root("living_standard_basic")), command) is False
    assert verify_species_right(before, _state(_root("living_standard_utopian"), date="2211.01.26"), command) is False
    other = loads((SAVE % (ENTRY % ("0", "living_standard_basic", "x", "0.01.01"),
                              ENTRY % (SPECIES, "living_standard_utopian", "x", "0.01.01"),
                              ENTRY % ("4294967295", "living_standard_normal", "x", "0.01.01"))))
    assert verify_species_right(before, _state(other), command) is False
    assert verify_species_right(before, _state(_root("living_standard_utopian")),
                                ResolvedSpeciesRightCommand(command.request, "living_standard_basic")) is False
