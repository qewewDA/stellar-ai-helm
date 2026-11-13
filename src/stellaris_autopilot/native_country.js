/* Pinned 4.5.2 empire policy and edict commands. Appended after the other
 * sources for the 4.5.2 build only; the 4.5.3 profile never includes it.
 * Callers never supply JavaScript or addresses. The factories, layouts and
 * validators follow the normal policy confirm dialog (0x106f550) and edict
 * toggle (0x15744f0). Submission must be verified by country_commands.py.
 */
'use strict';

const qsCountryKinds = new Set(['set_policy', 'activate_edict', 'deactivate_edict']);
const qsCreatePolicy = new NativeFunction(base.add(0x6d2330), 'pointer', [], 'win64');
const qsValidatePolicy = new NativeFunction(base.add(0x6c0eb0), 'uint8', ['pointer', 'pointer'], 'win64');
const qsCreateEdict = new NativeFunction(base.add(0x6d21f0), 'pointer', [], 'win64');
const qsValidateEdict = new NativeFunction(base.add(0x6c0330), 'uint8', ['pointer', 'pointer'], 'win64');
const qsCreateRemoveEdict = new NativeFunction(base.add(0x6d2260), 'pointer', [], 'win64');
const qsValidateRemoveEdict = new NativeFunction(base.add(0x6c0890), 'uint8', ['pointer', 'pointer'], 'win64');
const qsCountryKey = /^[a-z][a-z0-9_]{0,119}$/;

function canonicalCountryRequest(request) {
    if (request === null || typeof request !== 'object' || !qsCountryKinds.has(request.kind)) return null;
    const policy = request.kind === 'set_policy';
    const expected = policy ? 'country_id,current_option,kind,option,policy,token' : 'country_id,edict,kind,token';
    const keys = policy ? [request.policy, request.option, request.current_option] : [request.edict];
    if (Array.isArray(request) || Object.keys(request).sort().join(',') !== expected || request.country_id !== '0' ||
        typeof request.token !== 'string' || !/^[0-9a-f]{32}$/.test(request.token) ||
        !keys.every(key => typeof key === 'string' && qsCountryKey.test(key)) ||
        (policy && request.option === request.current_option)) {
        throw new Error('Malformed typed country request');
    }
    return policy
        ? {kind: request.kind, country_id: '0', policy: request.policy, option: request.option,
            current_option: request.current_option, token: request.token}
        : {kind: request.kind, country_id: '0', edict: request.edict, token: request.token};
}

function qsPlayerCountry() {
    const pool = base.add(0x3114c38).readPointer();
    if (pool.isNull()) return null;
    const count = pool.add(0x20).readU32();
    const entries = pool.add(0x18).readPointer();
    if (entries.isNull() || count < 1 || count > 1000000) throw new Error('Unrecognized country pool');
    const country = entries.add(8).readPointer();
    return country.isNull() || country.add(0x20).readU32() !== 0 ? null : country;
}

function qsDatabaseObject(globalRva, key, limit, label) {
    const database = base.add(globalRva).readPointer();
    if (database.isNull()) return null;
    const values = database.add(0x50).readPointer();
    const count = database.add(0x5c).readU32();
    if (values.isNull() || count < 1 || count > limit) throw new Error('Unrecognized ' + label + ' DB layout');
    let found = null;
    for (let index = 0; index < count; index++) {
        const value = values.add(index * 8).readPointer();
        if (value.isNull() || keyAt(value, 0x20, 0x30, 0x38) !== key) continue;
        if (found !== null) throw new Error('Duplicate ' + label + ' identity');
        found = value;
    }
    return found;
}

function qsPolicyOption(group, key) {
    const options = group.add(0x2e8).readPointer();
    const count = group.add(0x2f4).readS32();
    if (options.isNull() || count < 1 || count > 256) throw new Error('Unrecognized policy option layout');
    let found = null;
    for (let index = 0; index < count; index++) {
        const option = options.add(index * 8).readPointer();
        if (option.isNull() || keyAt(option, 0x20, 0x30, 0x38) !== key) continue;
        if (found !== null) throw new Error('Duplicate policy option identity');
        found = option;
    }
    return found;
}

function qsCountryEntries(country, arrayOffset, countOffset, pointerOffset, target) {
    const entries = country.add(arrayOffset).readPointer();
    const count = country.add(countOffset).readS32();
    if (count < 0 || count > 4096 || (count > 0 && entries.isNull())) throw new Error('Unrecognized country entry array');
    const matches = [];
    for (let index = 0; index < count; index++) {
        const entry = entries.add(index * 0x20);
        if (entry.add(pointerOffset).readPointer().equals(target)) matches.push(entry);
    }
    return matches;
}

function qsPolicyCommand(request, country) {
    const group = qsDatabaseObject(0x3112400, request.policy, 4096, 'policy');
    const option = group === null ? null : qsPolicyOption(group, request.option);
    if (group === null || option === null) {
        throw new NativePreconditionError('The option is not an exact option of this policy');
    }
    // Active policies: +0x1bb8 entries (stride 0x20; +8 group, +0x10 option, +0x18 date).
    const active = qsCountryEntries(country, 0x1bb8, 0x1bc4, 8, group);
    if (active.length !== 1) throw new NativePreconditionError('The policy is not exactly one active empire policy');
    const current = active[0].add(0x10).readPointer();
    if (current.isNull() || current.equals(option) || keyAt(current, 0x20, 0x30, 0x38) !== request.current_option) {
        throw new NativePreconditionError('The live policy selection no longer matches the observed one');
    }
    const command = qsCreatePolicy();
    if (command.isNull()) throw new Error('Policy command allocation failed');
    // Same fields as the confirm dialog: group, option, country handle 0.
    command.add(0x20).writePointer(group);
    command.add(0x28).writePointer(option);
    command.add(0x30).writeU32(0);
    return {command, validate: qsValidatePolicy, meta: {native_policy_option_before: request.current_option}};
}

function qsEdictCommand(request, country) {
    const edict = qsDatabaseObject(0x3112558, request.edict, 4096, 'edict');
    if (edict === null) throw new NativePreconditionError('No exact edict definition matches');
    // Active edicts: +0x27f8 entries (stride 0x20; +0xc start date, +0x10 edict).
    const active = qsCountryEntries(country, 0x27f8, 0x2804, 0x10, edict);
    const activate = request.kind === 'activate_edict';
    if (activate ? active.length !== 0 : active.length !== 1) {
        throw new NativePreconditionError(activate ? 'The edict is already active' : 'The edict is not exactly one active edict');
    }
    const command = activate ? qsCreateEdict() : qsCreateRemoveEdict();
    if (command.isNull()) throw new Error('Edict command allocation failed');
    try {
        // The command owns a CString at +0x20 (buffer +0x30, length +0x40,
        // capacity +0x48), exactly as the edict toggle fills it.
        assignString(command.add(0x20), Memory.allocUtf8String(request.edict), request.edict.length);
        if (keyAt(command.add(0x20), 0x10, 0x20, 0x28) !== request.edict) throw new Error('Edict name assignment mismatch');
        command.add(0x50).writeU32(0);
    } catch (error) {
        try { destroyUnsubmitted(command); } catch (_) { /* one cleanup attempt only */ }
        throw error;
    }
    return {command, validate: activate ? qsValidateEdict : qsValidateRemoveEdict, meta: {native_edict: request.edict}};
}

function prepareCountryCommand(request) {
    const canonical = canonicalCountryRequest(request);
    if (canonical === null) throw new Error('Unknown country command kind');
    const country = qsPlayerCountry();
    if (country === null) throw new NativePreconditionError('The player country 0 is unavailable');
    return request.kind === 'set_policy' ? qsPolicyCommand(request, country) : qsEdictCommand(request, country);
}
