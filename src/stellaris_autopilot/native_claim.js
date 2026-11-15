/* Pinned 4.5.2 system claims. Appended after the other sources for the 4.5.2
 * build only; the 4.5.3 profile never includes it. Callers never supply
 * JavaScript, addresses or type pointers.
 *
 * Static evidence (4.5.2): add_system_claim_command (token 0x3a95, factory
 * 0x6d2b40, vtable 0x2392e18: +0x40 validate 0x6c8d70 -> 0x6c9050, +0x48
 * execute 0x6c8d80, +0xa0 writer 0x6c8bb0). The factory allocates 0x30
 * bytes: +0x20 country handle, +0x24 system handle (galactic objects
 * [0x3114e30], identity +8), +0x28 claim count (> 0 required), +0x2c date.
 * The UI path 0x1073130 fills our country, the system, count 1 and the game
 * date ([0x3114700]+0xc0), validates with 0x6c9050 and posts the command.
 */
'use strict';

const qsCreateClaim = new NativeFunction(base.add(0x6d2b40), 'pointer', [], 'win64');
const qsValidateClaim = new NativeFunction(base.add(0x6c8d70), 'uint8', ['pointer', 'pointer'], 'win64');
const qsClaimVtable = 0x2392e18;

function canonicalClaimRequest(request) {
    if (request === null || typeof request !== 'object' || Array.isArray(request) || request.kind !== 'add_system_claim') {
        return null;
    }
    if (Object.keys(request).sort().join(',') !== 'country_id,kind,system_id,token' || request.country_id !== '0' ||
        typeof request.token !== 'string' || !/^[0-9a-f]{32}$/.test(request.token) || !validId(request.system_id)) {
        throw new Error('Malformed typed claim request');
    }
    return {kind: 'add_system_claim', country_id: '0', system_id: request.system_id, token: request.token};
}

function prepareClaimCommand(input) {
    const request = canonicalClaimRequest(input);
    if (request === null) throw new Error('Unknown claim command kind');
    const game = qsEventGame.readPointer();
    if (game.isNull()) throw new NativePreconditionError('The game state is unavailable');
    const player = qsLocalPlayer(game);
    if (player.isNull() || player.add(0x5c).readU32() !== 0) {
        throw new NativePreconditionError('The local human does not play country 0');
    }
    const sender = qsCountryObject(0);
    planetPoolObject(0x3114e30, request.system_id, 8, 'System');
    const date = game.add(0xc0).readU32();
    const command = qsCreateClaim();
    if (command.isNull() || !command.readPointer().equals(base.add(qsClaimVtable))) {
        throw new Error('Claim command layout mismatch'); // Never destroyed through a guessed vtable.
    }
    if (command.add(0x20).readU32() !== 0xffffffff || command.add(0x24).readU32() !== 0xffffffff ||
        command.add(0x28).readU32() !== 0) {
        try { destroyUnsubmitted(command); } catch (_) { /* no second cleanup attempt */ }
        throw new Error('Claim command defaults mismatch');
    }
    command.add(0x20).writeU32(sender.handle);
    command.add(0x24).writeU32(Number(request.system_id));
    command.add(0x28).writeU32(1);
    command.add(0x2c).writeU32(date);
    return {command, validation: qsValidateWithReason(qsValidateClaim, command),
        meta: {native_sender_handle: sender.handle, native_system: request.system_id}};
}
