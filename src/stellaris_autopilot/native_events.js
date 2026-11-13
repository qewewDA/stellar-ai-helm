/* Pinned 4.5.2 event option selection. Appended after the other sources for
 * the 4.5.2 build only; the 4.5.3 profile never includes it. Callers never
 * supply JavaScript or addresses.
 *
 * Static evidence (4.5.2): the event window option button (0x1589820) and
 * the open-event auto chooser (0x8e2270) both build a select_event_option
 * command (vtable 0x23abdd8, ctor 0x99bfb0, validate 0x99cd60, execute
 * 0x99c090, writer 0x99cbf0) with +0x20 event handle, +0x24 option index,
 * +0x28 open player event id, +0x2c human id and +0x30 a copy of the event
 * scope, then validate it and dispatch it with 0x5f88a0. Open player events
 * live in the game state ([0x3114700]) array at +0x180 (count +0x18c):
 * +8 event holder (event = [holder], event+8 handle, event+0x20 id string,
 * +0x5e8 options, +0x5f4 option count), +0x10 scope, +0x180 open event id,
 * +0x184 country handle. The local human id is +0x48 of 0x24c580(game).
 * The game's validator stays authoritative and every result is verified by
 * event_commands.py.
 */
'use strict';

const qsEventGame = base.add(0x3114700);
const qsLocalPlayer = new NativeFunction(base.add(0x24c580), 'pointer', ['pointer'], 'win64');
const qsAllocCommand = new NativeFunction(base.add(0x2021868), 'pointer', ['uint64'], 'win64');
const qsConstructEventOption = new NativeFunction(base.add(0x99bfb0), 'pointer',
    ['pointer', 'pointer', 'pointer', 'int32', 'int32', 'int32'], 'win64');
const qsValidateEventOption = new NativeFunction(base.add(0x99cd60), 'uint8', ['pointer', 'pointer'], 'win64');
const qsEventOptionVtable = 0x23abdd8;
const qsEventOptionSize = 0x1a0;
const qsMaxEventOptions = 64;

function canonicalEventRequest(request) {
    if (request === null || typeof request !== 'object' || request.kind !== 'choose_event_option') return null;
    if (Array.isArray(request) || Object.keys(request).sort().join(',') !==
            'country_id,event_id,kind,option_index,player_event_id,token' || request.country_id !== '0' ||
        typeof request.token !== 'string' || !/^[0-9a-f]{32}$/.test(request.token) ||
        !validId(request.player_event_id) || !validId(request.option_index) ||
        Number(request.option_index) >= qsMaxEventOptions || Number(request.player_event_id) > 0x7fffffff ||
        typeof request.event_id !== 'string' || !/^[A-Za-z0-9_.-]{1,96}$/.test(request.event_id)) {
        throw new Error('Malformed typed event request');
    }
    return {kind: request.kind, country_id: '0', player_event_id: request.player_event_id,
        event_id: request.event_id, option_index: request.option_index, token: request.token};
}

function qsStdString(address) {
    const size = Number(address.add(0x10).readU64());
    const capacity = Number(address.add(0x18).readU64());
    if (size > 256 || capacity < size) return null;
    const text = capacity >= 0x10 ? address.readPointer() : address;
    return size === 0 ? '' : text.readUtf8String(size);
}

function qsOpenPlayerEvent(id) {
    const game = qsEventGame.readPointer();
    if (game.isNull()) throw new NativePreconditionError('The game state is unavailable');
    const entries = game.add(0x180).readPointer();
    const count = game.add(0x18c).readS32();
    if (count < 0 || count > 4096 || (count > 0 && entries.isNull())) {
        throw new NativePreconditionError('The open player event list is unreadable');
    }
    let found = null;
    for (let index = 0; index < count; index++) {
        const entry = entries.add(index * 8).readPointer();
        if (!entry.isNull() && entry.add(0x180).readS32() === id) {
            if (found !== null) throw new NativePreconditionError('The open player event id is not unique');
            found = entry;
        }
    }
    return {game, entry: found};
}

function qsEventOptionCommand(request) {
    const {game, entry} = qsOpenPlayerEvent(Number(request.player_event_id));
    if (entry === null) throw new NativePreconditionError('The open player event is no longer pending');
    if (entry.add(0x184).readU32() !== 0) throw new NativePreconditionError('The open player event belongs to another country');
    const holder = entry.add(8).readPointer();
    const event = holder.isNull() ? ptr(0) : holder.readPointer();
    if (event.isNull()) throw new NativePreconditionError('The open player event has no event definition');
    if (qsStdString(event.add(0x20)) !== request.event_id) {
        throw new NativePreconditionError('The open player event is a different event');
    }
    const options = event.add(0x5f4).readS32();
    const option = Number(request.option_index);
    if (options < 1 || options > qsMaxEventOptions || option >= options) {
        throw new NativePreconditionError('The option index is outside the event options');
    }
    const player = qsLocalPlayer(game);
    if (player.isNull() || player.add(0x5c).readU32() !== 0) {
        throw new NativePreconditionError('The local human does not play country 0');
    }
    const human = player.add(0x48).readS32();
    const command = qsAllocCommand(qsEventOptionSize);
    if (command.isNull()) throw new Error('Event option command allocation failed');
    qsConstructEventOption(command, holder, entry.add(0x10), option, Number(request.player_event_id), human);
    if (!command.readPointer().equals(base.add(qsEventOptionVtable)) ||
        command.add(0x20).readS32() !== event.add(8).readS32() || command.add(0x24).readS32() !== option ||
        command.add(0x28).readS32() !== Number(request.player_event_id) || command.add(0x2c).readS32() !== human) {
        const abandoned = command;
        try { destroyUnsubmitted(abandoned); } catch (_) { /* no second cleanup attempt */ }
        throw new Error('Event option command layout mismatch');
    }
    return {command, validate: qsValidateEventOption,
        meta: {native_event_handle: event.add(8).readS32(), native_option_count: options, native_human: human}};
}

function prepareEventCommand(request) {
    const canonical = canonicalEventRequest(request);
    if (canonical === null) throw new Error('Unknown event command kind');
    return qsEventOptionCommand(canonical);
}
