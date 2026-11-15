"""Run the 4.5.2 claim adapter on fake memory (no Frida, no game)."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

NODE = shutil.which("node")
SOURCE = Path(__file__).resolve().parents[1] / "src" / "stellaris_autopilot" / "native_claim.js"
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
    writeU32(value) { writes.push([this.value, value >>> 0]); memory.set(this.value, value >>> 0); return this; }
}
const put = (address, value) => memory.set(address, value);
const B = 0x140000000; const base = new Pointer(B); const GAME = 0x50000, PLAYER = 0x51000, CMD = 0xc0000;
put(GAME + 0xc0, 777); put(PLAYER + 0x5c, scenario === 'other_player' ? 3 : 0);
function NativeFunction(address) {
    const rva = address.value - B;
    return () => {
        calls.push(rva);
        if (rva === 0x6d2b40) {
            put(CMD, scenario === 'bad_vtable' ? B + 1 : B + 0x2392e18);
            put(CMD + 0x20, 0xffffffff); put(CMD + 0x24, 0xffffffff); put(CMD + 0x28, scenario === 'bad_defaults' ? 5 : 0);
            put(CMD + 0x2c, 0x29c55c0);
            return new Pointer(CMD);
        }
        throw Error('Unexpected native call ' + rva.toString(16));
    };
}
const request = {kind: 'add_system_claim', country_id: '0', token: 'a'.repeat(32), system_id: '11'};
if (scenario === 'extra') request.claims = '2';
if (scenario === 'bad_id') request.system_id = '011';
class NativePreconditionError extends Error {}
const context = {
    base, request, NativeFunction, NativePreconditionError,
    qsEventGame: {readPointer: () => new Pointer(GAME)},
    qsLocalPlayer: () => new Pointer(PLAYER),
    qsCountryObject: id => { calls.push('country:' + id); return {country: new Pointer(0x52000), handle: 0}; },
    planetPoolObject: (rva, handle, offset, label) => {
        calls.push('pool:' + label + ':' + rva.toString(16) + ':' + offset);
        if (scenario === 'missing_system') throw new Error('System handle unavailable');
        return new Pointer(0x70000); },
    qsValidateWithReason: (validate, command) => { calls.push('validate'); return {allowed: true, reason: ''}; },
    validId: value => typeof value === 'string' && /^(0|[1-9][0-9]{0,9})$/.test(value),
    destroyUnsubmitted: object => destroyed.push(object.value),
};
try {
    const prepared = vm.runInNewContext(source + '\nprepareClaimCommand(request);', context);
    process.stdout.write(JSON.stringify({accepted: true, calls, writes, destroyed, meta: prepared.meta,
        command: prepared.command.value}));
} catch (error) {
    process.stdout.write(JSON.stringify({accepted: false, reason: String(error), calls, destroyed}));
}
"""
CMD = 0xc0000


def _run(scenario):
    result = subprocess.run([NODE, "-e", HARNESS, str(SOURCE), scenario], capture_output=True, text=True,
                            timeout=10, check=True)
    return json.loads(result.stdout)


pytestmark = pytest.mark.skipif(NODE is None, reason="Native JavaScript adapter checks require Node.js")


def test_claim_command_matches_the_ui_fill():
    result = _run("ok")
    assert result["accepted"] is True and result["command"] == CMD and result["destroyed"] == []
    assert result["writes"] == [[CMD + 0x20, 0], [CMD + 0x24, 11], [CMD + 0x28, 1], [CMD + 0x2c, 777]]
    assert result["calls"] == ["country:0", "pool:System:3114e30:8", 0x6d2b40, "validate"]


@pytest.mark.parametrize("scenario", ["other_player", "missing_system"])
def test_preconditions_fail_before_allocation(scenario):
    result = _run(scenario)
    assert result["accepted"] is False and 0x6d2b40 not in result["calls"]


@pytest.mark.parametrize("scenario", ["extra", "bad_id"])
def test_malformed_requests_never_read_game_memory(scenario):
    result = _run(scenario)
    assert result["accepted"] is False and result["calls"] == []


def test_layout_mismatches():
    assert _run("bad_vtable")["destroyed"] == []
    bad = _run("bad_defaults")
    assert bad["accepted"] is False and bad["destroyed"] == [CMD]


def test_router_knows_the_claim_kind():
    router = (SOURCE.parent / "native_research.js").read_text(encoding="utf-8")
    assert "request.kind === 'add_system_claim' ? prepareClaimCommand(request)" in router
    assert "canonicalClaimRequest(request)" in router
