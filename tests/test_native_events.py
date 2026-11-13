"""Run the 4.5.2 select-event-option adapter on fake memory.

Checks the open player event join, identity checks and command layout without
Frida or a game process. Layouts come from recorded PE evidence; a separate
live trial is still required.
"""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


NODE = shutil.which("node")
SOURCE = Path(__file__).resolve().parents[1] / "src" / "stellaris_autopilot" / "native_events.js"
HARNESS = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const scenario = process.argv[2];
const memory = new Map();
const strings = new Map();
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
    readU64() { return this.readNumber(); }
    readUtf8String(size) { return strings.get(this.value).slice(0, size); }
}
const put = (address, value) => memory.set(address, value);
const base = new Pointer(0x140000000);
const VTABLE = base.value + 0x23abdd8;
let destroyed = [];
function NativeFunction(address) {
    const rva = address.value - base.value;
    return (...values) => {
        calls.push(rva);
        if (rva === 0x24c580) return new Pointer(0x70000);
        if (rva === 0x2021868) return new Pointer(scenario === 'alloc_fail' ? 0 : 0x80000);
        if (rva === 0x99bfb0) {
            const [command, holder, scope, option, id, human] = values;
            put(command.value, scenario === 'bad_layout' ? 0 : VTABLE);
            put(command.value + 0x20, memory.get(memory.get(holder.value) + 8));
            put(command.value + 0x24, option); put(command.value + 0x28, id); put(command.value + 0x2c, human);
            put(command.value + 0x30, scope.value);
            return command;
        }
        return 1;
    };
}
// Game state with open events 12 (event qs_test.1, 3 options) and 13 (other country).
put(base.value + 0x3114700, 0x10000);
put(0x10000 + 0x180, 0x11000); put(0x10000 + 0x18c, 2);
put(0x11000, 0x20000); put(0x11008, 0x21000);
[[0x20000, 12, 0, 0x30000], [0x21000, 13, 5, 0x30000]].forEach(([entry, id, country, holder]) => {
    put(entry + 8, holder); put(entry + 0x180, id); put(entry + 0x184, country);
});
if (scenario === 'duplicate') put(0x21000 + 0x180, 12);
put(0x30000, 0x40000);  // holder -> event
put(0x40000 + 8, 4200001);  // event handle (namespace 42, event 1)
put(0x40000 + 0x20, 0x41000); put(0x40000 + 0x30, 9); put(0x40000 + 0x38, 31);
strings.set(0x41000, scenario === 'other_event' ? 'qs_test.9' : 'qs_test.1');
put(0x40000 + 0x5f4, 3);
put(0x70000 + 0x48, 7); put(0x70000 + 0x5c, scenario === 'other_human' ? 4 : 0);
const token = 'a'.repeat(32);
const request = {kind: 'choose_event_option', country_id: '0', token, player_event_id: '12',
    event_id: 'qs_test.1', option_index: '2'};
if (scenario === 'missing') request.player_event_id = '14';
if (scenario === 'foreign') request.player_event_id = '13';
if (scenario === 'option_range') request.option_index = '3';
if (scenario === 'extra') request.queue_id = '1';
if (scenario === 'code') request.event_id = 'x"); hostile(';
class NativePreconditionError extends Error {}
const context = {
    base, request, NativeFunction, NativePreconditionError, ptr: value => new Pointer(value),
    validId: value => typeof value === 'string' && /^(0|[1-9][0-9]{0,9})$/.test(value),
    destroyUnsubmitted: command => destroyed.push(command.value),
};
try {
    const prepared = vm.runInNewContext(source + '\nprepareEventCommand(request);', context);
    const command = prepared.command.value;
    const read = offset => memory.get(command + offset);
    process.stdout.write(JSON.stringify({accepted: true, calls, f20: read(0x20), f24: read(0x24), f28: read(0x28),
        f2c: read(0x2c), scope: read(0x30), meta: prepared.meta, validate: typeof prepared.validate, destroyed}));
} catch (error) {
    process.stdout.write(JSON.stringify({accepted: false, reason: String(error), calls, destroyed}));
}
"""


def _run(scenario):
    result = subprocess.run([NODE, "-e", HARNESS, str(SOURCE), scenario], capture_output=True, text=True,
                            timeout=10, check=True)
    return json.loads(result.stdout)


pytestmark = pytest.mark.skipif(NODE is None, reason="Native JavaScript adapter checks require Node.js")


def test_event_option_command_uses_the_open_event_holder_scope_id_and_local_human():
    result = _run("ok")
    assert result["accepted"] is True and result["validate"] == "function"
    assert (result["f20"], result["f24"], result["f28"], result["f2c"]) == (4200001, 2, 12, 7)
    assert result["scope"] == 0x20000 + 0x10
    assert result["meta"] == {"native_event_handle": 4200001, "native_option_count": 3, "native_human": 7}
    assert result["calls"] == [0x24c580, 0x2021868, 0x99bfb0] and result["destroyed"] == []


@pytest.mark.parametrize("scenario", ["missing", "foreign", "duplicate", "other_event", "option_range",
                                      "other_human"])
def test_event_option_identity_checks_run_before_any_allocation(scenario):
    result = _run(scenario)
    assert result["accepted"] is False and 0x2021868 not in result["calls"]


@pytest.mark.parametrize("scenario", ["extra", "code"])
def test_malformed_event_requests_never_read_game_memory(scenario):
    result = _run(scenario)
    assert result["accepted"] is False and result["calls"] == []


def test_a_command_with_an_unexpected_layout_is_destroyed_unsubmitted():
    result = _run("bad_layout")
    assert result["accepted"] is False and result["destroyed"] == [0x80000]
    assert "layout" in result["reason"]


def test_allocation_failure_is_reported():
    result = _run("alloc_fail")
    assert result["accepted"] is False and 0x99bfb0 not in result["calls"]
