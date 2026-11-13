"""Run the 4.5.2 fleet stance / queue priority / defense platform adapter on fake memory.

This checks exact live identity joins and command field layout without Frida
or a game process. Layouts come from recorded PE evidence (normal UI paths);
a separate live trial is still required.
"""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from stellaris_autopilot import native_builds


NODE = shutil.which("node")
PACKAGE = Path(__file__).resolve().parents[1] / "src" / "stellaris_autopilot"
SOURCE = PACKAGE / "native_control.js"
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
    readNumber() {
        if (!memory.has(this.value)) throw Error('Unmapped native memory ' + this.value.toString(16));
        return memory.get(this.value);
    }
    readPointer() { return new Pointer(this.readNumber()); }
    readU32() { return this.readNumber() >>> 0; }
    readS32() { return this.readNumber() | 0; }
    readU8() { return this.readNumber() & 0xff; }
    writePointer(value) { memory.set(this.value, value.value); return this; }
    writeU32(value) { memory.set(this.value, value); return this; }
}
const put = (address, value) => memory.set(address, value);
const base = new Pointer(0x140000000);
let next = 0x900000;
const created = [];
const factories = [0xa70c40, 0xacd870, 0xacd910, 0x8cb340];
function NativeFunction(address) {
    const rva = address.value - base.value;
    return (...values) => {
        calls.push(rva);
        if (factories.includes(rva)) {
            const object = new Pointer(next); next += 0x1000;
            created.push({rva, object: object.value});
            return object;
        }
        if (rva === 0xcd32b0) {  // select design on the implementation at +8
            put(values[0].value + 0x530, values[1]);
            put(values[0].value + 0x52c, scenario === 'platform_bad_stage' ? 1 : 0);
        }
        return 1;
    };
}
function pool(rva, address, objects) {
    put(base.value + rva, address);
    put(address + 0x18, address + 0x100);
    put(address + 0x20, 64);
    for (let index = 0; index < 64; index++) put(address + 0x100 + index * 16 + 8, 0);
    objects.forEach(([handle, object, identity]) => {
        put(address + 0x100 + (handle & 0xffffff) * 16 + 8, object);
        put(object + identity, handle);
    });
}
// Fleets: 3 owned (passive), 4 foreign owner, 5 controlled by another empire.
pool(0x31129d8, 0x10000, [[3, 0x11000, 0x30], [4, 0x12000, 0x30], [5, 0x13000, 0x30]]);
[[0x11000, 0, 0], [0x12000, 1, 1], [0x13000, 0, 2]].forEach(([fleet, owner, controller]) => {
    put(fleet + 0x44c, owner); put(fleet + 0x448, controller); put(fleet + 0x460, 0);
});
// Items 20/21 in queue 3 with order [21, 20]; queue 4 foreign with item 30.
pool(0x3114b90, 0x20000, [[20, 0x21000, 8], [21, 0x22000, 8], [30, 0x23000, 8]]);
put(0x21000 + 0x20, 3); put(0x22000 + 0x20, 3); put(0x23000 + 0x20, 4);
pool(0x3114ba0, 0x30000, [[3, 0x31000, 8], [4, 0x32000, 8]]);
put(0x31000 + 0x50, 0); put(0x31000 + 0x30, 0); put(0x31000 + 0x20, 0x31800); put(0x31000 + 0x2c, 2);
put(0x31800, 21); put(0x31804, 20);
put(0x32000 + 0x50, 0); put(0x32000 + 0x30, 1); put(0x32000 + 0x20, 0x32800); put(0x32000 + 0x2c, 2);
put(0x32800, 30); put(0x32804, 31);
// Starbase 0 (station 10, build queue 2, shipyard queue 3) and design 3058.
pool(0x3114c60, 0x40000, [[0, 0x41000, 8]]);
put(0x41000 + 0x98, 10); put(0x41000 + 0x880, 2); put(0x41000 + 0x884, 3);
pool(0x3114670, 0x50000, [[58, 0x51000, 0x10]]);
const token = 'a'.repeat(32);
let request = {kind: 'set_fleet_stance', country_id: '0', token, fleet_id: '3', stance: 'aggressive', current_stance: 'passive'};
if (scenario === 'stance_foreign') request.fleet_id = '4';
if (scenario === 'stance_controlled') request.fleet_id = '5';
if (scenario === 'stance_stale') request.current_stance = 'evasive';
if (scenario === 'stance_same') request.stance = 'passive';
if (scenario === 'stance_unknown') request.stance = 'hold';
if (scenario === 'stance_extra') request.queue_id = '1';
if (scenario.startsWith('move_')) {
    request = {kind: 'move_queue_item', country_id: '0', token, item_id: '20', queue_id: '3', direction: 'up', neighbor_id: '21'};
    if (scenario === 'move_down') Object.assign(request, {item_id: '21', direction: 'down', neighbor_id: '20'});
    if (scenario === 'move_top') Object.assign(request, {item_id: '21', direction: 'up', neighbor_id: '20'});
    if (scenario === 'move_stale_neighbour') put(0x31800, 22);
    if (scenario === 'move_foreign') Object.assign(request, {item_id: '30', queue_id: '4', direction: 'down', neighbor_id: '31'});
    if (scenario === 'move_wrong_queue') request.queue_id = '4';
}
if (scenario.startsWith('platform')) {
    request = {kind: 'queue_defense_platform', country_id: '0', token, starbase_id: '0', design_id: '58',
        station_id: '10', queue_id: '2'};
    if (scenario === 'platform_shipyard_queue') request.queue_id = '3';
    if (scenario === 'platform_foreign') put(0x41000 + 0x98, 10);
}
class NativePreconditionError extends Error {}
const destroyed = [];
const context = {
    base, request, NativeFunction, NativePreconditionError,
    validId: value => typeof value === 'string' && /^(0|[1-9][0-9]{0,9})$/.test(value),
    shipyardOwner: () => scenario === 'platform_foreign' ? 1 : 0,
    createQueue: () => { const object = new Pointer(next); next += 0x1000; created.push({rva: 0xacd7d0, object: object.value}); return object; },
    validateQueue: 'validateQueue',
    destroyUnsubmitted: command => destroyed.push(command.value),
};
try {
    const prepared = vm.runInNewContext(source + '\nprepareControlCommand(request);', context);
    const command = prepared.command.value;
    const read = offset => memory.get(command + offset);
    const nested = read(0x20);
    process.stdout.write(JSON.stringify({accepted: true, factories: created.map(item => item.rva),
        f20: nested, f24: read(0x24), f28: read(0x28), f2c: read(0x2c), meta: prepared.meta,
        validate: typeof prepared.validate === 'string' ? prepared.validate : null,
        platform: request.kind === 'queue_defense_platform' ? {starbase: memory.get(nested + 0x540)} : null,
        destroyed}));
} catch (error) {
    process.stdout.write(JSON.stringify({accepted: false, reason: String(error), created: created.length, destroyed}));
}
"""


def _run(scenario):
    result = subprocess.run([NODE, "-e", HARNESS, str(SOURCE), scenario], capture_output=True, text=True,
                            timeout=10, check=True)
    return json.loads(result.stdout)


pytestmark = pytest.mark.skipif(NODE is None, reason="Native JavaScript adapter checks require Node.js")


def test_stance_command_writes_fleet_handle_and_native_enum():
    result = _run("stance_ok")
    assert result["accepted"] is True and result["factories"] == [0xa70c40]
    assert (result["f20"], result["f24"]) == (3, 1)
    assert result["meta"] == {"native_stance_before": "passive"}


@pytest.mark.parametrize("scenario", ["stance_foreign", "stance_controlled", "stance_stale", "stance_same",
                                      "stance_unknown", "stance_extra"])
def test_stance_command_is_rejected_before_any_allocation(scenario):
    result = _run(scenario)
    assert result["accepted"] is False and result["created"] == 0


def test_queue_priority_uses_raise_or_lower_factory_with_player_and_item():
    up = _run("move_up")
    assert up["accepted"] is True and up["factories"] == [0xacd870]
    assert (up["f20"], up["f24"], up["meta"]) == (0, 20, {"native_queue_position_before": 1})
    down = _run("move_down")
    assert down["accepted"] is True and down["factories"] == [0xacd910]
    assert (down["f20"], down["f24"]) == (0, 21)


@pytest.mark.parametrize("scenario", ["move_top", "move_stale_neighbour", "move_foreign", "move_wrong_queue"])
def test_queue_priority_requires_the_exact_live_neighbour_in_an_owned_queue(scenario):
    result = _run(scenario)
    assert result["accepted"] is False and result["created"] == 0


def test_defense_platform_uses_the_platform_buildable_in_the_starbase_build_queue():
    result = _run("platform_ok")
    assert result["accepted"] is True and result["factories"] == [0x8cb340, 0xacd7d0]
    assert (result["f28"], result["f2c"], result["validate"]) == (0, 2, "validateQueue")
    assert result["platform"] == {"starbase": 0} and result["destroyed"] == []


@pytest.mark.parametrize("scenario", ["platform_shipyard_queue", "platform_foreign"])
def test_defense_platform_rejects_wrong_queue_or_owner_before_allocation(scenario):
    result = _run(scenario)
    assert result["accepted"] is False and result["created"] == 0


def test_defense_platform_destroys_its_unsubmitted_buildable_on_identity_mismatch():
    result = _run("platform_bad_stage")
    assert result["accepted"] is False and result["created"] == 1 and len(result["destroyed"]) == 1


def test_control_module_is_legacy_only_and_absent_from_the_453_translation():
    legacy = native_builds.render_native_source(native_builds.build_for_hash(native_builds.LEGACY_SHA256), PACKAGE)
    cygnus = native_builds.render_native_source(
        native_builds.build_for_hash(native_builds.CYGNUS_453_SHA256), PACKAGE)
    assert "prepareControlCommand(request)" in legacy and "function prepareControlCommand" in legacy
    assert "function prepareControlCommand" not in cygnus and "function canonicalControlRequest" not in cygnus
    for rva in ("0xa70c40", "0xa61f40", "0xacd870", "0xacc290", "0xacd910", "0xacc700", "0x8cb340",
                "0x31129d8", "0x3114b90", "0x3114ba0"):
        assert rva in legacy and rva not in cygnus
