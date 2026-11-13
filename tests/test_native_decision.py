"""Run the 4.5.2 planet decision adapter on fake memory (no Frida, no game)."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

NODE = shutil.which("node")
SOURCE = Path(__file__).resolve().parents[1] / "src" / "stellaris_autopilot" / "native_decision.js"
HARNESS = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const scenario = process.argv[2];
const memory = new Map(); const calls = []; const writes = []; const destroyed = [];
class Pointer {
    constructor(value) { this.value = value; }
    add(offset) { return new Pointer(this.value + offset); }
    isNull() { return this.value === 0; }
    equals(other) { return other.value === this.value; }
    readNumber() {
        if (!memory.has(this.value)) throw Error('Unmapped native memory ' + this.value.toString(16));
        return memory.get(this.value);
    }
    readPointer() { return new Pointer(this.readNumber()); }
    readU32() { return this.readNumber() >>> 0; }
    readU8() { return this.readNumber() & 0xff; }
    writePointer(value) { writes.push([this.value, value.value]); memory.set(this.value, value.value); return this; }
    writeU32(value) { writes.push([this.value, value >>> 0]); memory.set(this.value, value >>> 0); return this; }
    writeU8(value) { writes.push([this.value, value & 0xff]); memory.set(this.value, value & 0xff); return this; }
}
const put = (address, value) => memory.set(address, value);
const B = 0x140000000; const base = new Pointer(B);
const C = 0xb0000, VT = B + 0x2391880, NULLDEC = 0x7f000;
put(B + 0x31136b0, NULLDEC);
function NativeFunction(address) {
    const rva = address.value - B;
    return (...values) => {
        calls.push(rva);
        if (rva === 0x6b94e0) {
            put(C, scenario === 'bad_vtable' ? B + 0x23b4138 : VT);
            put(VT, B + 0x6b2ca0); put(VT + 0x40, B + 0x6b34c0); put(VT + 0x48, B + 0x6b2cd0);
            put(C + 0x20, NULLDEC); put(C + 0x28, scenario === 'bad_defaults' ? 0 : 0xffffffff);
            put(C + 0x2c, 0); put(C + 0x30, 0xffffffff);
            return new Pointer(C);
        }
        return 1;
    };
}
// Physical planet pool: index 3 holds planet 3; index 4 a stale handle.
put(B + 0x3114e10, 0x10000); put(0x10000 + 0x18, 0x11000); put(0x10000 + 0x20, 8);
put(0x11000 + 3 * 0x10 + 8, 0x20000); put(0x20000 + 0x18, 3);
put(0x11000 + 4 * 0x10 + 8, 0x21000); put(0x21000 + 0x18, 0x01000004);
// Decision database with two types; keys come from the keyAt stub.
put(B + 0x3112598, 0x30000); put(0x30000 + 0x50, 0x31000); put(0x30000 + 0x5c, 2);
put(0x31000, 0x40000); put(0x31008, 0x41000);
const keys = {0x40000: 'decision_planet_food_boost', 0x41000: scenario === 'duplicate' ? 'decision_planet_food_boost' : 'decision_other'};
put(0x70000 + 0x5c, scenario === 'other_human' ? 4 : 0); put(0x60000, 0x61000);
const request = {kind: 'enact_planet_decision', country_id: '0', token: 'a'.repeat(32), planet_id: '3',
    decision_id: 'decision_planet_food_boost'};
if (scenario === 'stale') request.planet_id = '4';
if (scenario === 'missing_planet') request.planet_id = '5';
if (scenario === 'unknown_decision') request.decision_id = 'decision_nope';
if (scenario === 'extra') request.leader_id = '1';
if (scenario === 'bad_key') request.decision_id = 'x y';
class NativePreconditionError extends Error {}
const context = {
    base, request, NativeFunction, NativePreconditionError,
    qsEventGame: new Pointer(0x60000), qsLocalPlayer: () => new Pointer(0x70000),
    qsValidateWithReason: () => { calls.push('validate'); return {allowed: true, reason: ''}; },
    validId: value => typeof value === 'string' && /^(0|[1-9][0-9]{0,9})$/.test(value),
    keyAt: address => keys[address.value] || null,
    destroyUnsubmitted: object => destroyed.push(object.value),
};
try {
    const prepared = vm.runInNewContext(source + '\nprepareDecisionCommand(request);', context);
    process.stdout.write(JSON.stringify({accepted: true, calls, writes, destroyed, meta: prepared.meta,
        validation: prepared.validation}));
} catch (error) {
    process.stdout.write(JSON.stringify({accepted: false, reason: String(error), calls, destroyed}));
}
"""
C = 0xb0000


def _run(scenario):
    result = subprocess.run([NODE, "-e", HARNESS, str(SOURCE), scenario], capture_output=True, text=True,
                            timeout=10, check=True)
    return json.loads(result.stdout)


pytestmark = pytest.mark.skipif(NODE is None, reason="Native JavaScript adapter checks require Node.js")


def test_enact_command_carries_the_decision_type_planet_carrier_and_our_country():
    result = _run("ok")
    assert result["accepted"] is True and result["validation"] == {"allowed": True, "reason": ""}
    assert result["writes"] == [[C + 0x20, 0x40000], [C + 0x28, 3], [C + 0x2c, 0], [C + 0x30, 0]]
    assert result["calls"] == [0x6b94e0, "validate"] and result["destroyed"] == []
    assert result["meta"] == {"native_planet_handle": 3, "native_decision": "decision_planet_food_boost"}


@pytest.mark.parametrize("scenario", ["stale", "missing_planet", "unknown_decision", "duplicate", "other_human"])
def test_lookups_fail_before_any_allocation(scenario):
    result = _run(scenario)
    assert result["accepted"] is False and 0x6b94e0 not in result["calls"]


@pytest.mark.parametrize("scenario", ["extra", "bad_key"])
def test_malformed_requests_never_read_game_memory(scenario):
    result = _run(scenario)
    assert result["accepted"] is False and result["calls"] == []


def test_an_unrecognized_command_is_never_destroyed_through_a_guessed_vtable():
    result = _run("bad_vtable")
    assert result["accepted"] is False and result["destroyed"] == [] and "validate" not in result["calls"]


def test_unexpected_defaults_destroy_the_unsubmitted_command():
    result = _run("bad_defaults")
    assert result["accepted"] is False and result["destroyed"] == [C] and "validate" not in result["calls"]


def test_the_router_sends_the_kind_to_the_decision_adapter():
    router = (SOURCE.parent / "native_research.js").read_text(encoding="utf-8")
    assert "request.kind === 'enact_planet_decision' ? prepareDecisionCommand(request)" in router


def test_enqueue_accepts_every_legacy_only_canonical_request():
    # Live 2026-11-14: enqueue rejected enact_planet_decision before dispatch
    # ("Unknown native kind or malformed typed payload") because its canonical
    # function was missing from the enqueue chain.
    import re

    from stellaris_autopilot import native_builds
    router = (SOURCE.parent / "native_research.js").read_text(encoding="utf-8")
    chain = router[router.index("canonical = canonicalShipRequest(request)"):
                   router.index("Unknown native kind or malformed typed payload")]
    for name in native_builds.LEGACY_ONLY_SOURCES:
        text = (SOURCE.parent / name).read_text(encoding="utf-8")
        for function in re.findall(r"^function (canonical[A-Za-z]+Request)\(request\)", text, re.M):
            if function in {"canonicalEnvoyRequest"}:
                continue  # called by canonicalDiplomacyRequest
            assert f"typeof {function} === 'function' ? {function}(request)" in chain, function
