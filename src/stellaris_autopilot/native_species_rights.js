/* Pinned 4.5.2 species rights of the primary species. Appended after the
 * other sources for the 4.5.2 build only; the 4.5.3 profile never includes
 * it. Callers never supply JavaScript, addresses or type pointers.
 *
 * Static evidence (4.5.2): set_species_right_command (token 0x3832, GetType
 * 0x6ced20) has vtable 0x2393770 (+0x0 dtor 0x6c7740, +0x40 validate
 * 0x6c7970, +0x48 execute 0x6c7a70, +0x60 clone 0x6cec70). Registered
 * factory 0x6d29d0 allocates 0xf8 bytes: +0x20 country handle (pool
 * [0x3114c38], handle at +0x20), +0x24 species handle (pool [0x3114c40],
 * handle at +0x10), +0x28 a rights block (ctor 0x399880, vtable 0x2337510,
 * size 0xc8), +0xf0 byte 1. Rights block (writer 0x399920): +0x10
 * citizenship, +0x18 living_standard, +0x20 military_service, +0x28 slavery,
 * +0x30 purge, +0x38 population_control, +0x40 colonization_control, +0x48
 * migration_control, +0x50 subspecies_integration, +0x58.. former values,
 * +0xa0 species handle, +0xa4.. last-changed dates. Each category points
 * into its own database (reader 0x398ea0; +0x50 values, +0x5c count, key at
 * +0x20). The country's rights module is [country+0x2ab0]; lookup 0x7f5b10
 * returns module+0x20 for the primary species (not called here: it can fall
 * through to a creating path).
 */
'use strict';

const qsCreateSpeciesRight = new NativeFunction(base.add(0x6d29d0), 'pointer', [], 'win64');
const qsValidateSpeciesRight = new NativeFunction(base.add(0x6c7970), 'uint8', ['pointer', 'pointer'], 'win64');
const qsSpeciesRightVtable = 0x2393770;
const qsRightsBlockVtable = 0x2337510;
const qsRightCategories = {
    citizenship: [0x10, 0x3112870], living_standard: [0x18, 0x3112860], military_service: [0x20, 0x3112850],
    slavery: [0x28, 0x3112838], purge: [0x30, 0x3112840], population_control: [0x38, 0x3112848],
    colonization_control: [0x40, 0x3112868], migration_control: [0x48, 0x3112858],
};

function canonicalSpeciesRightRequest(request) {
    if (request === null || typeof request !== 'object' || Array.isArray(request) ||
        request.kind !== 'set_species_right') return null;
    const key = /^[a-z][a-z0-9_]{0,120}$/;
    if (Object.keys(request).sort().join(',') !== 'category,country_id,current_right,kind,right,species_id,token' ||
        request.country_id !== '0' || typeof request.token !== 'string' || !/^[0-9a-f]{32}$/.test(request.token) ||
        !validId(request.species_id) || !Object.prototype.hasOwnProperty.call(qsRightCategories, request.category) ||
        typeof request.right !== 'string' || !key.test(request.right) ||
        typeof request.current_right !== 'string' || !key.test(request.current_right) ||
        !request.right.startsWith(request.category + '_') || !request.current_right.startsWith(request.category + '_') ||
        request.right === request.current_right) {
        throw new Error('Malformed typed species rights request');
    }
    return {kind: request.kind, country_id: '0', species_id: request.species_id, category: request.category,
        right: request.right, current_right: request.current_right, token: request.token};
}

function qsRightObject(databaseRva, key) {
    const database = base.add(databaseRva).readPointer();
    if (database.isNull()) throw new NativePreconditionError('The species rights database is unavailable');
    const values = database.add(0x50).readPointer();
    const count = database.add(0x5c).readU32();
    if (values.isNull() || count < 1 || count > 256) throw new Error('Unrecognized species rights DB layout');
    let found = null;
    for (let index = 0; index < count; index++) {
        const value = values.add(index * 8).readPointer();
        if (value.isNull() || keyAt(value, 0x20, 0x30, 0x38) !== key) continue;
        if (found !== null) throw new Error('Duplicate species right identity');
        found = value;
    }
    if (found === null) throw new NativePreconditionError('The right is not in the game database');
    return found;
}

function qsSpeciesObject(id) {
    const pool = base.add(0x3114c40).readPointer();
    if (pool.isNull()) throw new NativePreconditionError('The species pool is unavailable');
    const entries = pool.add(0x18).readPointer();
    const count = pool.add(0x20).readU32();
    if (entries.isNull() || count < 1 || count > 0x100000) throw new NativePreconditionError('Unrecognized species pool layout');
    const index = id % 0x1000000;
    if (index >= count) throw new NativePreconditionError('The species is not in the pool');
    const species = entries.add(index * 0x10 + 8).readPointer();
    if (species.isNull() || species.add(0x10).readU32() !== id) {
        throw new NativePreconditionError('The species handle does not match its id');
    }
    return species;
}

function prepareSpeciesRightCommand(input) {
    const request = canonicalSpeciesRightRequest(input);
    if (request === null) throw new Error('Unknown species rights command kind');
    const country = qsPlayerCountry();
    if (country === null) throw new NativePreconditionError('The player country 0 is unavailable');
    const speciesId = Number(request.species_id);
    qsSpeciesObject(speciesId);
    const module = country.add(0x2ab0).readPointer();
    if (module.isNull()) throw new NativePreconditionError('The species rights module is unavailable');
    const primary = module.add(0x20);
    if (!primary.readPointer().equals(base.add(qsRightsBlockVtable)) || primary.add(0xa0).readU32() !== speciesId) {
        throw new NativePreconditionError('The species is not our primary species');
    }
    const [offset, databaseRva] = qsRightCategories[request.category];
    const current = primary.add(offset).readPointer();
    if (current.isNull() || keyAt(current, 0x20, 0x30, 0x38) !== request.current_right) {
        throw new NativePreconditionError('The live right no longer matches the observed one');
    }
    const target = qsRightObject(databaseRva, request.right);
    const command = qsCreateSpeciesRight();
    if (command.isNull()) throw new Error('Species rights command allocation failed');
    const vtable = command.readPointer();
    // An unrecognized command is never destroyed through a guessed vtable.
    if (!vtable.equals(base.add(qsSpeciesRightVtable)) || !vtable.readPointer().equals(base.add(0x6c7740)) ||
        !vtable.add(0x40).readPointer().equals(base.add(0x6c7970)) ||
        !vtable.add(0x48).readPointer().equals(base.add(0x6c7a70))) {
        throw new Error('Species rights command layout mismatch');
    }
    if (!command.add(0x28).readPointer().equals(base.add(qsRightsBlockVtable)) || command.add(0xf0).readU8() !== 1 ||
        command.add(0x20).readU32() !== 0xffffffff) {
        try { destroyUnsubmitted(command); } catch (_) { /* no second cleanup attempt */ }
        throw new Error('Species rights command defaults mismatch');
    }
    command.add(0x20).writeU32(0);
    command.add(0x24).writeU32(speciesId);
    // Observed rights without the block's vtable; then exactly one category.
    Memory.copy(command.add(0x28 + 8), primary.add(8), 0xc8 - 8);
    command.add(0x28 + offset).writePointer(target);
    return {command, validation: qsValidateWithReason(qsValidateSpeciesRight, command),
        meta: {native_species_handle: speciesId, native_right_before: request.current_right}};
}
