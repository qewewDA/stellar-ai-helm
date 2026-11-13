/* Pinned 4.5.2 unilateral diplomatic actions (auto-accepted proposals). Appended
 * after the other sources for the 4.5.2 build only; the 4.5.3 profile never
 * includes it. Callers never supply JavaScript, addresses or action tokens.
 *
 * Static evidence (4.5.2): the console peace command (0xeb8490) builds a
 * diplomatic action with the per-type factory 0x9a9860(type token), sets the
 * sender (vtable +0xa8, 0x940120: action+0x20 = country+0x20 handle) and the
 * receiver (vtable +0xb0, 0x940140: action+0x24), wraps it in a
 * diplo_action_propose_command (registered factory 0x9d8280, vtable
 * 0x23b4138, size 0x28, +0x20 owns the action) and dispatches it with
 * 0x5f88a0. The command validator 0x9d5e90 forwards the reason buffer to the
 * action's own check (vtable +0x58, 0x942130); execute 0x9d5d80 resolves the
 * sender and calls the country's proposal routine 0x6fca10, which validates
 * again. Countries live in the database at [0x3114c38]: +0x18 entries
 * (stride 0x10, object at +8), +0x20 count, object+0x20 full handle.
 * Action tokens/vtables: action_close_borders 0x3695 / 0x23aebc8,
 * action_open_borders 0x3696 / 0x23aed70, action_insult 0x3385 / 0x23ae878,
 * action_make_rival 0x3384 / 0x23ae6d0, action_end_rivalry 0x3524 / 0x23aea20,
 * action_recall_embassy 0x3525 / 0x23aef18; all 0x50 bytes with type data
 * at +0x40 and the same base vtable slots (+0x58 0x942130, +0xa8/+0xb0
 * setters). All are auto_accepted in 00_actions.txt; action_embassy is not
 * (the recipient decides) and is deliberately absent. Save country ids are
 * full handles (generation << 24 | index). The game's validator stays authoritative and every result is
 * verified from a fresh dump by diplomacy_commands.py.
 */
'use strict';

const qsCountryDatabase = base.add(0x3114c38);
const qsCreateDiploAction = new NativeFunction(base.add(0x9a9860), 'pointer', ['int32'], 'win64');
const qsSetDiploSender = new NativeFunction(base.add(0x940120), 'void', ['pointer', 'pointer'], 'win64');
const qsSetDiploReceiver = new NativeFunction(base.add(0x940140), 'void', ['pointer', 'pointer'], 'win64');
const qsCreateDiploPropose = new NativeFunction(base.add(0x9d8280), 'pointer', [], 'win64');
const qsValidateDiploPropose = new NativeFunction(base.add(0x9d5e90), 'uint8', ['pointer', 'pointer'], 'win64');
const qsDiploProposeVtable = 0x23b4138;
const qsDiploActions = {
    close_borders: {token: 0x3695, vtable: 0x23aebc8},
    open_borders: {token: 0x3696, vtable: 0x23aed70},
    insult: {token: 0x3385, vtable: 0x23ae878},
    make_rival: {token: 0x3384, vtable: 0x23ae6d0},
    end_rivalry: {token: 0x3524, vtable: 0x23aea20},
    recall_embassy: {token: 0x3525, vtable: 0x23aef18},
};

function canonicalDiplomacyRequest(request) {
    if (request === null || typeof request !== 'object' ||
        !Object.prototype.hasOwnProperty.call(qsDiploActions, request.kind)) return null;
    if (Array.isArray(request) || Object.keys(request).sort().join(',') !== 'country_id,kind,target_country_id,token' ||
        request.country_id !== '0' || typeof request.token !== 'string' || !/^[0-9a-f]{32}$/.test(request.token) ||
        !validId(request.target_country_id) || request.target_country_id === '0') {
        throw new Error('Malformed typed diplomacy request');
    }
    return {kind: request.kind, country_id: '0', target_country_id: request.target_country_id, token: request.token};
}

function qsCountryObject(id) {
    const index = id % 0x1000000;
    const database = qsCountryDatabase.readPointer();
    if (database.isNull()) throw new NativePreconditionError('The country database is unavailable');
    const entries = database.add(0x18).readPointer();
    const count = database.add(0x20).readU32();
    if (entries.isNull() || count < 1 || count > 0x100000) throw new NativePreconditionError('Unrecognized country database layout');
    if (index >= count) throw new NativePreconditionError('The country is not in the database');
    const country = entries.add(index * 0x10 + 8).readPointer();
    if (country.isNull()) throw new NativePreconditionError('The country no longer exists');
    const handle = country.add(0x20).readU32();
    if (handle !== id) throw new NativePreconditionError('The country handle does not match its id');
    return {country, handle};
}

function qsDiplomacyCommand(request) {
    const spec = qsDiploActions[request.kind];
    const game = qsEventGame.readPointer();
    if (game.isNull()) throw new NativePreconditionError('The game state is unavailable');
    const player = qsLocalPlayer(game);
    if (player.isNull() || player.add(0x5c).readU32() !== 0) {
        throw new NativePreconditionError('The local human does not play country 0');
    }
    const sender = qsCountryObject(0);
    const receiver = qsCountryObject(Number(request.target_country_id));
    if (sender.handle === receiver.handle) throw new NativePreconditionError('A country cannot target itself');
    const action = qsCreateDiploAction(spec.token);
    if (action.isNull()) throw new Error('Diplomatic action allocation failed');
    const vtable = action.readPointer();
    if (!vtable.equals(base.add(spec.vtable)) || !vtable.add(0xa8).readPointer().equals(base.add(0x940120)) ||
        !vtable.add(0xb0).readPointer().equals(base.add(0x940140)) || action.add(0x40).readPointer().isNull()) {
        try { destroyUnsubmitted(action); } catch (_) { /* no second cleanup attempt */ }
        throw new Error('Diplomatic action layout mismatch');
    }
    qsSetDiploSender(action, sender.country);
    qsSetDiploReceiver(action, receiver.country);
    if (action.add(0x20).readU32() !== sender.handle || action.add(0x24).readU32() !== receiver.handle) {
        try { destroyUnsubmitted(action); } catch (_) { /* no second cleanup attempt */ }
        throw new Error('Diplomatic action fields mismatch');
    }
    let command;
    try {
        command = qsCreateDiploPropose();
    } catch (error) {
        try { destroyUnsubmitted(action); } catch (_) { /* no second cleanup attempt */ }
        throw error;
    }
    if (command.isNull() || !command.readPointer().equals(base.add(qsDiploProposeVtable)) ||
        !command.add(0x20).readPointer().isNull()) {
        // An unrecognized command is never destroyed through a guessed vtable.
        try { destroyUnsubmitted(action); } catch (_) { /* no second cleanup attempt */ }
        throw new Error('Diplomatic proposal command layout mismatch');
    }
    command.add(0x20).writePointer(action); // The proposal command now owns the action.
    return {command, validation: qsValidateWithReason(qsValidateDiploPropose, command),
        meta: {native_sender_handle: sender.handle, native_receiver_handle: receiver.handle,
            native_action_token: spec.token}};
}

function prepareDiplomacyCommand(request) {
    const canonical = canonicalDiplomacyRequest(request);
    if (canonical === null) throw new Error('Unknown diplomacy command kind');
    return qsDiplomacyCommand(canonical);
}
