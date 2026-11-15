"""Run the 4.5.2 starbase construction adapter on fake memory (no Frida, no game)."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

NODE = shutil.which("node")
SOURCE = Path(__file__).resolve().parents[1] / "src" / "stellaris_autopilot" / "native_starbase.js"
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
const BUILDABLE = 0xb0000, QUEUE = 0xc0000, STARBASE = 0x60000, DEFAULT = 0x41000, HUB = 0x40000;
put(STARBASE + 0x98, 10); put(STARBASE + 0x880, scenario === 'wrong_queue' ? 9 : 2);
put(B + 0x3113d20, DEFAULT);
put(B + 0x3112358, 0x30000); put(0x30000 + 0x50, 0x31000); put(0x30000 + 0x5c, 2);
put(0x31000, HUB); put(0x31008, DEFAULT);
const keys = {[HUB]: 'trading_hub', [DEFAULT]: scenario === 'duplicate' ? 'trading_hub' : 'shipyard'};
function NativeFunction(address) {
    const rva = address.value - B;
    return () => {
        calls.push(rva);
        if (rva === 0x227ef0) {
            put(BUILDABLE, scenario === 'bad_vtable' ? B + 0x2343850 : B + 0x23436f0);
            put(BUILDABLE + 8, DEFAULT);
            put(BUILDABLE + 0x10, 0xffffffff); put(BUILDABLE + 0x14, scenario === 'bad_defaults' ? 0 : 0xffffffff);
            return new Pointer(BUILDABLE);
        }
        throw Error('Unexpected native call ' + rva.toString(16));
    };
}
const request = {kind: 'queue_starbase_module', country_id: '0', token: 'a'.repeat(32), starbase_id: '72',
    station_id: '10', queue_id: '2', slot: '1', module_id: 'trading_hub'};
if (scenario === 'unknown_type') request.module_id = 'nope';
if (scenario === 'extra') request.design_id = '1';
if (scenario === 'bad_slot') request.slot = '01';
if (scenario === 'building_field') { delete request.module_id; request.building_id = 'trading_hub'; }
class NativePreconditionError extends Error {}
const context = {
    base, request, NativeFunction, NativePreconditionError,
    qsControlPoolObject: (rva, handle, identity) => {
        calls.push('pool'); return rva === 0x3114c60 && handle === '72' && identity === 8 ? new Pointer(STARBASE) : null; },
    shipyardOwner: () => scenario === 'foreign' ? 1 : 0,
    createQueue: () => { calls.push('createQueue'); return new Pointer(QUEUE); },
    validateQueue: 'validateQueue',
    qsValidateWithReason: (validate, command) => { calls.push('validate:' + validate); return {allowed: true, reason: ''}; },
    validId: value => typeof value === 'string' && /^(0|[1-9][0-9]{0,9})$/.test(value),
    keyAt: address => keys[address.value] || null,
    destroyUnsubmitted: object => destroyed.push(object.value),
};
try {
    const prepared = vm.runInNewContext(source + '\nprepareStarbaseCommand(request);', context);
    process.stdout.write(JSON.stringify({accepted: true, calls, writes, destroyed, meta: prepared.meta,
        validation: prepared.validation, command: prepared.command.value}));
} catch (error) {
    process.stdout.write(JSON.stringify({accepted: false, reason: String(error), calls, destroyed}));
}
"""
BUILDABLE = 0xb0000
QUEUE = 0xc0000


def _run(scenario):
    result = subprocess.run([NODE, "-e", HARNESS, str(SOURCE), scenario], capture_output=True, text=True,
                            timeout=10, check=True)
    return json.loads(result.stdout)


pytestmark = pytest.mark.skipif(NODE is None, reason="Native JavaScript adapter checks require Node.js")


def test_module_buildable_is_filled_and_posted_like_a_defense_platform():
    result = _run("ok")
    assert result["accepted"] is True and result["validation"] == {"allowed": True, "reason": ""}
    assert result["writes"] == [[BUILDABLE + 8, 0x40000], [BUILDABLE + 0x10, 72], [BUILDABLE + 0x14, 1],
                                [QUEUE + 0x28, 0], [QUEUE + 0x2c, 2], [QUEUE + 0x20, BUILDABLE]]
    assert result["calls"] == ["pool", 0x227ef0, "createQueue", "validate:validateQueue"]
    assert result["destroyed"] == [] and result["command"] == QUEUE
    assert result["meta"] == {"native_starbase_type": "trading_hub", "native_slot": 1}


@pytest.mark.parametrize("scenario", ["wrong_queue", "foreign", "unknown_type", "duplicate"])
def test_identity_and_type_checks_fail_before_any_allocation(scenario):
    result = _run(scenario)
    assert result["accepted"] is False and 0x227ef0 not in result["calls"] and result["destroyed"] == []


@pytest.mark.parametrize("scenario", ["extra", "bad_slot", "building_field"])
def test_malformed_requests_never_read_game_memory(scenario):
    result = _run(scenario)
    assert result["accepted"] is False and result["calls"] == []


def test_an_unrecognized_buildable_is_never_destroyed_through_a_guessed_vtable():
    result = _run("bad_vtable")
    assert result["accepted"] is False and result["destroyed"] == [] and "createQueue" not in result["calls"]


def test_unexpected_defaults_destroy_only_the_unsubmitted_buildable():
    result = _run("bad_defaults")
    assert result["accepted"] is False and result["destroyed"] == [BUILDABLE] and "createQueue" not in result["calls"]


def test_the_router_and_enqueue_chain_know_both_kinds():
    router = (SOURCE.parent / "native_research.js").read_text(encoding="utf-8")
    assert ("(request.kind === 'queue_starbase_module' || request.kind === 'queue_starbase_building')"
            " ? prepareStarbaseCommand(request)") in router
    assert "canonicalStarbaseRequest(request)" in router
