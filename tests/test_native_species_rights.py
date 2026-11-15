"""Run the 4.5.2 species rights adapter on fake memory (no Frida, no game)."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

NODE = shutil.which("node")
SOURCE = Path(__file__).resolve().parents[1] / "src" / "stellaris_autopilot" / "native_species_rights.js"
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
}
const Memory = {copy(dst, src, size) {
    calls.push('copy');
    for (const [address, value] of [...memory.entries()]) {
        if (address >= src.value && address < src.value + size) memory.set(dst.value + address - src.value, value);
    }
}};
const put = (address, value) => memory.set(address, value);
const B = 0x140000000; const base = new Pointer(B);
const C = 0xb0000, VT = B + 0x2393770, RVT = B + 0x2337510;
const COUNTRY = 0x50000, MODULE = 0x52000, PRIMARY = MODULE + 0x20, SPECIES = 0x01000001;
put(COUNTRY + 0x2ab0, MODULE);
put(PRIMARY, RVT); put(PRIMARY + 0xa0, scenario === 'not_primary' ? 0x01000002 : SPECIES);
put(PRIMARY + 0x18, 0x40000);  // living_standard_normal
put(PRIMARY + 0x10, 0x42000);  // citizenship_full (copied unchanged)
// Species pool: index 1 holds SPECIES.
put(B + 0x3114c40, 0x10000); put(0x10000 + 0x18, 0x11000); put(0x10000 + 0x20, 4);
put(0x11000 + 1 * 0x10 + 8, 0x20000); put(0x20000 + 0x10, scenario === 'stale' ? 7 : SPECIES);
// Living standard database (0x3112860) with two rights.
put(B + 0x3112860, 0x30000); put(0x30000 + 0x50, 0x31000); put(0x30000 + 0x5c, 2);
put(0x31000, 0x40000); put(0x31008, 0x41000);
const keys = {0x40000: 'living_standard_normal', 0x42000: 'citizenship_full',
    0x41000: scenario === 'duplicate' ? 'living_standard_normal' : 'living_standard_utopian'};
if (scenario === 'live_changed') keys[0x40000] = 'living_standard_basic';
function NativeFunction(address) {
    const rva = address.value - B;
    return (...values) => {
        calls.push(rva);
        if (rva === 0x6d29d0) {
            put(C, scenario === 'bad_vtable' ? B + 0x2391880 : VT);
            put(VT, B + 0x6c7740); put(VT + 0x40, B + 0x6c7970); put(VT + 0x48, B + 0x6c7a70);
            put(C + 0x20, 0xffffffff); put(C + 0x24, 0x7);
            put(C + 0x28, RVT); put(C + 0xf0, scenario === 'bad_defaults' ? 0 : 1);
            return new Pointer(C);
        }
        return 1;
    };
}
const request = {kind: 'set_species_right', country_id: '0', token: 'a'.repeat(32), species_id: String(SPECIES),
    category: 'living_standard', right: 'living_standard_utopian', current_right: 'living_standard_normal'};
if (scenario === 'unknown_right') request.right = 'living_standard_nope';
if (scenario === 'extra') request.leader_id = '1';
if (scenario === 'wrong_category') request.right = 'citizenship_full';
if (scenario === 'same') request.right = 'living_standard_normal';
class NativePreconditionError extends Error {}
const context = {
    base, request, NativeFunction, NativePreconditionError, Memory,
    qsPlayerCountry: () => new Pointer(COUNTRY),
    qsValidateWithReason: () => { calls.push('validate'); return {allowed: true, reason: ''}; },
    validId: value => typeof value === 'string' && /^(0|[1-9][0-9]{0,9})$/.test(value),
    keyAt: address => keys[address.value] || null,
    destroyUnsubmitted: object => destroyed.push(object.value),
};
try {
    const prepared = vm.runInNewContext(source + '\nprepareSpeciesRightCommand(request);', context);
    process.stdout.write(JSON.stringify({accepted: true, calls, writes, destroyed, meta: prepared.meta,
        validation: prepared.validation, block: {cit: memory.get(C + 0x28 + 0x10), ls: memory.get(C + 0x28 + 0x18),
        species: memory.get(C + 0x28 + 0xa0), vt: memory.get(C + 0x28)}}));
} catch (error) {
    process.stdout.write(JSON.stringify({accepted: false, reason: String(error), calls, destroyed}));
}
"""
C = 0xb0000
SPECIES = 0x01000001


def _run(scenario):
    result = subprocess.run([NODE, "-e", HARNESS, str(SOURCE), scenario], capture_output=True, text=True,
                            timeout=10, check=True)
    return json.loads(result.stdout)


pytestmark = pytest.mark.skipif(NODE is None, reason="Native JavaScript adapter checks require Node.js")


def test_command_copies_observed_rights_and_changes_exactly_one_category():
    result = _run("ok")
    assert result["accepted"] is True and result["validation"] == {"allowed": True, "reason": ""}
    assert result["writes"][:2] == [[C + 0x20, 0], [C + 0x24, SPECIES]]
    assert result["writes"][2:] == [[C + 0x28 + 0x18, 0x41000]]
    assert result["block"] == {"cit": 0x42000, "ls": 0x41000, "species": SPECIES, "vt": 0x140000000 + 0x2337510}
    assert result["calls"] == [0x6d29d0, "copy", "validate"] and result["destroyed"] == []
    assert result["meta"] == {"native_species_handle": SPECIES, "native_right_before": "living_standard_normal"}


@pytest.mark.parametrize("scenario", ["stale", "not_primary", "unknown_right", "duplicate", "live_changed"])
def test_lookups_fail_before_any_allocation(scenario):
    result = _run(scenario)
    assert result["accepted"] is False and 0x6d29d0 not in result["calls"]


@pytest.mark.parametrize("scenario", ["extra", "wrong_category", "same"])
def test_malformed_requests_never_read_game_memory(scenario):
    result = _run(scenario)
    assert result["accepted"] is False and result["calls"] == []


def test_an_unrecognized_command_is_never_destroyed_through_a_guessed_vtable():
    result = _run("bad_vtable")
    assert result["accepted"] is False and result["destroyed"] == [] and "validate" not in result["calls"]


def test_unexpected_defaults_destroy_the_unsubmitted_command():
    result = _run("bad_defaults")
    assert result["accepted"] is False and result["destroyed"] == [C] and "validate" not in result["calls"]


def test_the_router_and_enqueue_chain_know_the_kind():
    router = (SOURCE.parent / "native_research.js").read_text(encoding="utf-8")
    assert "request.kind === 'set_species_right' ? prepareSpeciesRightCommand(request)" in router
    assert "canonicalSpeciesRightRequest(request)" in router
