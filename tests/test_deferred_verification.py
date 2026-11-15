"""Fast-mode journal transitions and deferred positive postconditions."""
import json

import pytest

from stellaris_autopilot.deferred_verification import evaluate_effect, settle_accepted
from stellaris_autopilot.journal import Journal
from stellaris_autopilot.write_mode import use_verification, reset_verification, verification_required


def test_verification_required_respects_override_and_env(monkeypatch):
    monkeypatch.delenv("STELLARIS_AUTOPILOT_VERIFY", raising=False)
    assert verification_required() is False
    monkeypatch.setenv("STELLARIS_AUTOPILOT_VERIFY", "1")
    assert verification_required() is True
    token = use_verification(False)
    try:
        assert verification_required() is False
    finally:
        reset_verification(token)
    assert verification_required(True) is True
    assert verification_required(False) is False


def test_journal_allows_multiple_accepted_rows_but_blocks_unverified(tmp_path):
    journal = Journal(tmp_path / "journal.sqlite")
    first = journal.claim("S0", "add_system_claim:{\"system_id\":\"1\"}", {"country_id": "0"})
    journal.update(first, "dispatched", "d")
    journal.update(first, "accepted", "a")
    assert journal.blocking() == []
    assert len(journal.unresolved()) == 1
    second = journal.claim("S0", "set_species_right:{\"species_id\":\"1\",\"right\":\"x\"}", {"country_id": "0"})
    assert second is not None
    journal.update(second, "dispatched", "d")
    journal.update(second, "accepted", "a")
    third = journal.claim("S0", "declare_war:{\"target_country_id\":\"2\"}", {"country_id": "0"})
    assert third is not None
    journal.update(third, "dispatched", "d")
    journal.update(third, "unverified", "ambiguous")
    assert journal.claim("S0", "offer_peace:{\"target_country_id\":\"2\"}", {"country_id": "0"}) is None
    journal.close()


def test_deferred_claim_and_unknown_kind():
    before = {"country_id": "0", "_native_operation": {"kind": "add_system_claim", "system_id": "596"}}
    present = {"claims": {"known": True, "by_system": {"596": {"system_id": "596"}}}}
    absent = {"claims": {"known": True, "by_system": {}}}
    unknown = {"claims": {"known": False, "by_system": {}}}
    assert evaluate_effect(before, present, "add_system_claim") == (
        True, "Positive postcondition is present in the later observation")
    verdict, _ = evaluate_effect(before, absent, "add_system_claim")
    assert verdict is False
    verdict, _ = evaluate_effect(before, unknown, "add_system_claim")
    assert verdict is None
    verdict, reason = evaluate_effect(before, present, "queue_mystery")
    assert verdict is None and "No deferred positive postcondition" in reason


def test_settle_accepted_marks_verified_failed_and_unverified(tmp_path):
    journal = Journal(tmp_path / "journal.sqlite")
    def record(action, payload, status="accepted"):
        state = {"country_id": "0", "_native_operation": payload}
        op = journal.claim("S0", action, state)
        journal.update(op, "dispatched", "d")
        journal.update(op, status, "a")
        return op
    verified_id = record("add_system_claim:" + json.dumps({"system_id": "1"}, separators=(",", ":")), {"system_id": "1"})
    failed_id = record("add_system_claim:" + json.dumps({"system_id": "2"}, separators=(",", ":")), {"system_id": "2"})
    # Unique(snapshot, action) - use different snapshot for third
    unknown_state = {"country_id": "0", "_native_operation": {"kind": "queue_mystery"}}
    unknown_id = journal.claim("S1", "queue_mystery:{}", unknown_state)
    journal.update(unknown_id, "dispatched", "d")
    journal.update(unknown_id, "accepted", "a")
    observation = {"state": {"claims": {"known": True, "by_system": {"1": {}}}}, "snapshot": "S2"}
    settle_accepted(observation, journal)
    deferred = observation["deferred_verification"]
    by_id = {item["id"]: item for item in deferred["verifications"]}
    assert by_id[verified_id]["status"] == "verified"
    assert by_id[failed_id]["status"] == "failed"
    assert by_id[unknown_id]["status"] == "unverified"
    assert deferred["pending"] is True
    assert journal.accepted() == []
    assert any(row["id"] == unknown_id and row["status"] == "unverified" for row in journal.unresolved())
    journal.close()
