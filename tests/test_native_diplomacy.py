"""Run the 4.5.2 diplomatic proposal adapter on fake memory.

Checks the country lookup, action/command layout and ownership without Frida
or a game process. Layouts come from recorded PE evidence (see
native_diplomacy.js); a separate live trial is still required.
"""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


NODE = shutil.which("node")
SOURCE = Path(__file__).resolve().parents[1] / "src" / "stellaris_autopilot" / "native_diplomacy.js"
HARNESS = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const scenario = process.argv[2];
const memory = new Map();
const calls = [];
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
    readS32() { return this.readNumber() | 0; }
    writePointer(value) { memory.set(this.value, value.value); return this; }
}
const put = (address, value) => memory.set(address, value);
const B = 0x140000000;
const base = new Pointer(B);
const destroyed = [];
// Action vtable for close borders with the reviewed sender/receiver setters.
const CLOSE = B + 0x23aebc8, OPEN = B + 0x23aed70, PROPOSE = B + 0x23b4138;
[CLOSE, OPEN].forEach(vt => { put(vt + 0xa8, B + 0x940120); put(vt + 0xb0, B + 0x940140); });
function NativeFunction(address) {
    const rva = address.value - B;
    return (...values) => {
        calls.push(rva);
        if (rva === 0x9a9860) {
            const token = values[0];
            if (scenario === 'action_fail') return new Pointer(0);
            put(0x90000, scenario === 'bad_action' ? 0 : token === 0x3695 ? CLOSE : OPEN);
            put(0x90000 + 0x20, 0xffffffff); put(0x90000 + 0x24, 0xffffffff);
            put(0x90000 + 0x40, scenario === 'no_type' ? 0 : 0x95000);
            return new Pointer(0x90000);
        }
        if (rva === 0x940120) { put(values[0].value + 0x20, memory.get(values[1].value + 0x20)); return; }
        if (rva === 0x940140) {
            put(values[0].value + 0x24, scenario === 'bad_fields' ? 1 : memory.get(values[1].value + 0x20)); return;
        }
        if (rva === 0x9d8280) {
            put(0xa0000, scenario === 'bad_command' ? 0 : PROPOSE); put(0xa0000 + 0x20, 0);
            return new Pointer(0xa0000);
        }
        return 1;
    };
}
// Country database: index 0 (player), 8 (handle 8), 9 (stale generation mismatch).
put(B + 0x3114c38, 0x10000);
put(0x10000 + 0x18, 0x11000); put(0x10000 + 0x20, 12);
const country = (index, address, handle) => { put(0x11000 + index * 0x10 + 8, address); put(address + 0x20, handle); };
country(0, 0x20000, 0);
country(8, 0x28000, 0x01000008);
country(9, 0x29000, 0x0100000a);
put(0x11000 + 7 * 0x10 + 8, 0);
put(0x70000 + 0x5c, scenario === 'other_human' ? 4 : 0);
const token = 'a'.repeat(32);
const request = {kind: scenario === 'open' ? 'open_borders' : 'close_borders', country_id: '0', token,
    target_country_id: '8'};
if (scenario === 'self') request.target_country_id = '0';
if (scenario === 'missing') request.target_country_id = '7';
if (scenario === 'mismatch') request.target_country_id = '9';
if (scenario === 'outside') request.target_country_id = '40';
if (scenario === 'extra') request.queue_id = '1';
if (scenario === 'kind') request.kind = 'declare_war';
class NativePreconditionError extends Error {}
const context = {
    base, request, NativeFunction, NativePreconditionError, ptr: value => new Pointer(value),
    qsEventGame: new Pointer(0x60000), qsLocalPlayer: () => new Pointer(0x70000),
    qsValidateWithReason: (validate, command) => { calls.push('validate'); return {allowed: true, reason: ''}; },
    validId: value => typeof value === 'string' && /^(0|[1-9][0-9]{0,9})$/.test(value),
    destroyUnsubmitted: object => destroyed.push(object.value),
};
put(0x60000, 0x61000);
try {
    const prepared = vm.runInNewContext(source + '\nprepareDiplomacyCommand(request);', context);
    const command = prepared.command.value;
    process.stdout.write(JSON.stringify({accepted: true, calls, owned: memory.get(command + 0x20),
        sender: memory.get(0x90000 + 0x20), receiver: memory.get(0x90000 + 0x24), action_vtable: memory.get(0x90000),
        meta: prepared.meta, validation: prepared.validation, destroyed}));
} catch (error) {
    process.stdout.write(JSON.stringify({accepted: false, reason: String(error), calls, destroyed}));
}
"""
B = 0x140000000


def _run(scenario):
    result = subprocess.run([NODE, "-e", HARNESS, str(SOURCE), scenario], capture_output=True, text=True,
                            timeout=10, check=True)
    return json.loads(result.stdout)


pytestmark = pytest.mark.skipif(NODE is None, reason="Native JavaScript adapter checks require Node.js")


@pytest.mark.parametrize("scenario,token,vtable", [("ok", 0x3695, 0x23aebc8), ("open", 0x3696, 0x23aed70)])
def test_proposal_owns_one_action_with_player_sender_and_exact_receiver(scenario, token, vtable):
    result = _run(scenario)
    assert result["accepted"] is True and result["validation"] == {"allowed": True, "reason": ""}
    assert result["owned"] == 0x90000 and result["action_vtable"] == B + vtable
    assert (result["sender"], result["receiver"]) == (0, 0x01000008)
    assert result["meta"] == {"native_sender_handle": 0, "native_receiver_handle": 0x01000008,
                              "native_action_token": token}
    assert result["calls"] == [0x9a9860, 0x940120, 0x940140, 0x9d8280, "validate"]
    assert result["destroyed"] == []


@pytest.mark.parametrize("scenario", ["self", "missing", "mismatch", "outside", "other_human"])
def test_country_checks_run_before_any_allocation(scenario):
    result = _run(scenario)
    assert result["accepted"] is False and 0x9a9860 not in result["calls"]


@pytest.mark.parametrize("scenario", ["extra", "kind"])
def test_malformed_requests_never_read_game_memory(scenario):
    result = _run(scenario)
    assert result["accepted"] is False and result["calls"] == []


@pytest.mark.parametrize("scenario", ["bad_action", "no_type", "bad_fields"])
def test_an_unexpected_action_is_destroyed_before_any_command(scenario):
    result = _run(scenario)
    assert result["accepted"] is False and result["destroyed"] == [0x90000]
    assert 0x9d8280 not in result["calls"]


def test_an_unexpected_command_destroys_only_the_unowned_action():
    result = _run("bad_command")
    assert result["accepted"] is False and result["destroyed"] == [0x90000]
    assert "validate" not in result["calls"]


def test_action_allocation_failure_is_reported():
    result = _run("action_fail")
    assert result["accepted"] is False and 0x9d8280 not in result["calls"]
