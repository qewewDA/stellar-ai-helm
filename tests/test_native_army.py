"""Run the 4.5.2 army recruitment adapter on fake memory (no Frida, no game)."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

NODE = shutil.which("node")
SOURCE = Path(__file__).resolve().parents[1] / "src" / "stellaris_autopilot" / "native_army.js"
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
    writePointer(value) { writes.push([this.value, value.value]); memory.set(this.value, value.value); return this; }
    writeU32(value) { writes.push([this.value, value >>> 0]); memory.set(this.value, value >>> 0); return this; }
}
const put = (address, value) => memory.set(address, value);
const B = 0x140000000; const base = new Pointer(B);
const BUILDABLE = 0xb0000, QUEUE = 0xc0000, DEFAULT = 0x41000, HUB = 0x40000;
put(B + 0x31138b0, DEFAULT);
put(B + 0x3112648, 0x30000); put(0x30000 + 0x50, 0x31000); put(0x30000 + 0x5c, 2);
put(0x31000, HUB); put(0x31008, DEFAULT);
const keys = {[HUB]: 'assault_army', [DEFAULT]: scenario === 'duplicate' ? 'assault_army' : 'defensive_army'};
function NativeFunction(address) {
    const rva = address.value - B;
    return () => {
        calls.push(rva);
        if (rva === 0x435ab0) {
            put(BUILDABLE, scenario === 'bad_vtable' ? B + 0x2343850 : B + 0x2376900);
            put(BUILDABLE + 8, DEFAULT); put(BUILDABLE + 0x10, 7);
            put(BUILDABLE + 0x18, 0xffffffff); put(BUILDABLE + 0x20, scenario === 'bad_defaults' ? 0 : 0xffffffff);
            return new Pointer(BUILDABLE);
        }
        throw Error('Unexpected native call ' + rva.toString(16));
    };
}
const request = {kind: 'recruit_army', country_id: '0', token: 'a'.repeat(32), planet_id: '3', colony_id: '0',
    queue_id: '1', species_id: '2', starbase_id: scenario === 'rally' || scenario === 'no_starbase' ? '72' : '4294967295',
    army_type: 'assault_army'};
if (scenario === 'unknown_type') request.army_type = 'nope';
if (scenario === 'extra') request.design_id = '1';
if (scenario === 'bad_id') request.species_id = '02';
if (scenario === 'bad_type') request.army_type = 'Assault';
class NativePreconditionError extends Error {}
const context = {
    base, request, NativeFunction, NativePreconditionError,
    qsControlPoolObject: (rva, handle, identity) => {
        calls.push('starbase'); return rva === 0x3114c60 && identity === 8 && scenario !== 'no_starbase' ? new Pointer(0x60000) : null; },
    planetPoolObject: (rva, handle, offset, label) => {
        calls.push('pool:' + label);
        if (scenario === 'missing_colony' && label === 'Colony') throw new Error('Colony pool entry missing');
        return new Pointer(0x70000); },
    qsStarbaseType: (rva, key, label) => {
        if (rva !== 0x3112648) throw new Error('wrong database');
        const db = base.add(rva).readPointer(); const values = db.add(0x50).readPointer(); const count = db.add(0x5c).readU32();
        let found = null;
        for (let i = 0; i < count; i++) { const v = values.add(i * 8).readPointer(); if (keys[v.value] !== key) continue;
            if (found !== null) throw new Error('Duplicate ' + label); found = v; }
        if (found === null) throw new NativePreconditionError('missing ' + label);
        return found; },
    createQueue: () => { calls.push('createQueue'); return new Pointer(QUEUE); },
    validateQueue: 'validateQueue',
    qsValidateWithReason: (validate, command) => { calls.push('validate:' + validate); return {allowed: true, reason: ''}; },
    validId: value => typeof value === 'string' && /^(0|[1-9][0-9]{0,9})$/.test(value),
    keyAt: address => keys[address.value] || null,
    destroyUnsubmitted: object => destroyed.push(object.value),
};
try {
    const prepared = vm.runInNewContext(source + '\nprepareArmyCommand(request);', context);
    process.stdout.write(JSON.stringify({accepted: true, calls, writes, destroyed, meta: prepared.meta,
        validation: prepared.validation, command: prepared.command.value}));
} catch (error) {
    process.stdout.write(JSON.stringify({accepted: false, reason: String(error), calls, destroyed}));
}
"""
BUILDABLE = 0xb0000
QUEUE = 0xc0000
POOLS = ["pool:Colony", "pool:Physical planet", "pool:Construction queue", "pool:Species"]


def _run(scenario):
    result = subprocess.run([NODE, "-e", HARNESS, str(SOURCE), scenario], capture_output=True, text=True,
                            timeout=10, check=True)
    return json.loads(result.stdout)


pytestmark = pytest.mark.skipif(NODE is None, reason="Native JavaScript adapter checks require Node.js")


def test_planet_army_buildable_is_filled_and_posted_to_the_army_queue():
    result = _run("ok")
    assert result["accepted"] is True and result["validation"] == {"allowed": True, "reason": ""}
    assert result["writes"] == [[BUILDABLE + 8, 0x40000], [BUILDABLE + 0x10, 2], [BUILDABLE + 0x18, 0],
                                [BUILDABLE + 0x20, 0xffffffff], [QUEUE + 0x28, 0], [QUEUE + 0x2c, 1],
                                [QUEUE + 0x20, BUILDABLE]]
    assert result["calls"] == [*POOLS, 0x435ab0, "createQueue", "validate:validateQueue"]
    assert result["destroyed"] == [] and result["command"] == QUEUE
    assert result["meta"] == {"native_army_type": "assault_army", "native_species": "2"}


def test_starbase_path_sets_the_rally_starbase():
    result = _run("rally")
    assert result["accepted"] is True and [BUILDABLE + 0x20, 72] in result["writes"]
    assert result["calls"][:5] == [*POOLS, "starbase"]


@pytest.mark.parametrize("scenario", ["unknown_type", "duplicate", "missing_colony", "no_starbase"])
def test_identity_and_type_checks_fail_before_any_allocation(scenario):
    result = _run(scenario)
    assert result["accepted"] is False and 0x435ab0 not in result["calls"] and result["destroyed"] == []


@pytest.mark.parametrize("scenario", ["extra", "bad_id", "bad_type"])
def test_malformed_requests_never_read_game_memory(scenario):
    result = _run(scenario)
    assert result["accepted"] is False and result["calls"] == []


def test_an_unrecognized_buildable_is_never_destroyed_through_a_guessed_vtable():
    result = _run("bad_vtable")
    assert result["accepted"] is False and result["destroyed"] == [] and "createQueue" not in result["calls"]


def test_unexpected_defaults_destroy_only_the_unsubmitted_buildable():
    result = _run("bad_defaults")
    assert result["accepted"] is False and result["destroyed"] == [BUILDABLE] and "createQueue" not in result["calls"]


def test_the_router_and_enqueue_chain_know_recruit_army():
    router = (SOURCE.parent / "native_research.js").read_text(encoding="utf-8")
    assert "request.kind === 'recruit_army' ? prepareArmyCommand(request)" in router
    assert "canonicalArmyRequest(request)" in router
