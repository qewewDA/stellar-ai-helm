/* Pinned 4.5.2 starbase module and building construction. Appended after
 * the other sources for the 4.5.2 build only; the 4.5.3 profile never
 * includes it. Callers never supply JavaScript, addresses or type pointers.
 *
 * Static evidence (4.5.2): buildable registrations 0x43c4 (token 0x3e01
 * buildable_starbase_module, factory 0x227ef0, vtable 0x23436f0, default type
 * [0x3113d20]) and 0x4324 (token 0x3e00 buildable_starbase_building, factory
 * 0x227dc0, vtable 0x2343850, default type [0x3113d28]). Both allocate 0x20
 * bytes: +0x8 type pointer, +0x10 starbase handle, +0x14 slot (writers
 * 0x8c4090/0x8c2640 save starbase_module|starbase_building=<type+0x20 key>,
 * slot, starbase). Types live in [0x3112358] (modules, lookup 0x8c8c40) and
 * [0x3112360] (buildings, lookup 0x8c75e0); +0x50 values, +0x5c count, key at
 * +0x20. The buildable is posted like a defense platform through
 * add_buildable_to_queue_command (0xacd7d0, validate 0xacb850).
 */
'use strict';

const qsStarbaseBuildables = {
    queue_starbase_module: {factory: 0x227ef0, vtable: 0x23436f0, defaultType: 0x3113d20, database: 0x3112358,
        field: 'module_id', label: 'starbase module'},
    queue_starbase_building: {factory: 0x227dc0, vtable: 0x2343850, defaultType: 0x3113d28, database: 0x3112360,
        field: 'building_id', label: 'starbase building'},
};

function canonicalStarbaseRequest(request) {
    if (request === null || typeof request !== 'object' || Array.isArray(request) ||
        !Object.prototype.hasOwnProperty.call(qsStarbaseBuildables, request.kind)) return null;
    const spec = qsStarbaseBuildables[request.kind];
    const fields = ['country_id', spec.field, 'kind', 'queue_id', 'slot', 'starbase_id', 'station_id', 'token'].sort();
    if (Object.keys(request).sort().join(',') !== fields.join(',') || request.country_id !== '0' ||
        typeof request.token !== 'string' || !/^[0-9a-f]{32}$/.test(request.token) ||
        !['queue_id', 'starbase_id', 'station_id'].every(key => validId(request[key])) ||
        typeof request.slot !== 'string' || !/^(0|[1-9][0-9]?)$/.test(request.slot) ||
        typeof request[spec.field] !== 'string' || !/^[a-z][a-z0-9_]{0,120}$/.test(request[spec.field])) {
        throw new Error('Malformed typed starbase construction request');
    }
    const canonical = {kind: request.kind, country_id: '0', starbase_id: request.starbase_id,
        station_id: request.station_id, queue_id: request.queue_id, slot: request.slot, token: request.token};
    canonical[spec.field] = request[spec.field];
    return canonical;
}

function qsStarbaseType(databaseRva, key, label) {
    const database = base.add(databaseRva).readPointer();
    if (database.isNull()) throw new NativePreconditionError('The ' + label + ' database is unavailable');
    const values = database.add(0x50).readPointer();
    const count = database.add(0x5c).readU32();
    if (values.isNull() || count < 1 || count > 1024) throw new Error('Unrecognized ' + label + ' DB layout');
    let found = null;
    for (let index = 0; index < count; index++) {
        const value = values.add(index * 8).readPointer();
        if (value.isNull() || keyAt(value, 0x20, 0x30, 0x38) !== key) continue;
        if (found !== null) throw new Error('Duplicate ' + label + ' identity');
        found = value;
    }
    if (found === null) throw new NativePreconditionError('The ' + label + ' is not in the game database');
    return found;
}

function prepareStarbaseCommand(input) {
    const request = canonicalStarbaseRequest(input);
    if (request === null) throw new Error('Unknown starbase command kind');
    const spec = qsStarbaseBuildables[request.kind];
    // Starbase pool 0x3114c60 (identity +8; +0x98 station, +0x880 build queue).
    const starbase = qsControlPoolObject(0x3114c60, request.starbase_id, 8);
    if (starbase === null || shipyardOwner(request.starbase_id) !== 0 ||
        starbase.add(0x98).readU32() !== Number(request.station_id) ||
        starbase.add(0x880).readU32() !== Number(request.queue_id)) {
        throw new NativePreconditionError('Starbase identity mismatch');
    }
    const type = qsStarbaseType(spec.database, request[spec.field], spec.label);
    let command = null;
    let buildable = null;
    try {
        buildable = new NativeFunction(base.add(spec.factory), 'pointer', [], 'win64')();
        if (buildable.isNull()) throw new Error('Starbase buildable allocation failed');
        if (!buildable.readPointer().equals(base.add(spec.vtable))) {
            buildable = null; // An unrecognized object is never destroyed through a guessed vtable.
            throw new Error('Starbase buildable layout mismatch');
        }
        if (!buildable.add(8).readPointer().equals(base.add(spec.defaultType).readPointer()) ||
            buildable.add(0x10).readU32() !== 0xffffffff || buildable.add(0x14).readU32() !== 0xffffffff) {
            throw new Error('Starbase buildable defaults mismatch');
        }
        buildable.add(8).writePointer(type);
        buildable.add(0x10).writeU32(Number(request.starbase_id));
        buildable.add(0x14).writeU32(Number(request.slot));
        command = createQueue();
        if (command.isNull()) throw new Error('Starbase queue command allocation failed');
        command.add(0x28).writeU32(0);
        command.add(0x2c).writeU32(Number(request.queue_id));
        const nested = buildable;
        buildable = null; // Transfer is attempted once; never destroy aliases.
        command.add(0x20).writePointer(nested);
        return {command, validation: qsValidateWithReason(validateQueue, command),
            meta: {native_starbase_type: request[spec.field], native_slot: Number(request.slot)}};
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
