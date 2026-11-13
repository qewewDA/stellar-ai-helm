/* Pinned 4.5.2 fleet stance, construction queue priority and defense
 * platform commands. Appended after the other sources for the 4.5.2 build
 * only; the 4.5.3 profile never includes it. Callers never supply JavaScript
 * or addresses. Layouts follow the normal UI paths: the fleet stance button
 * (0x10286a0), the queue item priority buttons (0x1562570/0x1562690) and the
 * starbase defense platform build entry (0x15dbe60). The game's validators
 * stay authoritative and every result is verified by control_commands.py.
 */
'use strict';

const qsControlKinds = new Set(['set_fleet_stance', 'move_queue_item', 'queue_defense_platform']);
// switch_fleet_stance_command: size 0x28, +0x20 fleet handle, +0x24 stance.
const qsCreateFleetStance = new NativeFunction(base.add(0xa70c40), 'pointer', [], 'win64');
const qsValidateFleetStance = new NativeFunction(base.add(0xa61f40), 'uint8', ['pointer', 'pointer'], 'win64');
// increase/decrease_once_buildable_priority: +0x20 country, +0x24 item.
const qsCreateRaisePriority = new NativeFunction(base.add(0xacd870), 'pointer', [], 'win64');
const qsValidateRaisePriority = new NativeFunction(base.add(0xacc290), 'uint8', ['pointer', 'pointer'], 'win64');
const qsCreateLowerPriority = new NativeFunction(base.add(0xacd910), 'pointer', [], 'win64');
const qsValidateLowerPriority = new NativeFunction(base.add(0xacc700), 'uint8', ['pointer', 'pointer'], 'win64');
// buildable_defense_platform: size 0x548, design implementation at +8,
// +0x538 design, +0x534 growth stage, +0x540 starbase handle.
const qsCreateDefensePlatform = new NativeFunction(base.add(0x8cb340), 'pointer', [], 'win64');
const qsSelectPlatformDesign = new NativeFunction(base.add(0xcd32b0), 'void', ['pointer', 'uint32'], 'win64');
const qsStances = {passive: 0, aggressive: 1, evasive: 2};
// Validator reason text (the UI tooltip path): a CString with +0 allocator
// mode (0 = default heap), +8 allocator, +0x10 std::string (SSO buffer or
// heap pointer, +0x20 size, +0x28 capacity). Validators append with
// 0x1b2ef50; a heap buffer is released with the game's free (0x1b69180),
// exactly like the validator's own temporaries.
const qsFreeReasonText = new NativeFunction(base.add(0x1b69180), 'void', ['pointer'], 'win64');
const qsReasonLimit = 4096;

function qsReasonFromBuffer(reason) {
    const size = Number(reason.add(0x20).readU64());
    const capacity = Number(reason.add(0x28).readU64());
    const heap = capacity >= 0x10;
    const textPointer = heap ? reason.add(0x10).readPointer() : reason.add(0x10);
    let text = '';
    try {
        if (size > 0 && !textPointer.isNull()) {
            try { text = textPointer.readUtf8String(Math.min(size, qsReasonLimit)); }
            catch (_) { text = textPointer.readCString(Math.min(size, qsReasonLimit)); }
        }
    } finally {
        if (heap && reason.add(0).readU32() !== 1 && !textPointer.isNull()) qsFreeReasonText(textPointer);
        reason.add(0x10).writeByteArray(new Array(0x18).fill(0));
        reason.add(0x28).writeU64(15);
    }
    return qsCleanReasonText(text || '');
}

function qsCleanReasonText(text) {
    // Strip colour/format codes (section sign + code char, and the bare
    // bracket-less end marker) and collapse whitespace; the result is data.
    return String(text).replace(/\u00a7[\s\S]?/g, '').replace(/\u00a3[^\u00a3\s]*\u00a3?/g, '')
        .replace(/[\u0000-\u0008\u000b-\u001f]/g, '').replace(/[ \t]+/g, ' ')
        .split('\n').map(line => line.trim()).filter(Boolean).join('\n').slice(0, qsReasonLimit);
}

function qsValidateWithReason(validate, command) {
    const reason = Memory.alloc(0x40);
    reason.writeByteArray(new Array(0x40).fill(0));
    reason.add(0x28).writeU64(15);
    const allowed = validate(command, reason) !== 0;
    const text = qsReasonFromBuffer(reason);
    return {allowed, reason: allowed ? '' : 'Normal native command validation rejected' + (text ? ': ' + text : ''),
        native_reason: text};
}

function canonicalControlRequest(request) {
    if (request === null || typeof request !== 'object' || !qsControlKinds.has(request.kind)) return null;
    const layouts = {
        set_fleet_stance: ['country_id,current_stance,fleet_id,kind,stance,token', ['fleet_id']],
        move_queue_item: ['country_id,direction,item_id,kind,neighbor_id,queue_id,token', ['item_id', 'neighbor_id', 'queue_id']],
        queue_defense_platform: ['country_id,design_id,kind,queue_id,starbase_id,station_id,token',
            ['design_id', 'queue_id', 'starbase_id', 'station_id']],
    };
    const [fields, ids] = layouts[request.kind];
    if (Array.isArray(request) || Object.keys(request).sort().join(',') !== fields || request.country_id !== '0' ||
        typeof request.token !== 'string' || !/^[0-9a-f]{32}$/.test(request.token) ||
        !ids.every(key => validId(request[key]))) {
        throw new Error('Malformed typed control request');
    }
    if (request.kind === 'set_fleet_stance') {
        if (!Object.prototype.hasOwnProperty.call(qsStances, request.stance) ||
            !Object.prototype.hasOwnProperty.call(qsStances, request.current_stance) ||
            request.stance === request.current_stance) throw new Error('Malformed typed control request');
        return {kind: request.kind, country_id: '0', fleet_id: request.fleet_id, stance: request.stance,
            current_stance: request.current_stance, token: request.token};
    }
    if (request.kind === 'move_queue_item') {
        if (!['up', 'down'].includes(request.direction) || request.item_id === request.neighbor_id) {
            throw new Error('Malformed typed control request');
        }
        return {kind: request.kind, country_id: '0', item_id: request.item_id, queue_id: request.queue_id,
            direction: request.direction, neighbor_id: request.neighbor_id, token: request.token};
    }
    return {kind: request.kind, country_id: '0', starbase_id: request.starbase_id, design_id: request.design_id,
        station_id: request.station_id, queue_id: request.queue_id, token: request.token};
}

function qsControlPoolObject(globalRva, handle, identityOffset) {
    const pool = base.add(globalRva).readPointer();
    if (pool.isNull()) return null;
    const count = pool.add(0x20).readU32();
    const entries = pool.add(0x18).readPointer();
    const index = Number(handle) & 0xffffff;
    if (entries.isNull() || count > 1000000) throw new Error('Unrecognized native object pool');
    if (index >= count) return null;
    const object = entries.add(index * 16 + 8).readPointer();
    return !object.isNull() && object.add(identityOffset).readU32() === Number(handle) ? object : null;
}

function qsFleetStanceCommand(request) {
    // Fleet pool 0x31129d8 (identity +0x30; controller +0x448, owner +0x44c;
    // stance enum +0x460). The native validator does not check ownership.
    const fleet = qsControlPoolObject(0x31129d8, request.fleet_id, 0x30);
    if (fleet === null || fleet.add(0x44c).readU32() !== 0 || fleet.add(0x448).readU32() !== 0) {
        throw new NativePreconditionError('The fleet is not an exact fleet owned and controlled by country 0');
    }
    if (fleet.add(0x460).readU32() !== qsStances[request.current_stance]) {
        throw new NativePreconditionError('The live fleet stance no longer matches the observed one');
    }
    const command = qsCreateFleetStance();
    if (command.isNull()) throw new Error('Fleet stance command allocation failed');
    command.add(0x20).writeU32(Number(request.fleet_id));
    command.add(0x24).writeU32(qsStances[request.stance]);
    return {command, validate: qsValidateFleetStance, meta: {native_stance_before: request.current_stance}};
}

function qsQueuePriorityCommand(request) {
    // Item pool 0x3114b90 (identity +8, queue handle +0x20); queue pool
    // 0x3114ba0 (identity +8, item ids at +0x20 with count +0x2c).
    const item = qsControlPoolObject(0x3114b90, request.item_id, 8);
    const queue = item === null ? null : qsControlPoolObject(0x3114ba0, item.add(0x20).readU32(), 8);
    if (item === null || queue === null || item.add(0x20).readU32() !== Number(request.queue_id)) {
        throw new NativePreconditionError('The construction item is not in the observed queue');
    }
    const owner = queue.add(0x50).readU8() !== 0 ? queue.add(0x58).readU32() : queue.add(0x30).readU32();
    if (owner !== 0) throw new NativePreconditionError('The construction queue is not owned by country 0');
    const ids = queue.add(0x20).readPointer();
    const count = queue.add(0x2c).readS32();
    if (count < 2 || count > 4096 || ids.isNull()) throw new NativePreconditionError('The queue has no neighbour to swap with');
    const order = [];
    for (let index = 0; index < count; index++) order.push(ids.add(index * 4).readU32());
    const position = order.indexOf(Number(request.item_id));
    if (position < 0 || order.indexOf(Number(request.item_id), position + 1) >= 0) {
        throw new NativePreconditionError('The item is not exactly once in its live queue');
    }
    const neighbour = request.direction === 'up' ? position - 1 : position + 1;
    if (neighbour < 0 || neighbour >= count || order[neighbour] !== Number(request.neighbor_id)) {
        throw new NativePreconditionError('The live queue neighbour no longer matches the observed order');
    }
    const up = request.direction === 'up';
    const command = up ? qsCreateRaisePriority() : qsCreateLowerPriority();
    if (command.isNull()) throw new Error('Queue priority command allocation failed');
    command.add(0x20).writeU32(0);
    command.add(0x24).writeU32(Number(request.item_id));
    return {command, validate: up ? qsValidateRaisePriority : qsValidateLowerPriority,
        meta: {native_queue_position_before: position}};
}

function qsDefensePlatformCommand(request) {
    // Starbase pool 0x3114c60 (identity +8; +0x98 station, +0x880 build
    // queue, +0x884 shipyard queue); design pool 0x3114670 (identity +0x10).
    const design = qsControlPoolObject(0x3114670, request.design_id, 0x10);
    const starbase = qsControlPoolObject(0x3114c60, request.starbase_id, 8);
    if (design === null || starbase === null || shipyardOwner(request.starbase_id) !== 0 ||
        starbase.add(0x98).readU32() !== Number(request.station_id) ||
        starbase.add(0x880).readU32() !== Number(request.queue_id)) {
        throw new NativePreconditionError('Defense platform identity mismatch');
    }
    let command = null;
    let buildable = null;
    try {
        buildable = qsCreateDefensePlatform();
        if (buildable.isNull()) throw new Error('Defense platform buildable allocation failed');
        qsSelectPlatformDesign(buildable.add(8), Number(request.design_id));
        if (buildable.add(0x538).readU32() !== Number(request.design_id) ||
            buildable.add(0x534).readS32() !== 0) throw new Error('Defense platform design/stage identity mismatch');
        buildable.add(0x540).writeU32(Number(request.starbase_id));
        command = createQueue();
        if (command.isNull()) throw new Error('Defense platform queue command allocation failed');
        command.add(0x28).writeU32(0);
        command.add(0x2c).writeU32(Number(request.queue_id));
        const nested = buildable;
        buildable = null; // Transfer is attempted once; never destroy aliases.
        command.add(0x20).writePointer(nested);
        return {command, validation: qsValidateWithReason(validateQueue, command),
            meta: {native_design_id: Number(request.design_id)}};
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

function prepareControlCommand(request) {
    const canonical = canonicalControlRequest(request);
    if (canonical === null) throw new Error('Unknown control command kind');
    if (canonical.kind === 'set_fleet_stance') return qsFleetStanceCommand(canonical);
    if (canonical.kind === 'move_queue_item') return qsQueuePriorityCommand(canonical);
    return qsDefensePlatformCommand(canonical);
}
