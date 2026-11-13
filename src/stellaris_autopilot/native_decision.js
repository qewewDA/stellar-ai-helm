/* Pinned 4.5.2 planetary decisions. Appended after the other sources for the
 * 4.5.2 build only; the 4.5.3 profile never includes it. Callers never supply
 * JavaScript, addresses or type pointers.
 *
 * Static evidence (4.5.2): enact_decision_planet_command (token 0x2b85,
 * GetType 0x6b88e0) has vtable 0x2391880 (+0x0 dtor 0x6b2ca0, +0x40 validate
 * 0x6b34c0, +0x48 execute 0x6b2cd0, +0xa0 write 0x6b3a70, +0xa8 read
 * 0x6b3b10). Registered factory 0x6b94e0 allocates 0x38 bytes: +0x20 decision
 * type (default [0x31136b0]), +0x28 carrier {u32 id = -1, +0x2c type byte 0},
 * +0x30 country (-1). Write serializes decision (type+0x20 key), carrier
 * (0x3e7110) and country. The carrier resolver 0xccd000 maps type 0 to the
 * physical planet pool [0x3114e10] (handle at +0x18). Decision types live in
 * [0x3112598] (+0x50 values, +0x5c count, key string at +0x20; lookup
 * 0x6b8d20). The planet view (0x17d0080) fills exactly these fields from the
 * planet and its owner, validates with 0x6b34c0 and posts the command.
 */
'use strict';

const qsDecisionDatabase = base.add(0x3112598);
const qsPhysicalPlanets = base.add(0x3114e10);
const qsDefaultDecision = base.add(0x31136b0);
const qsCreateEnactDecision = new NativeFunction(base.add(0x6b94e0), 'pointer', [], 'win64');
const qsValidateEnactDecision = new NativeFunction(base.add(0x6b34c0), 'uint8', ['pointer', 'pointer'], 'win64');
const qsEnactDecisionVtable = 0x2391880;

function canonicalDecisionRequest(request) {
    if (request === null || typeof request !== 'object' || Array.isArray(request) ||
        request.kind !== 'enact_planet_decision') return null;
    if (Object.keys(request).sort().join(',') !== 'country_id,decision_id,kind,planet_id,token' ||
        request.country_id !== '0' || typeof request.token !== 'string' || !/^[0-9a-f]{32}$/.test(request.token) ||
        !validId(request.planet_id) || typeof request.decision_id !== 'string' ||
        !/^[A-Za-z][A-Za-z0-9_]{0,120}$/.test(request.decision_id)) {
        throw new Error('Malformed typed decision request');
    }
    return {kind: request.kind, country_id: '0', planet_id: request.planet_id,
        decision_id: request.decision_id, token: request.token};
}

function qsDecisionType(key) {
    const database = qsDecisionDatabase.readPointer();
    if (database.isNull()) throw new NativePreconditionError('The decision database is unavailable');
    const values = database.add(0x50).readPointer();
    const count = database.add(0x5c).readU32();
    if (values.isNull() || count < 1 || count > 4096) throw new Error('Unrecognized decision DB layout');
    let found = null;
    for (let index = 0; index < count; index++) {
        const value = values.add(index * 8).readPointer();
        if (value.isNull() || keyAt(value, 0x20, 0x30, 0x38) !== key) continue;
        if (found !== null) throw new Error('Duplicate decision identity');
        found = value;
    }
    if (found === null) throw new NativePreconditionError('The decision is not in the game database');
    return found;
}

function qsPhysicalPlanet(id) {
    const pool = qsPhysicalPlanets.readPointer();
    if (pool.isNull()) throw new NativePreconditionError('The planet pool is unavailable');
    const entries = pool.add(0x18).readPointer();
    const count = pool.add(0x20).readU32();
    if (entries.isNull() || count < 1 || count > 0x100000) throw new NativePreconditionError('Unrecognized planet pool layout');
    const index = id % 0x1000000;
    if (index >= count) throw new NativePreconditionError('The planet is not in the pool');
    const planet = entries.add(index * 0x10 + 8).readPointer();
    if (planet.isNull()) throw new NativePreconditionError('The planet no longer exists');
    if (planet.add(0x18).readU32() !== id) throw new NativePreconditionError('The planet handle does not match its id');
    return planet;
}

function prepareDecisionCommand(input) {
    const request = canonicalDecisionRequest(input);
    if (request === null) throw new Error('Unknown decision command kind');
    const game = qsEventGame.readPointer();
    if (game.isNull()) throw new NativePreconditionError('The game state is unavailable');
    const player = qsLocalPlayer(game);
    if (player.isNull() || player.add(0x5c).readU32() !== 0) {
        throw new NativePreconditionError('The local human does not play country 0');
    }
    const planetId = Number(request.planet_id);
    qsPhysicalPlanet(planetId);
    const decision = qsDecisionType(request.decision_id);
    const command = qsCreateEnactDecision();
    if (command.isNull()) throw new Error('Decision command allocation failed');
    const vtable = command.readPointer();
    // An unrecognized command is never destroyed through a guessed vtable.
    if (!vtable.equals(base.add(qsEnactDecisionVtable)) || !vtable.readPointer().equals(base.add(0x6b2ca0)) ||
        !vtable.add(0x40).readPointer().equals(base.add(0x6b34c0)) ||
        !vtable.add(0x48).readPointer().equals(base.add(0x6b2cd0))) {
        throw new Error('Decision command layout mismatch');
    }
    if (!command.add(0x20).readPointer().equals(qsDefaultDecision.readPointer()) ||
        command.add(0x28).readU32() !== 0xffffffff || command.add(0x2c).readU8() !== 0 ||
        command.add(0x30).readU32() !== 0xffffffff) {
        try { destroyUnsubmitted(command); } catch (_) { /* no second cleanup attempt */ }
        throw new Error('Decision command defaults mismatch');
    }
    command.add(0x20).writePointer(decision);
    command.add(0x28).writeU32(planetId);
    command.add(0x2c).writeU8(0);
    command.add(0x30).writeU32(0);
    return {command, validation: qsValidateWithReason(qsValidateEnactDecision, command),
        meta: {native_planet_handle: planetId, native_decision: request.decision_id}};
}
