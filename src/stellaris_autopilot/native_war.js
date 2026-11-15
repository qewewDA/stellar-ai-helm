/* Pinned 4.5.2 declare-war and peace-offer diplomatic actions. Appended
 * after the other sources for the 4.5.2 build only; the 4.5.3 profile never
 * includes it. Callers never supply JavaScript, addresses or action tokens.
 *
 * Static evidence (4.5.2): qsCreateDiploAction 0x9a9860(0x2d52) allocates
 * 0x90 bytes, calls 0x93f6f0, sets vtable 0x23b2438 (validate +0x58 0x9ad0d0,
 * sender/receiver +0xa8/+0xb0 still 0x940120/0x940140) and constructs a
 * CWarGoal at +0x50 with 0x82fca0 (vtable 0x239e0a0; type pointer +0x08,
 * war +0x10, actor +0x14, target +0x18; writer keys type/war/actor/target).
 * War-goal types live in [0x3112808] (+0x50 entries, +0x5c count, name at
 * +0x20/0x30/0x38). The action is wrapped in diplo_action_propose_command
 * (factory 0x9d8280) like the other diplomatic actions. action_declare_war
 * is auto_accepted.
 *
 * action_offer_peace 0x2d53 allocates 0x68 bytes (ctor 0x9add70, vtable
 * 0x23b25e0, custom setters +0xa8 0x9addf0 / +0xb0 0x9adf10). +0x60 is the
 * war handle (pool [0x3114d10], identity +0x20). +0x64 is the peace-term
 * enum: 4 is the unset sentinel (PICK_A_PEACE_OPTION); 1 runs the demand-deal
 * checks; 0/2/3 pass the type switch. Mapping: status_quo=0,
 * demand_surrender=1, surrender=2.
 */
'use strict';

const qsWarGoalDatabase = base.add(0x3112808);
const qsWarDatabase = base.add(0x3114d10);
const qsSetPeaceSender = new NativeFunction(base.add(0x9addf0), 'void', ['pointer', 'pointer'], 'win64');
const qsSetPeaceReceiver = new NativeFunction(base.add(0x9adf10), 'void', ['pointer', 'pointer'], 'win64');
const qsDeclareWarVtable = 0x23b2438;
const qsDeclareWarGoalVtable = 0x239e0a0;
const qsEmptyWarGoalList = 0x22fe610;
const qsPeaceVtable = 0x23b25e0;
const qsPeaceTerms = {status_quo: 0, demand_surrender: 1, surrender: 2};

function warGoalType(key) {
    if (typeof key !== 'string' || !/^wg_[A-Za-z0-9_]{1,120}$/.test(key)) {
        throw new Error('Malformed war goal key');
    }
    const database = qsWarGoalDatabase.readPointer();
    if (database.isNull()) return null;
    const values = database.add(0x50).readPointer();
    const count = database.add(0x5c).readU32();
    if (values.isNull() || count < 1 || count > 2048) throw new Error('Unrecognized war-goal DB layout');
    let found = null;
    for (let index = 0; index < count; index++) {
        const value = values.add(index * 8).readPointer();
        if (value.isNull() || keyAt(value, 0x20, 0x30, 0x38) !== key) continue;
        if (found !== null) throw new Error('Duplicate war-goal identity');
        found = value;
    }
    return found;
}

function qsWarObject(id) {
    const index = id % 0x1000000;
    const database = qsWarDatabase.readPointer();
    if (database.isNull()) throw new NativePreconditionError('The war database is unavailable');
    const entries = database.add(0x18).readPointer();
    const count = database.add(0x20).readU32();
    if (entries.isNull() || count < 1 || count > 0x100000) throw new NativePreconditionError('Unrecognized war database layout');
    if (index >= count) throw new NativePreconditionError('The war is not in the database');
    const war = entries.add(index * 0x10 + 8).readPointer();
    if (war.isNull()) throw new NativePreconditionError('The war no longer exists');
    const handle = war.add(0x20).readU32();
    if (handle !== id) throw new NativePreconditionError('The war handle does not match its id');
    return {war, handle};
}

function canonicalWarRequest(request) {
    if (request === null || typeof request !== 'object') return null;
    if (request.kind === 'declare_war') {
        if (Array.isArray(request) ||
            Object.keys(request).sort().join(',') !== 'country_id,kind,target_country_id,token,war_goal' ||
            request.country_id !== '0' || typeof request.token !== 'string' || !/^[0-9a-f]{32}$/.test(request.token) ||
            !validId(request.target_country_id) || request.target_country_id === '0' ||
            typeof request.war_goal !== 'string' || !/^wg_[A-Za-z0-9_]{1,120}$/.test(request.war_goal)) {
            throw new Error('Malformed typed war request');
        }
        return {kind: 'declare_war', country_id: '0', target_country_id: request.target_country_id,
            war_goal: request.war_goal, token: request.token};
    }
    if (request.kind === 'offer_peace') {
        if (Array.isArray(request) ||
            Object.keys(request).sort().join(',') !== 'country_id,kind,target_country_id,terms,token,war_id' ||
            request.country_id !== '0' || typeof request.token !== 'string' || !/^[0-9a-f]{32}$/.test(request.token) ||
            !validId(request.target_country_id) || request.target_country_id === '0' || !validId(request.war_id) ||
            !Object.prototype.hasOwnProperty.call(qsPeaceTerms, request.terms)) {
            throw new Error('Malformed typed war request');
        }
        return {kind: 'offer_peace', country_id: '0', target_country_id: request.target_country_id,
            war_id: request.war_id, terms: request.terms, token: request.token};
    }
    return null;
}

function qsWrapDiploAction(action, sender, receiver, token) {
    let command;
    try {
        command = qsCreateDiploPropose();
    } catch (error) {
        try { destroyUnsubmitted(action); } catch (_) { /* no second cleanup attempt */ }
        throw error;
    }
    if (command.isNull() || !command.readPointer().equals(base.add(qsDiploProposeVtable)) ||
        !command.add(0x20).readPointer().isNull()) {
        try { destroyUnsubmitted(action); } catch (_) { /* no second cleanup attempt */ }
        throw new Error('Diplomatic proposal command layout mismatch');
    }
    command.add(0x20).writePointer(action);
    return {command, validation: qsValidateWithReason(qsValidateDiploPropose, command),
        meta: {native_sender_handle: sender.handle, native_receiver_handle: receiver.handle,
            native_action_token: token}};
}

function qsDeclareWarCommand(request) {
    const game = qsEventGame.readPointer();
    if (game.isNull()) throw new NativePreconditionError('The game state is unavailable');
    const player = qsLocalPlayer(game);
    if (player.isNull() || player.add(0x5c).readU32() !== 0) {
        throw new NativePreconditionError('The local human does not play country 0');
    }
    const goalType = warGoalType(request.war_goal);
    if (goalType === null) throw new NativePreconditionError('The war goal is not in the database');
    const sender = qsCountryObject(0);
    const receiver = qsCountryObject(Number(request.target_country_id));
    if (sender.handle === receiver.handle) throw new NativePreconditionError('A country cannot target itself');
    const action = qsCreateDiploAction(0x2d52);
    if (action.isNull()) throw new Error('Diplomatic action allocation failed');
    const vtable = action.readPointer();
    if (!vtable.equals(base.add(qsDeclareWarVtable)) || !vtable.add(0xa8).readPointer().equals(base.add(0x940120)) ||
        !vtable.add(0xb0).readPointer().equals(base.add(0x940140))) {
        throw new Error('Declare-war action layout mismatch'); // Never destroyed through a guessed vtable.
    }
    if (action.add(0x40).readPointer().isNull() ||
        !action.add(0x50).readPointer().equals(base.add(qsDeclareWarGoalVtable)) ||
        !action.add(0x70).readPointer().equals(base.add(qsEmptyWarGoalList)) ||
        !action.add(0x78).readPointer().isNull() || action.add(0x80).readU64().toNumber() !== 0 ||
        action.add(0x88).readU32() !== 0xffffffff) {
        try { destroyUnsubmitted(action); } catch (_) { /* no second cleanup attempt */ }
        throw new Error('Declare-war action defaults mismatch');
    }
    qsSetDiploSender(action, sender.country);
    qsSetDiploReceiver(action, receiver.country);
    if (action.add(0x20).readU32() !== sender.handle || action.add(0x24).readU32() !== receiver.handle) {
        try { destroyUnsubmitted(action); } catch (_) { /* no second cleanup attempt */ }
        throw new Error('Diplomatic action fields mismatch');
    }
    action.add(0x58).writePointer(goalType);
    action.add(0x64).writeU32(sender.handle);
    action.add(0x68).writeU32(receiver.handle);
    const wrapped = qsWrapDiploAction(action, sender, receiver, 0x2d52);
    wrapped.meta.native_war_goal = request.war_goal;
    return wrapped;
}

function qsOfferPeaceCommand(request) {
    const game = qsEventGame.readPointer();
    if (game.isNull()) throw new NativePreconditionError('The game state is unavailable');
    const player = qsLocalPlayer(game);
    if (player.isNull() || player.add(0x5c).readU32() !== 0) {
        throw new NativePreconditionError('The local human does not play country 0');
    }
    const sender = qsCountryObject(0);
    const receiver = qsCountryObject(Number(request.target_country_id));
    if (sender.handle === receiver.handle) throw new NativePreconditionError('A country cannot target itself');
    const war = qsWarObject(Number(request.war_id));
    const action = qsCreateDiploAction(0x2d53);
    if (action.isNull()) throw new Error('Diplomatic action allocation failed');
    const vtable = action.readPointer();
    if (!vtable.equals(base.add(qsPeaceVtable)) || !vtable.add(0xa8).readPointer().equals(base.add(0x9addf0)) ||
        !vtable.add(0xb0).readPointer().equals(base.add(0x9adf10))) {
        throw new Error('Peace-offer action layout mismatch'); // Never destroyed through a guessed vtable.
    }
    if (action.add(0x40).readPointer().isNull() ||
        action.add(0x60).readU32() !== 0xffffffff || action.add(0x64).readU32() !== 4) {
        try { destroyUnsubmitted(action); } catch (_) { /* no second cleanup attempt */ }
        throw new Error('Peace-offer action defaults mismatch');
    }
    qsSetPeaceSender(action, sender.country);
    qsSetPeaceReceiver(action, receiver.country);
    if (action.add(0x20).readU32() !== sender.handle || action.add(0x24).readU32() !== receiver.handle) {
        try { destroyUnsubmitted(action); } catch (_) { /* no second cleanup attempt */ }
        throw new Error('Diplomatic action fields mismatch');
    }
    action.add(0x60).writeU32(war.handle);
    action.add(0x64).writeU32(qsPeaceTerms[request.terms]);
    const wrapped = qsWrapDiploAction(action, sender, receiver, 0x2d53);
    wrapped.meta.native_war_handle = war.handle;
    wrapped.meta.native_peace_terms = request.terms;
    return wrapped;
}

function prepareWarCommand(request) {
    const canonical = canonicalWarRequest(request);
    if (canonical === null) throw new Error('Unknown war command kind');
    if (canonical.kind === 'declare_war') return qsDeclareWarCommand(canonical);
    return qsOfferPeaceCommand(canonical);
}
