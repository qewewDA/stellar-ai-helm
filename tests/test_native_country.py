"""Run the 4.5.2 policy/edict command adapter against bounded fake memory.

This checks exact live identity joins and command field layout without Frida
or a game process. The layouts come from recorded PE evidence (normal policy
confirm dialog and edict toggle); a separate live trial is still required.
"""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from stellaris_autopilot import native_builds


NODE = shutil.which("node")
PACKAGE = Path(__file__).resolve().parents[1] / "src" / "stellaris_autopilot"
SOURCE = PACKAGE / "native_country.js"
HARNESS = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const scenario = process.argv[2];
const memory = new Map();
const keys = new Map();
const calls = [];
class Pointer {
    constructor(value) { this.value = value; }
    add(offset) { return new Pointer(this.value + offset); }
    isNull() { return this.value === 0; }
    equals(other) { return other instanceof Pointer && other.value === this.value; }
    readNumber() {
        if (!memory.has(this.value)) throw Error('Unmapped native memory ' + this.value.toString(16));
        return memory.get(this.value);
    }
    readPointer() { return new Pointer(this.readNumber()); }
    readU32() { return this.readNumber() >>> 0; }
    readS32() { return this.readNumber() | 0; }
    writePointer(value) { memory.set(this.value, value.value); return this; }
    writeU32(value) { memory.set(this.value, value); return this; }
}
const put = (address, value) => memory.set(address, value);
const base = new Pointer(0x140000000);
let nextCommand = 0x900000;
const created = [];
function NativeFunction(address, ret, args) {
    const rva = address.value - base.value;
    return (...values) => {
        calls.push(rva);
        if ([0x6d2330, 0x6d21f0, 0x6d2260].includes(rva)) {
            const command = new Pointer(nextCommand); nextCommand += 0x1000;
            created.push({rva, command});
            return command;
        }
        return 1;
    };
}
function db(rva, address, objects) {
    put(base.value + rva, address);
    put(address + 0x50, address + 0x100);
    put(address + 0x5c, objects.length);
    objects.forEach(([object, key], index) => { put(address + 0x100 + index * 8, object); keys.set(object, key); });
}
// Country pool: handle 0 at index 0.
put(base.value + 0x3114c38, 0x10000); put(0x10000 + 0x20, 1); put(0x10000 + 0x18, 0x10100);
put(0x10100 + 8, 0x20000); put(0x20000 + 0x20, scenario === 'wrong_country' ? 5 : 0);
// Policy DB: economic_policy with three options; diplomatic_stance unrelated.
db(0x3112400, 0x30000, [[0x31000, 'economic_policy'], [0x32000, 'diplomatic_stance']]);
put(0x31000 + 0x2e8, 0x31800); put(0x31000 + 0x2f4, 3);
[[0x33000, 'economic_policy_civilian'], [0x34000, 'economic_policy_balanced'], [0x35000, 'economic_policy_military']]
    .forEach(([object, key], index) => { put(0x31800 + index * 8, object); keys.set(object, key); });
put(0x32000 + 0x2e8, 0x32800); put(0x32000 + 0x2f4, 1); put(0x32800, 0x36000); keys.set(0x36000, 'diplo_stance_expansionist');
// Active policies (+0x1bb8, stride 0x20; +8 group, +0x10 option).
put(0x20000 + 0x1bb8, 0x40000); put(0x20000 + 0x1bc4, 2);
put(0x40000 + 8, 0x32000); put(0x40000 + 0x10, 0x36000);
put(0x40020 + 8, 0x31000); put(0x40020 + 0x10, 0x34000);
// Edict DB and active edicts (+0x27f8, stride 0x20; +0x10 edict).
db(0x3112558, 0x50000, [[0x51000, 'fortify_the_border'], [0x52000, 'farming_subsidies']]);
put(0x20000 + 0x27f8, 0x60000); put(0x20000 + 0x2804, 1); put(0x60000 + 0x10, 0x52000);
let request = {kind: 'set_policy', country_id: '0', token: 'a'.repeat(32), policy: 'economic_policy',
    option: 'economic_policy_civilian', current_option: 'economic_policy_balanced'};
if (scenario === 'foreign_option') request.option = 'diplo_stance_expansionist';
if (scenario === 'stale_selection') request.current_option = 'economic_policy_military';
if (scenario === 'same_option') { request.option = 'economic_policy_balanced'; }
if (scenario === 'live_already_selected') put(0x40020 + 0x10, 0x33000);
if (scenario === 'inactive_policy') put(0x20000 + 0x1bc4, 1);
if (scenario === 'extra_field') request.queue_id = '1';
if (scenario.startsWith('edict_') || scenario.startsWith('remove_')) {
    request = {kind: scenario.startsWith('edict_') ? 'activate_edict' : 'deactivate_edict', country_id: '0',
        token: 'b'.repeat(32), edict: scenario.endsWith('_active') ? 'farming_subsidies' : 'fortify_the_border'};
}
if (scenario === 'edict_unknown') request.edict = 'map_the_stars';
class NativePreconditionError extends Error {}
const destroyed = [];
const context = {
    base, request, NativeFunction, NativePreconditionError,
    Memory: {allocUtf8String: text => ({text})},
    keyAt: (pointer, bufferOffset) => keys.get(pointer.value),
    assignString: (target, text, length) => { keys.set(target.value, text.text.slice(0, length)); },
    destroyUnsubmitted: command => destroyed.push(command.value),
};
try {
    const prepared = vm.runInNewContext(source + '\nprepareCountryCommand(request);', context);
    const command = prepared.command.value;
    const read = offset => memory.get(command + offset);
    process.stdout.write(JSON.stringify({accepted: true, factory: created[0].rva,
        group: read(0x20), option: read(0x28), handle: request.kind === 'set_policy' ? read(0x30) : read(0x50),
        name: keys.get(command + 0x20) || null, meta: prepared.meta, destroyed}));
} catch (error) {
    process.stdout.write(JSON.stringify({accepted: false, reason: String(error), created: created.length, destroyed}));
}
"""


def _run(scenario):
    result = subprocess.run([NODE, "-e", HARNESS, str(SOURCE), scenario], capture_output=True, text=True,
                            timeout=10, check=True)
    return json.loads(result.stdout)


pytestmark = pytest.mark.skipif(NODE is None, reason="Native JavaScript adapter checks require Node.js")


def test_policy_command_uses_dialog_layout_with_exact_live_group_and_option():
    result = _run("valid_policy")
    assert result["accepted"] is True and result["factory"] == 0x6d2330
    assert (result["group"], result["option"], result["handle"]) == (0x31000, 0x33000, 0)
    assert result["meta"] == {"native_policy_option_before": "economic_policy_balanced"}


@pytest.mark.parametrize("scenario", ["foreign_option", "stale_selection", "same_option",
                                      "live_already_selected", "inactive_policy", "extra_field",
                                      "wrong_country"])
def test_policy_command_is_rejected_before_any_allocation(scenario):
    result = _run(scenario)
    assert result["accepted"] is False and result["created"] == 0


def test_activate_edict_fills_the_command_name_and_player_handle():
    result = _run("edict_new")
    assert result["accepted"] is True and result["factory"] == 0x6d21f0
    assert result["name"] == "fortify_the_border" and result["handle"] == 0


def test_deactivate_edict_requires_exactly_one_active_entry():
    result = _run("remove_active")
    assert result["accepted"] is True and result["factory"] == 0x6d2260
    assert result["name"] == "farming_subsidies"
    assert _run("remove_inactive")["accepted"] is False


@pytest.mark.parametrize("scenario", ["edict_active", "edict_unknown"])
def test_activate_edict_rejects_active_or_unknown_edicts(scenario):
    result = _run(scenario)
    assert result["accepted"] is False and result["created"] == 0


def test_country_module_is_legacy_only_and_absent_from_the_453_translation():
    legacy = native_builds.render_native_source(native_builds.build_for_hash(native_builds.LEGACY_SHA256), PACKAGE)
    cygnus = native_builds.render_native_source(
        native_builds.build_for_hash(native_builds.CYGNUS_453_SHA256), PACKAGE)
    assert "prepareCountryCommand(request)" in legacy and "function prepareCountryCommand" in legacy
    assert "function prepareCountryCommand" not in cygnus
    for rva in ("0x6d2330", "0x6c0eb0", "0x6d21f0", "0x6c0330", "0x6d2260", "0x6c0890",
                "0x3112400", "0x3112558", "0x3114c38"):
        assert rva in legacy and rva not in cygnus
