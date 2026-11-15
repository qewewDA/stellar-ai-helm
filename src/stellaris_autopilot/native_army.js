/* Pinned 4.5.2 army recruitment. Appended after the other sources for the
 * 4.5.2 build only; the 4.5.3 profile never includes it. Callers never supply
 * JavaScript, addresses or type pointers.
 *
 * Static evidence (4.5.2): buildable registration 0x2e680 (token 0x3dff
 * buildable_army, factory 0x435ab0, vtable 0x2376900, default type
 * [0x31138b0]). The factory allocates 0x28 bytes: +0x8 army type pointer,
 * +0x10 species handle (pool [0x3114c40]), +0x18 colony handle (pool
 * [0x3114e28], read by validate 0xa2f9b0), +0x20 starbase handle (-1 for
 * planets). Writer 0xa30050 saves army_type=<type+0x20 key>, species,
 * planet (the colony handle) and starbase. Army types live in [0x3112648]
 * (+0x50 values, +0x5c count, key at +0x20; lookup 0x42e9c0). The buildable
 * is posted through add_buildable_to_queue_command (0xacd7d0, validate
 * 0xacb850) to the colony's army_build_queue.
 */
'use strict';

function canonicalArmyRequest(request) {
    if (request === null || typeof request !== 'object' || Array.isArray(request) || request.kind !== 'recruit_army') {
        return null;
    }
    const fields = ['army_type', 'colony_id', 'country_id', 'kind', 'planet_id', 'queue_id', 'species_id', 'starbase_id',
        'token'];
    if (Object.keys(request).sort().join(',') !== fields.join(',') || request.country_id !== '0' ||
        typeof request.token !== 'string' || !/^[0-9a-f]{32}$/.test(request.token) ||
        !['colony_id', 'planet_id', 'queue_id', 'species_id'].every(key => validId(request[key])) ||
        (request.starbase_id !== '4294967295' && !validId(request.starbase_id)) ||
        typeof request.army_type !== 'string' || !/^[a-z][a-z0-9_]{0,120}$/.test(request.army_type)) {
        throw new Error('Malformed typed army request');
    }
    return {kind: 'recruit_army', country_id: '0', planet_id: request.planet_id, colony_id: request.colony_id,
        queue_id: request.queue_id, species_id: request.species_id, army_type: request.army_type,
        starbase_id: request.starbase_id, token: request.token};
}

function prepareArmyCommand(input) {
    const request = canonicalArmyRequest(input);
    if (request === null) throw new Error('Unknown army command kind');
    planetPoolObject(0x3114e28, request.colony_id, 0x10, 'Colony');
    planetPoolObject(0x3114e10, request.planet_id, 0x18, 'Physical planet');
    planetPoolObject(0x3114ba0, request.queue_id, 8, 'Construction queue');
    planetPoolObject(0x3114c40, request.species_id, 0x10, 'Species');
    if (request.starbase_id !== '4294967295' && qsControlPoolObject(0x3114c60, request.starbase_id, 8) === null) {
        throw new NativePreconditionError('Rally starbase unavailable');
    }
    const type = qsStarbaseType(0x3112648, request.army_type, 'army type');
    let command = null;
    let buildable = null;
    try {
        buildable = new NativeFunction(base.add(0x435ab0), 'pointer', [], 'win64')();
        if (buildable.isNull()) throw new Error('Army buildable allocation failed');
        if (!buildable.readPointer().equals(base.add(0x2376900))) {
            buildable = null; // An unrecognized object is never destroyed through a guessed vtable.
            throw new Error('Army buildable layout mismatch');
        }
        if (!buildable.add(8).readPointer().equals(base.add(0x31138b0).readPointer()) ||
            buildable.add(0x18).readU32() !== 0xffffffff || buildable.add(0x20).readU32() !== 0xffffffff) {
            throw new Error('Army buildable defaults mismatch');
        }
        buildable.add(8).writePointer(type);
        buildable.add(0x10).writeU32(Number(request.species_id));
        buildable.add(0x18).writeU32(Number(request.colony_id));
        buildable.add(0x20).writeU32(Number(request.starbase_id)); // -1 or the rally starbase (slot 0x20 reads pool [0x3114c60]).
        command = createQueue();
        if (command.isNull()) throw new Error('Army queue command allocation failed');
        command.add(0x28).writeU32(0);
        command.add(0x2c).writeU32(Number(request.queue_id));
        const nested = buildable;
        buildable = null; // Transfer is attempted once; never destroy aliases.
        command.add(0x20).writePointer(nested);
        return {command, validation: qsValidateWithReason(validateQueue, command),
            meta: {native_army_type: request.army_type, native_species: request.species_id}};
    } catch (error) {
        if (command !== null && !command.isNull()) {
            const abandoned = command; command = null;
            try { destroyUnsubmitted(abandoned); } catch (_) { /* no second cleanup attempt */ }
        }
        if (buildable !== null && !buildable.isNull()) {
            const abandoned = buildable; buildable = null;
            try { destroyUnsubmitted(abandoned); } catch (_) { /* no second cleanup attempt */ }
        }
        throw error;
    }
}
