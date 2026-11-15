"""Equivalence and accounting tests for the fast attached-mode verification path.

Everything is offline: dumps are written to pytest temporary directories. Set
QS_REAL_DUMP to a *copy* of a real dump_qs_direct.sav.txt to also check it.
"""
import gc
import hashlib
import os
from pathlib import Path
import random
import shutil
import time
import uuid

import pytest

import stellaris_autopilot.attached_runtime as attached
import stellaris_autopilot.direct_observe as observe_module
from stellaris_autopilot.attached_runtime import AttachedRuntime
from stellaris_autopilot.campaign import CampaignError, package_dump
from stellaris_autopilot.direct import DirectConfig
from stellaris_autopilot.direct_mcp import validate_connection
from stellaris_autopilot.direct_observe import DirectObserver, parse_stats
from stellaris_autopilot.pdx import PDXError, PDXNode, _reference_loads, loads, loads_selected
from stellaris_autopilot.saves import SaveError


TOKEN = "a" * 32
SESSION = f"qs_direct_session_{TOKEN}"
TARGETS = f"qs_direct_targets_{TOKEN}"
DONE = "qs_direct_done_" + "1" * 32
DUMP = '''version="Cygnus v4.5.2" version_control_revision=101 name="Earth"
date="2200.01.01" galaxy={name="campaign-123" other={a=1}} ironman=no multiplayer=no
required_dlcs={"Utopia"} player={{name="me" country=0}}
species_db={2348810241={portrait="human"}}
planets={planet={1={owner=0 name="Earth"}}}
country={0={name="Earth" founder_species_ref=2348810241 # comment { with brace
flag={icon={category="human" file="flag_human_9.dds"}
background={category="backgrounds" file="00_solid.dds"} colors={"blue" "black" "null" "null" "null" "null"}}
flags={FLAGS}
desc="has } brace \\"quoted\\" and { more"
resources={minerals=617.55104}} 1={name="Other" flags={qs_direct_session_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb=1}}}
'''


def dump_text(flags=f"{SESSION}=62808000 {TARGETS}=62808000 {DONE}=1", **replace):
    text = DUMP.replace("{FLAGS}", "{" + flags + "}")
    for old, new in replace.items():
        text = text.replace(old, new)
    return text


def write(tmp_path, text, name="dump_qs_direct.sav.txt"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def config(tmp_path, **changes):
    values = dict(
        user_dir=tmp_path, state_dir=tmp_path / "state", country_id="0", campaign_id="campaign-123",
        verified_game_version="Cygnus v4.5.2", session_token=TOKEN, allowed_actions=("probe_connection",),
        allowed_orders=(), allowed_native_actions=("set_paused",), single_player_confirmed=True,
        non_ironman_confirmed=True, verification_timeout_seconds=120, poll_seconds=0.25,
        event_namespace="qs_live_" + TOKEN)
    values.update(changes)
    return DirectConfig(**values)


def outcome(function):
    try:
        function()
    except (ValueError, CampaignError) as exc:
        return type(exc).__name__, str(exc)
    return "ok"


def gate(observed, version="Cygnus v4.5.2"):
    """The exact connect step-one gate from AttachedRuntime.initialize."""
    meta = observed.state.get("metadata", {})
    return not (observed.state.get("country_id") != "0" or meta.get("player_country_ids") != ["0"]
                or meta.get("ironman") not in (False, "no") or meta.get("multiplayer") in (True, "yes")
                or meta.get("game_version") != version or not meta.get("campaign_id"))


# --- fast full parser -----------------------------------------------------------------------------

def test_regex_whitespace_matches_str_isspace_for_every_code_point():
    import re
    pattern = re.compile(r"\s")
    assert all(bool(pattern.match(chr(code))) == chr(code).isspace() for code in range(0x110000))


@pytest.mark.parametrize("text", [
    "", "a", "a=b", "a={}", "{}", "{ {} }", "a=1 b=-2.5 c=+3 d=1e5 e=.5 f=1. g=yes h=\"1\"",
    'k="esc \\n \\t \\" \\\\ \\q" "q"=v', "x=rgb { 1 2 3 } y = hsv{0.1 0.2 0.3}",
    "\ufeffa=b\ufeff c=\u00a0d\u3000e=f", "a=b#comment\nc=d # x { } = \"\n", "a=1 2 3 b", "a=\"\"",
    "1=2 3={4={5}}", "a\ufeffb=c", "a=b c=d",
    "a = { x = { y = { z = 1 } } } a = 2",
])
def test_fast_parser_builds_identical_trees(text):
    assert loads(text) == _reference_loads(text)


@pytest.mark.parametrize("text", [
    "}", "a=}", "a=", "=a", "a==b", "a={", "a={b=1", '"open', 'a="open\\', "a=b }", "\x00",
    "{" * 130 + "}" * 130, "a=" + "{" * 129 + "}" * 129,
])
def test_malformed_input_raises_the_reference_error(text):
    with pytest.raises(PDXError) as reference:
        _reference_loads(text) if "\x00" not in text else loads(text)
    with pytest.raises(PDXError) as fast:
        loads(text)
    assert str(fast.value) == str(reference.value)


def test_nesting_limit_boundary_matches_reference():
    text = "a=" + "{" * 128 + "}" * 128
    assert loads(text) == _reference_loads(text)


def test_random_token_streams_are_equivalent_including_errors():
    rng = random.Random(4520)
    atoms = ["{", "}", "=", "a", "b_c", "1", "-2", "3.5", "1e3", "yes", '"q"', '"e\\"s"', '"}"',
             "#c\n", " ", "\n", "\t", "\ufeff", "\u2003", "x.y", '"\\n"', "rgb"]
    for _ in range(4000):
        text = "".join(rng.choice(atoms) + rng.choice(["", " "]) for _ in range(rng.randint(0, 30)))
        try:
            reference = _reference_loads(text)
        except PDXError as exc:
            with pytest.raises(PDXError) as fast:
                loads(text)
            assert str(fast.value) == str(exc), text
        else:
            assert loads(text) == reference, text


def test_parser_restores_the_cyclic_collector_state():
    assert gc.isenabled()
    loads("a={b=1}")
    assert gc.isenabled()
    gc.disable()
    try:
        loads("a={b=1}")
        assert not gc.isenabled()
    finally:
        gc.enable()


# --- targeted extraction --------------------------------------------------------------------------

def test_selected_paths_equal_the_full_tree_and_other_objects_are_placeholders():
    text = dump_text()
    full, selected = loads(text), loads_selected(text, {"galaxy": {"name": True}, "player": True,
                                                        "country": {"0": {"flags": True}}})
    assert [key for key, _ in selected.entries] == [key for key, _ in full.entries]
    assert selected.get("player") == full.get("player")
    assert selected.get("galaxy").get("name") == full.get("galaxy").get("name")
    assert selected.get("galaxy").get("other") == PDXNode()
    assert selected.get("species_db") == PDXNode()
    country, full_country = selected.get("country").get("0"), full.get("country").get("0")
    assert country.get("flags") == full_country.get("flags")
    assert country.get("desc") == full_country.get("desc")
    assert country.get("flag") == PDXNode()
    assert selected.get("country").get("1") == PDXNode()
    for key in ("version", "date", "ironman", "multiplayer", "name"):
        assert selected.get(key) == full.get(key)


@pytest.mark.parametrize("text", ["a={", 'a={"x}', "a={b={c=1}", "a=}", "a=", "a==1"])
def test_selected_parse_rejects_truncated_or_malformed_selected_input(text):
    with pytest.raises(PDXError):
        loads_selected(text, {})


VARIANTS = {
    "valid": {},
    "ironman": {"ironman=no": "ironman=yes"},
    "multiplayer": {"multiplayer=no": "multiplayer=yes"},
    "other_build": {'version="Cygnus v4.5.2"': 'version="Cygnus v4.5.4"'},
    "campaign": {'name="campaign-123"': 'name="campaign-999"'},
    "blank_campaign": {'name="campaign-123"': 'name=" "'},
    "two_players": {"player={{name=\"me\" country=0}}": "player={{country=0} {country=1}}"},
    "other_player": {"player={{name=\"me\" country=0}}": "player={{country=1}}"},
    "no_player": {"player={{name=\"me\" country=0}}": ""},
    "no_date": {'date="2200.01.01"': ""},
    "bad_date": {'date="2200.01.01"': 'date="soon"'},
    "repeated_flags": {"flags={": "flags={} flags={"},
    "galaxy_ironman": {"ironman=no multiplayer=no": "multiplayer=no", "other={a=1}": "ironman=yes"},
    "savegame_version": {'version="Cygnus v4.5.2" ': 'savegame_version="Cygnus v4.5.2" '},
    "no_country_0": {"country={0={": "country={7={"},
}


@pytest.mark.parametrize("flags", [f"{SESSION}=62808000 {TARGETS}=62808000", f"{TARGETS}=1", "",
                                   f"{SESSION}={{flag_date=5}}", f"{SESSION}=-1", f"{SESSION}=1 {SESSION}=2"])
@pytest.mark.parametrize("variant", sorted(VARIANTS))
def test_targeted_and_full_reads_give_identical_validation(tmp_path, variant, flags):
    path = write(tmp_path, dump_text(flags, **VARIANTS[variant]))
    observer = DirectObserver(path, "0")
    full = outcome(lambda: observer.read())
    targeted = outcome(lambda: observer.read_identity())
    if full != "ok" or targeted != "ok":
        # Identity errors are the same SaveError subset (country/player/date).
        assert full == targeted
        return
    full, targeted = observer.read(), observer.read_identity()
    assert targeted.digest == full.digest and targeted.modified_at == full.modified_at
    assert targeted.state["direct_bridge"] == full.state["direct_bridge"]
    for key in ("date", "country_id"):
        assert targeted.state[key] == full.state[key]
    for key in ("source", "format", "game_version", "campaign_id", "player_country_ids", "ironman", "multiplayer"):
        assert targeted.state["metadata"][key] == full.state["metadata"][key]
    assert gate(targeted) == gate(full)
    for fresh in (True, False):
        assert (outcome(lambda: validate_connection(config(tmp_path), targeted, require_fresh=fresh))
                == outcome(lambda: validate_connection(config(tmp_path), full, require_fresh=fresh)))
    assert ((TARGETS in targeted.state["direct_bridge"]["target_flags"])
            == (TARGETS in full.state["direct_bridge"]["target_flags"]))


def test_real_dump_copy_equivalence_if_provided(tmp_path):
    source = os.environ.get("QS_REAL_DUMP")
    if not source:
        pytest.skip("Set QS_REAL_DUMP to a copied real dump to run this check")
    path = tmp_path / "dump_qs_direct.sav.txt"
    shutil.copyfile(source, path)
    text = path.read_text(encoding="utf-8-sig")
    assert loads(text) == _reference_loads(text)
    observer = DirectObserver(path, "0")
    full, targeted = observer.read(), observer.read_identity()
    assert targeted.state["direct_bridge"] == full.state["direct_bridge"]
    assert gate(targeted) == gate(full)
    token = full.state["direct_bridge"]["session_flags"][0].removeprefix("qs_direct_session_") \
        if full.state["direct_bridge"]["session_flags"] else TOKEN
    cfg = config(tmp_path, campaign_id=full.state["metadata"]["campaign_id"], session_token=token)
    assert (outcome(lambda: validate_connection(cfg, targeted, require_fresh=False))
            == outcome(lambda: validate_connection(cfg, full, require_fresh=False)))


# --- caches ---------------------------------------------------------------------------------------

def test_unchanged_bytes_are_parsed_once_across_observer_instances(tmp_path):
    path = write(tmp_path, dump_text())
    first = DirectObserver(path, "0").read()
    first.state["date"] = "mutated"
    second = DirectObserver(path, "0").read()
    assert second.state["date"] == "2200.01.01" and second.digest == first.digest
    stats = parse_stats()
    assert stats["full_parse"] == 1 and stats["state_cache_hit"] == 1


def test_changed_bytes_are_parsed_again(tmp_path):
    path = write(tmp_path, dump_text())
    DirectObserver(path, "0").read()
    write(tmp_path, dump_text(f"{SESSION}=62808000"))
    assert DirectObserver(path, "0").read().state["direct_bridge"]["target_flags"] == []
    assert parse_stats()["full_parse"] == 2


def test_same_bytes_at_another_path_keep_their_own_source(tmp_path):
    a = write(tmp_path, dump_text())
    (tmp_path / "b").mkdir()
    b = write(tmp_path / "b", dump_text())
    assert DirectObserver(a, "0").read().state["metadata"]["source"] == str(a.resolve())
    assert DirectObserver(b, "0").read().state["metadata"]["source"] == str(b.resolve())
    assert parse_stats()["full_parse"] == 1  # one tree, two normalizations


def test_checkpoint_packaging_reuses_the_verified_parse_of_the_same_bytes(tmp_path):
    path = write(tmp_path, dump_text())
    observed = DirectObserver(path, "0").read()
    saved = package_dump(path, tmp_path / "out" / "a.sav", "0", expected_digest=observed.digest)
    assert saved["parse_verified"] is True and saved["gamestate_sha256"] == observed.digest
    assert parse_stats()["full_parse"] == 1 and parse_stats()["root_cache_hit"] == 1


def test_checkpoint_packaging_still_parses_uncached_bytes_and_rejects_changes(tmp_path):
    path = write(tmp_path, dump_text())
    observed = DirectObserver(path, "0").read()
    observe_module.clear_observation_caches()
    package_dump(path, tmp_path / "out" / "a.sav", "0", expected_digest=observed.digest)
    assert parse_stats()["full_parse"] == 2
    write(tmp_path, dump_text(""))
    with pytest.raises(CampaignError, match="changed after the verified observation"):
        package_dump(path, tmp_path / "out" / "b.sav", "0", expected_digest=observed.digest)


def test_is_stale_uses_only_stat(tmp_path, monkeypatch):
    path = write(tmp_path, dump_text())
    old = time.time() - 60
    os.utime(path, (old, old))
    monkeypatch.setattr(observe_module, "loads", lambda text: pytest.fail("must not parse"))
    observer = DirectObserver(path, "0")
    assert observer.is_stale(time.time()) is True
    assert observer.is_stale(old - 1) is False
    assert DirectObserver(tmp_path / "missing.txt", "0").is_stale(time.time()) is False


# --- wait-for-dump loop ---------------------------------------------------------------------------

class Process:
    pid = 1

    def poll(self):
        return None

    def close(self):
        pass


class DumpNative:
    """request_dump optionally rewrites the dump; results correlate immediately."""

    def __init__(self, path, text=None, writes=1):
        self.path, self.text, self.writes = path, text, writes

    def request_dump(self, token):
        # Coarse filesystem clocks can stamp an immediate write slightly before
        # the request time; the real game takes seconds to write a dump.
        time.sleep(0.05)
        for _ in range(self.writes if self.text is not None else 0):
            self.path.write_text(self.text, encoding="utf-8")

    def result(self, token):
        return {"status": "dump_requested", "kind": "request_observation", "token": token,
                "country_id": "0", "execution_source": "window_message", "runtime": {"paused": True}}

    def close(self):
        pass


def runtime_for(tmp_path, native_text=None, seconds=3.0):
    user = tmp_path / "user"
    (user / "save games").mkdir(parents=True, exist_ok=True)
    path = user / "save games" / "dump_qs_direct.sav.txt"
    path.write_text(dump_text(""), encoding="utf-8")
    old = time.time() - 60
    os.utime(path, (old, old))
    native = DumpNative(path, native_text)
    runtime = AttachedRuntime(Process(), native, user, tmp_path / "state", uuid.uuid4().hex,
                              deadline=time.monotonic() + seconds)
    return runtime, path


def test_stale_dump_is_never_parsed_and_timeout_names_the_missing_write(tmp_path, monkeypatch):
    runtime, _ = runtime_for(tmp_path, None, seconds=1.0)
    monkeypatch.setattr(observe_module, "loads", lambda text: pytest.fail("stale dump parsed"))
    monkeypatch.setattr(observe_module, "loads_selected", lambda *a: pytest.fail("stale dump parsed"))
    with pytest.raises(CampaignError, match="No fresh matching dump.*wrote no new dump.*connect-timeout"):
        runtime._fresh()


def test_fresh_dump_is_read_once_after_it_is_stable(tmp_path):
    runtime, _ = runtime_for(tmp_path, dump_text())
    observed, receipt = runtime._fresh()
    assert receipt["status"] == "dump_requested"
    assert TARGETS in observed.state["direct_bridge"]["target_flags"]
    assert parse_stats() == {"full_parse": 1}


def test_identity_read_uses_targeted_extraction_only(tmp_path):
    runtime, _ = runtime_for(tmp_path, dump_text())
    observed, _ = runtime._fresh_identity()
    assert gate(observed) and observed.state["metadata"]["extraction"] == "targeted_identity"
    assert parse_stats() == {"targeted_parse": 1}


def test_unreadable_fresh_generation_is_not_reparsed_and_error_is_specific(tmp_path, monkeypatch):
    runtime, _ = runtime_for(tmp_path, "country={0={", seconds=1.5)
    calls = []
    original = observe_module.loads

    def counted(text):
        calls.append(1)
        return original(text)

    monkeypatch.setattr(observe_module, "loads", counted)
    with pytest.raises(CampaignError, match="No fresh matching dump.*written but could not be read"):
        runtime._fresh()
    assert len(calls) == 1


def test_window_limit_and_budget_are_reported_differently(tmp_path, monkeypatch):
    runtime, _ = runtime_for(tmp_path, None, seconds=0.5)
    with pytest.raises(CampaignError, match="connection time budget"):
        runtime._fresh()
    runtime._deadline = None
    clock = iter([0.0] + [0.0] * 3 + [10_000.0] * 50)
    monkeypatch.setattr(attached.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(attached.time, "sleep", lambda seconds: None)
    with pytest.raises(CampaignError, match="120-second dump window"):
        runtime._fresh()


def test_default_connect_timeout_is_the_maximum():
    import inspect
    from stellaris_autopilot.existing_session import ExistingGameSession
    assert inspect.signature(ExistingGameSession).parameters["timeout_seconds"].default == 120
    script = (Path(__file__).parents[1] / "scripts" / "serve_existing_game.py").read_text(encoding="utf-8")
    assert 'parser.add_argument("--connect-timeout", type=float, default=120,' in script


def test_digest_is_of_the_exact_bytes(tmp_path):
    path = write(tmp_path, dump_text())
    assert DirectObserver(path, "0").read_identity().digest == hashlib.sha256(path.read_bytes()).hexdigest()


def test_identity_read_requires_a_country(tmp_path):
    with pytest.raises(ValueError, match="explicit country_id"):
        DirectObserver(write(tmp_path, dump_text())).read_identity()


def test_saveerror_type_is_preserved_for_targeted_parse_errors(tmp_path):
    with pytest.raises(SaveError, match="Cannot read textual direct dump"):
        DirectObserver(write(tmp_path, "country={0={"), "0").read_identity()


# --- whole connect and checkpoint accounting -------------------------------------------------------

class GameNative:
    """Offline stand-in for the pinned worker: writes dumps like the game does."""

    supports_wakeup = True
    game_version = "Cygnus v4.5.2"
    observation_only = False

    def __init__(self, path):
        self.path, self.flags, self.results, self.pending_pause = path, [], {}, None
        self.last_runtime = {"paused": True, "protected_pause": False}
        self.dumps = 0

    def _write(self):
        time.sleep(0.05)
        self.dumps += 1
        self.path.write_text(dump_text(" ".join(f"{flag}=62808000" for flag in self.flags)), encoding="utf-8")

    def _response(self, kind, token, status):
        return {"kind": kind, "token": token, "country_id": "0", "status": status,
                "execution_source": "window_message", "runtime": dict(self.last_runtime)}

    def bind_existing_window(self, token):
        return {"token": token, "status": "window_bound", "runtime": {"paused": True}}

    def load_attached_events(self, token):
        self.results[token] = self._response("load_attached_events", token, "events_load_returned")

    def run_inbox(self, token):
        inbox = (self.path.parents[1] / "qs_direct_inbox.txt").read_text(encoding="utf-8")
        if ".99 0" in inbox:
            session = inbox.split("session=")[1].split()[0]
            self.flags += [f"qs_direct_session_{session}", f"qs_direct_targets_{session}"]
        if self.pending_pause is not None:
            self.flags.append(f"qs_direct_done_{self.pending_pause}")
            self.pending_pause = None
        self.results[token] = self._response("run_inbox", token, "inbox_executed")
        self._write()

    def request_dump(self, token):
        self.results[token] = self._response("request_observation", token, "dump_requested")
        self._write()

    def submit(self, selection, token):
        self.pending_pause = token
        self.results[token] = {"status": "submitted", "kind": selection.kind, "country_id": "0",
                               "token": token, **selection.to_dict()}
        return {"status": "pending", "token": token}

    def result(self, token):
        return dict(self.results.get(token, {"status": "pending"}))

    def cancel(self, token):
        return {"status": "cancelled", "token": token}

    def close(self):
        pass


def test_connect_and_checkpoint_parse_counts(tmp_path):
    user = tmp_path / "user"
    (user / "save games").mkdir(parents=True)
    path = user / "save games" / "dump_qs_direct.sav.txt"
    path.write_text(dump_text(""), encoding="utf-8")
    old = time.time() - 60
    os.utime(path, (old, old))
    native = GameNative(path)
    runtime = AttachedRuntime(Process(), native, user, tmp_path / "state", "c" * 32)
    try:
        runtime.initialize()
        # Identity+backup reuse the on-disk dump (targeted + one full parse);
        # the bound session still requests one fresh full parse after events.
        connect = parse_stats()
        assert connect.get("targeted_parse") == 1 and connect.get("full_parse") == 2
        assert runtime.initial_summary["planet_count"] == 1
        observe_module.reset_parse_stats()
        result = runtime.checkpoint_game()
        assert result["status"] == "checkpoint_saved"
        assert result["checkpoint"]["parse_verified"] is True
        # The refresh repeats the bound snapshot's bytes (cache), the pause proof
        # adds a receipt (one parse), the checkpoint dump repeats it (cache) and
        # packaging reuses that verified tree. Every step still read a new dump.
        checkpoint = parse_stats()
        assert checkpoint.get("full_parse") == 1 and checkpoint.get("root_cache_hit") == 1
        # Connect issues one post-event dump (the pre-existing dump is reused);
        # checkpoint still issues its own dumps.
        assert native.dumps >= 1 + 3
    finally:
        runtime.close()
    assert observe_module._ROOT_CACHE == {} and observe_module._STATE_CACHE == {}
