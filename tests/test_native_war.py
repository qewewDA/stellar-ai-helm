"""Run the 4.5.2 war adapter on fake memory (no Frida, no game)."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

NODE = shutil.which("node")
SOURCE = Path(__file__).resolve().parents[1] / "src" / "stellaris_autopilot" / "native_war.js"
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
    readNumber() { if (!memory.has(this.value)) throw Error('Unmapped ' + this.value.toString(16)); return memory.get(this.value); }
    readPointer() { return new Pointer(this.readNumber()); }
    readU32() { return this.readNumber() >>> 0; }
    readU64() { return { toNumber: () => this.readNumber() >>> 0 }; }
    writeU32(value) { writes.push(['u32', this.value, value >>> 0]); memory.set(this.value, value >>> 0); return this; }
    writePointer(pointer) { writes.push(['ptr', this.value, pointer.value]); memory.set(this.value, pointer.value); return this; }
}
const put = (address, value) => memory.set(address, value);
const B = 0x140000000; const base = new Pointer(B);
const GAME = 0x50000, PLAYER = 0x51000, SENDER = 0x52000, RECEIVER = 0x53000, ACTION = 0xa0000, CMD = 0xc0000, TYPE = 0x70000, WAR = 0x80000;
put(GAME + 0xc0, 777); put(PLAYER + 0x5c, scenario === 'other_player' ? 3 : 0);
put(B + 0x23b2438 + 0xa8, B + 0x940120); put(B + 0x23b2438 + 0xb0, B + 0x940140);
put(B + 0x23b25e0 + 0xa8, B + 0x9addf0); put(B + 0x23b25e0 + 0xb0, B + 0x9adf10);

put(TYPE + 0x20, 0); put(TYPE + 0x30, 12); put(TYPE + 0x38, 15);
function NativeFunction(address) {
    const rva = address.value - B;
    return (...args) => {
        calls.push(rva);
        if (rva === 0x9a9860) {
            const token = args[0];
            put(ACTION, token === 0x2d52 ? (scenario === 'bad_vtable' ? B + 1 : B + 0x23b2438) : B + 0x23b25e0);
            put(ACTION + 0x20, 0xffffffff); put(ACTION + 0x24, 0xffffffff);
            put(ACTION + 0x40, B + 0x10);
            put(ACTION + 0xa8, 0); // not used
            if (token === 0x2d52) {
                put(ACTION + 0x50, scenario === 'bad_defaults' ? 1 : B + 0x239e0a0);
                put(ACTION + 0x58, 0); put(ACTION + 0x60, 1); put(ACTION + 0x64, 0xffffffff);
                put(ACTION + 0x68, 0xffffffff); put(ACTION + 0x70, B + 0x22fe610);
                put(ACTION + 0x78, 0); put(ACTION + 0x80, 0); put(ACTION + 0x88, 0xffffffff);
            } else {
                put(ACTION + 0x60, scenario === 'bad_defaults' ? 7 : 0xffffffff);
                put(ACTION + 0x64, 4);
            }
            return new Pointer(ACTION);
        }
        if (rva === 0x940120) { put(ACTION + 0x20, 0); return; }
        if (rva === 0x940140) { put(ACTION + 0x24, 66); return; }
        if (rva === 0x9addf0) { put(ACTION + 0x20, 0); return; }
        if (rva === 0x9adf10) { put(ACTION + 0x24, 66); return; }
        if (rva === 0x9d8280) {
            put(CMD, B + 0x23b4138); put(CMD + 0x20, 0);
            return new Pointer(CMD);
        }
        throw Error('Unexpected native call ' + rva.toString(16));
    };
}
function requestFor() {
    if (scenario === 'peace' || scenario === 'peace_bad') {
        return {kind: 'offer_peace', country_id: '0', token: 'a'.repeat(32), target_country_id: '66',
            war_id: '7', terms: 'status_quo'};
    }
    const request = {kind: 'declare_war', country_id: '0', token: 'a'.repeat(32), target_country_id: '66',
        war_goal: 'wg_conquest'};
    if (scenario === 'extra') request.extra = '1';
    if (scenario === 'bad_id') request.target_country_id = '066';
    if (scenario === 'bad_goal') request.war_goal = 'conquest';
    return request;
}
class NativePreconditionError extends Error {}
const utf = {readUtf8String: () => scenario === 'missing_goal' ? 'wg_other' : 'wg_conquest'};
const context = {
    base, request: requestFor(), NativeFunction, NativePreconditionError,
    qsEventGame: {readPointer: () => new Pointer(GAME)},
    qsLocalPlayer: () => new Pointer(PLAYER),
    qsCountryObject: id => { calls.push('country:' + id); return {country: new Pointer(id === 0 ? SENDER : RECEIVER), handle: id}; },
    keyAt: (address) => utf.readUtf8String(),
    qsValidateWithReason: (validate, command) => { calls.push('validate'); return {allowed: true, reason: ''}; },
    validId: value => typeof value === 'string' && /^(0|[1-9][0-9]{0,9})$/.test(value),
    destroyUnsubmitted: object => destroyed.push(object.value),
    qsCreateDiploAction: NativeFunction(base.add(0x9a9860)),
    qsCreateDiploPropose: NativeFunction(base.add(0x9d8280)),
    qsSetDiploSender: NativeFunction(base.add(0x940120)),
    qsSetDiploReceiver: NativeFunction(base.add(0x940140)),
    qsValidateDiploPropose: NativeFunction(base.add(0x9d5e90)),
    qsDiploProposeVtable: 0x23b4138,
};
// The source calls new NativeFunction(base.add(...)) at load time, and also
// uses already-defined diplomacy helpers. Provide those as globals after a
// thin prelude that matches names the file expects from earlier sources.
// war-goal DB
put(B + 0x3112808, 0x60000);
put(0x60000 + 0x50, 0x61000);
put(0x60000 + 0x5c, 1);
put(0x61000, TYPE);
put(B + 0x3114d10, 0x81000);
put(0x81000 + 0x18, 0x82000);
put(0x81000 + 0x20, 16);
put(0x82000 + 7 * 16 + 8, WAR);
put(WAR + 0x20, 7);
try {
    const prepared = vm.runInNewContext(source + '\nprepareWarCommand(request);', context);
    process.stdout.write(JSON.stringify({accepted: true, calls, writes, destroyed, meta: prepared.meta,
        command: prepared.command.value, actionOwner: memory.get(CMD + 0x20)}));
} catch (error) {
    process.stdout.write(JSON.stringify({accepted: false, reason: String(error), calls, destroyed, writes}));
}
"""
ACTION = 0xa0000
CMD = 0xc0000
TYPE = 0x70000


def _run(scenario):
    result = subprocess.run([NODE, "-e", HARNESS, str(SOURCE), scenario], capture_output=True, text=True,
                            timeout=10, check=True)
    return json.loads(result.stdout)


pytestmark = pytest.mark.skipif(NODE is None, reason="Native JavaScript adapter checks require Node.js")


def test_declare_war_fills_the_war_goal_and_wraps_the_proposal():
    result = _run("ok")
    assert result["accepted"] is True and result["command"] == CMD and result["destroyed"] == []
    assert result["actionOwner"] == ACTION
    writes = [tuple(item) for item in result["writes"]]
    assert ("ptr", ACTION + 0x58, TYPE) in writes
    assert ("u32", ACTION + 0x64, 0) in writes
    assert ("u32", ACTION + 0x68, 66) in writes
    assert ("ptr", CMD + 0x20, ACTION) in writes
    assert result["meta"]["native_war_goal"] == "wg_conquest"
    assert 0x9a9860 in result["calls"] and 0x9d8280 in result["calls"] and "validate" in result["calls"]


def test_offer_peace_sets_the_war_handle_and_status_quo():
    result = _run("peace")
    assert result["accepted"] is True and result["destroyed"] == []
    writes = [tuple(item) for item in result["writes"]]
    assert ("u32", ACTION + 0x60, 7) in writes
    assert ("u32", ACTION + 0x64, 0) in writes
    assert result["meta"]["native_peace_terms"] == "status_quo"


@pytest.mark.parametrize("scenario", ["other_player"])
def test_preconditions_fail_before_allocation(scenario):
    result = _run(scenario)
    assert result["accepted"] is False and 0x9a9860 not in result["calls"]


@pytest.mark.parametrize("scenario", ["extra", "bad_id", "bad_goal"])
def test_malformed_requests_never_read_game_memory(scenario):
    result = _run(scenario)
    assert result["accepted"] is False and result["calls"] == []


def test_layout_mismatches():
    assert _run("bad_vtable")["destroyed"] == []
    bad = _run("bad_defaults")
    assert bad["accepted"] is False and bad["destroyed"] == [ACTION]


def test_router_knows_the_war_kinds():
    router = (SOURCE.parent / "native_research.js").read_text(encoding="utf-8")
    assert "prepareWarCommand(request)" in router
    assert "canonicalWarRequest(request)" in router
