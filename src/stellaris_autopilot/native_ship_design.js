/* Pinned 4.5.2 set_ship_design_command. Appended after the other sources for
 * the 4.5.2 build only; the 4.5.3 profile never includes it. Callers never
 * supply JavaScript, addresses or type pointers.
 *
 * Static evidence (4.5.2): token 0x3b4b, factory 0xa71210, vtable 0x23b9008
 * (+0x0 dtor 0x597aa0, +0x40 validate 0xa68790, +0x48 execute 0xa689f0,
 * writer 0xa68880 emits design then ships). Size 0x40: +0x20 is a uint32
 * array (data +0x28, capacity +0x30, count +0x34) of ship handles (pool
 * [0x3114c58], identity +0x18); +0x38 is the design handle (pool [0x3114670],
 * identity +0x10). The factory leaves +0x20 at the empty sentinel 0x22fe610
 * and +0x28/+0x30/+0x38 as null/0/-1. The destructor 0x597aa0 frees +0x28
 * with 0x20218a4, so the array is allocated with the matching 0x20218ac
 * (element count in ecx). Execute walks the ship list and applies the design.
 */
'use strict';

const qsCreateShipDesign = new NativeFunction(base.add(0xa71210), 'pointer', [], 'win64');
const qsValidateShipDesign = new NativeFunction(base.add(0xa68790), 'uint8', ['pointer', 'pointer'], 'win64');
const qsAllocShipIds = new NativeFunction(base.add(0x20218ac), 'pointer', ['uint32'], 'win64');
const qsShipDesignVtable = 0x23b9008;
const qsEmptyShipList = 0x22fe610;

function canonicalShipDesignRequest(request) {
    if (request === null || typeof request !== 'object' || Array.isArray(request) ||
        request.kind !== 'set_ship_design') {
        return null;
    }
    if (Object.keys(request).sort().join(',') !== 'country_id,design_id,kind,ship_id,token' ||
        request.country_id !== '0' || typeof request.token !== 'string' || !/^[0-9a-f]{32}$/.test(request.token) ||
        !validId(request.ship_id) || !validId(request.design_id)) {
        throw new Error('Malformed typed ship-design request');
    }
    return {kind: 'set_ship_design', country_id: '0', ship_id: request.ship_id,
        design_id: request.design_id, token: request.token};
}

function prepareShipDesignCommand(input) {
    const request = canonicalShipDesignRequest(input);
    if (request === null) throw new Error('Unknown ship-design command kind');
    const game = qsEventGame.readPointer();
    if (game.isNull()) throw new NativePreconditionError('The game state is unavailable');
    const player = qsLocalPlayer(game);
    if (player.isNull() || player.add(0x5c).readU32() !== 0) {
        throw new NativePreconditionError('The local human does not play country 0');
    }
    const designId = Number(request.design_id);
    const shipId = Number(request.ship_id);
    if (qsControlPoolObject(0x3114670, request.design_id, 0x10) === null) {
        throw new NativePreconditionError('The ship design is not in the design pool');
    }
    if (qsControlPoolObject(0x3114c58, request.ship_id, 0x18) === null) {
        throw new NativePreconditionError('The ship is not in the ship pool');
    }
    const command = qsCreateShipDesign();
    if (command.isNull() || !command.readPointer().equals(base.add(qsShipDesignVtable)) ||
        !command.readPointer().readPointer().equals(base.add(0x597aa0)) ||
        !command.readPointer().add(0x40).readPointer().equals(base.add(0xa68790)) ||
        !command.readPointer().add(0x48).readPointer().equals(base.add(0xa689f0))) {
        throw new Error('Ship-design command layout mismatch');
    }
    if (!command.add(0x20).readPointer().equals(base.add(qsEmptyShipList)) ||
        !command.add(0x28).readPointer().isNull() || command.add(0x30).readU32() !== 0 ||
        command.add(0x34).readU32() !== 0 || command.add(0x38).readU32() !== 0xffffffff) {
        try { destroyUnsubmitted(command); } catch (_) { /* no second cleanup attempt */ }
        throw new Error('Ship-design command defaults mismatch');
    }
    const buffer = qsAllocShipIds(1);
    if (buffer.isNull()) {
        try { destroyUnsubmitted(command); } catch (_) { /* no second cleanup attempt */ }
        throw new Error('Ship-design list allocation failed');
    }
    buffer.writeU32(shipId);
    command.add(0x28).writePointer(buffer);
    command.add(0x30).writeU32(1);
    command.add(0x34).writeU32(1);
    command.add(0x38).writeU32(designId);
    return {command, validation: qsValidateWithReason(qsValidateShipDesign, command),
        meta: {native_ship_handle: shipId, native_design_handle: designId}};
}
