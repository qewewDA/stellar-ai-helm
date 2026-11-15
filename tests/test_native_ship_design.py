"""Run the 4.5.2 ship-design adapter on fake memory (no Frida, no game)."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

NODE = shutil.which("node")
SOURCE = Path(__file__).resolve().parents[1] / "src" / "stellaris_autopilot" / "native_ship_design.js"
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
    writeU32(value) { writes.push(['u32', this.value, value >>> 0]); memory.set(this.value, value >>> 0); return this; }
    writePointer(pointer) { writes.push(['ptr', this.value, pointer.value]); memory.set(this.value, pointer.value); return this; }
}
const put = (address, value) => memory.set(address, value);
const B = 0x140000000; const base = new Pointer(B);
const GAME = 0x50000, PLAYER = 0x51000, CMD = 0xc0000, BUF = 0xd0000;
put(GAME + 0xc0, 777); put(PLAYER + 0x5c, scenario === 'other_player' ? 3 : 0);
put(B + 0x23b9008, B + 0x597aa0);
put(B + 0x23b9008 + 0x40, B + 0xa68790);
put(B + 0x23b9008 + 0x48, B + 0xa689f0);
function NativeFunction(address) {
    const rva = address.value - B;
    return (...args) => {
        calls.push(rva === 0x20218ac ? ['alloc', args[0]] : rva);
        if (rva === 0xa71210) {
            put(CMD, scenario === 'bad_vtable' ? B + 1 : B + 0x23b9008);
            put(CMD + 0x20, B + 0x22fe610);
            put(CMD + 0x28, scenario === 'bad_defaults' ? 1 : 0);
            put(CMD + 0x30, 0); put(CMD + 0x34, 0); put(CMD + 0x38, 0xffffffff);
            return new Pointer(CMD);
        }
        if (rva === 0x20218ac) {
            if (scenario === 'alloc_fail') return new Pointer(0);
            return new Pointer(BUF);
        }
        throw Error('Unexpected native call ' + rva.toString(16));
    };
}
const request = {kind: 'set_ship_design', country_id: '0', token: 'a'.repeat(32), ship_id: '3', design_id: '3057'};
if (scenario === 'extra') request.extra = '1';
if (scenario === 'bad_id') request.ship_id = '03';
class NativePreconditionError extends Error {}
const context = {
    base, request, NativeFunction, NativePreconditionError,
    qsEventGame: {readPointer: () => new Pointer(GAME)},
    qsLocalPlayer: () => new Pointer(PLAYER),
    qsControlPoolObject: (rva, handle, offset) => {
        calls.push('pool:' + rva.toString(16) + ':' + handle + ':' + offset);
        if (scenario === 'missing_design' && rva === 0x3114670) return null;
        if (scenario === 'missing_ship' && rva === 0x3114c58) return null;
        return new Pointer(0x70000);
    },
    qsValidateWithReason: (validate, command) => { calls.push('validate'); return {allowed: true, reason: ''}; },
    validId: value => typeof value === 'string' && /^(0|[1-9][0-9]{0,9})$/.test(value),
    destroyUnsubmitted: object => destroyed.push(object.value),
};
try {
    const prepared = vm.runInNewContext(source + '\nprepareShipDesignCommand(request);', context);
    process.stdout.write(JSON.stringify({accepted: true, calls, writes, destroyed, meta: prepared.meta,
        command: prepared.command.value}));
} catch (error) {
    process.stdout.write(JSON.stringify({accepted: false, reason: String(error), calls, destroyed, writes}));
}
"""
CMD = 0xc0000
BUF = 0xd0000


def _run(scenario):
    result = subprocess.run([NODE, "-e", HARNESS, str(SOURCE), scenario], capture_output=True, text=True,
                            timeout=10, check=True)
    return json.loads(result.stdout)


pytestmark = pytest.mark.skipif(NODE is None, reason="Native JavaScript adapter checks require Node.js")


def test_set_ship_design_fills_the_ship_list_and_design():
    result = _run("ok")
    assert result["accepted"] is True and result["command"] == CMD and result["destroyed"] == []
    writes = [tuple(item) for item in result["writes"]]
    assert ("u32", BUF, 3) in writes
    assert ("ptr", CMD + 0x28, BUF) in writes
    assert ("u32", CMD + 0x30, 1) in writes
    assert ("u32", CMD + 0x34, 1) in writes
    assert ("u32", CMD + 0x38, 3057) in writes
    assert result["calls"][:4] == ["pool:3114670:3057:16", "pool:3114c58:3:24", 0xa71210, ["alloc", 1]]
    assert "validate" in result["calls"]
    assert result["meta"] == {"native_ship_handle": 3, "native_design_handle": 3057}


@pytest.mark.parametrize("scenario", ["other_player", "missing_design", "missing_ship"])
def test_preconditions_fail_before_allocation(scenario):
    result = _run(scenario)
    assert result["accepted"] is False and 0xa71210 not in result["calls"]


@pytest.mark.parametrize("scenario", ["extra", "bad_id"])
def test_malformed_requests_never_read_game_memory(scenario):
    result = _run(scenario)
    assert result["accepted"] is False and result["calls"] == []


def test_layout_mismatches():
    assert _run("bad_vtable")["destroyed"] == []
    bad = _run("bad_defaults")
    assert bad["accepted"] is False and bad["destroyed"] == [CMD]


def test_router_knows_the_ship_design_kind():
    router = (SOURCE.parent / "native_research.js").read_text(encoding="utf-8")
    assert "request.kind === 'set_ship_design' ? prepareShipDesignCommand(request)" in router
    assert "canonicalShipDesignRequest(request)" in router
