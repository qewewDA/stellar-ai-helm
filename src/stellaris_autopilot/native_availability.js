/* Pinned 4.5.2 availability query. Appended after the other 4.5.2 sources
 * only; the 4.5.3 profile never includes it. Builds typed commands, runs the
 * game's own validator (potential/allow/cost), records the reason, and
 * always destroys the command. Never posts or dispatches.
 */
'use strict';

const qsAvailabilityLimit = 500;

function canonicalAvailabilityRequest(request) {
    if (request === null || typeof request !== 'object' || Array.isArray(request) ||
        request.kind !== 'query_availability') return null;
    const keys = Object.keys(request).sort().join(',');
    if (keys !== 'candidates,country_id,kind,token' || request.country_id !== '0' ||
        typeof request.token !== 'string' || !/^[0-9a-f]{32}$/.test(request.token) ||
        !Array.isArray(request.candidates) || request.candidates.length < 1 ||
        request.candidates.length > qsAvailabilityLimit) {
        throw new Error('Malformed availability query');
    }
    const candidates = [];
    for (const item of request.candidates) {
        if (item === null || typeof item !== 'object' || Array.isArray(item) ||
            typeof item.kind !== 'string') {
            throw new Error('Malformed availability candidate');
        }
        const payload = Object.assign({}, item, {country_id: '0', token: request.token});
        candidates.push(payload);
    }
    return {kind: 'query_availability', country_id: '0', token: request.token, candidates};
}

function qsPrepareAvailabilityCandidate(payload) {
    if (payload.kind === 'queue_ship') return prepareShipCommand(payload);
    if (payload.kind === 'queue_colony_ship') return prepareColonyShipCommand(payload);
    if (['set_policy', 'activate_edict', 'deactivate_edict'].includes(payload.kind)) return prepareCountryCommand(payload);
    if (['set_fleet_stance', 'move_queue_item', 'queue_defense_platform'].includes(payload.kind)) {
        return prepareControlCommand(payload);
    }
    if (payload.kind === 'choose_event_option') return prepareEventCommand(payload);
    if (['close_borders', 'open_borders', 'insult', 'make_rival', 'end_rivalry', 'recall_embassy',
        'harm_relations', 'improve_relations', 'propose_embassy', 'propose_non_aggression_pact',
        'propose_research_agreement', 'propose_commercial_pact', 'propose_migration_pact'].includes(payload.kind)) {
        return prepareDiplomacyCommand(payload);
    }
    if (payload.kind === 'enact_planet_decision') return prepareDecisionCommand(payload);
    if (payload.kind === 'set_species_right') return prepareSpeciesRightCommand(payload);
    if (payload.kind === 'queue_starbase_module' || payload.kind === 'queue_starbase_building') {
        return prepareStarbaseCommand(payload);
    }
    if (payload.kind === 'recruit_army') return prepareArmyCommand(payload);
    if (payload.kind === 'add_system_claim') return prepareClaimCommand(payload);
    if (payload.kind === 'declare_war' || payload.kind === 'offer_peace') return prepareWarCommand(payload);
    if (payload.kind === 'set_ship_design') return prepareShipDesignCommand(payload);
    if (['upgrade_building', 'demolish_building', 'convert_zone', 'cancel_construction'].includes(payload.kind)) {
        return preparePlanetCommand(payload);
    }
    if (payload.kind === 'queue_district' || payload.kind === 'queue_building' || payload.kind === 'select_research') {
        return qsPrepareBasicAvailability(payload);
    }
    throw new Error('Unknown availability candidate kind');
}

function qsPrepareBasicAvailability(request) {
    if (request.kind === 'select_research') {
        const target = technology(request.technology);
        if (target === null) throw new NativePreconditionError('Database object unavailable');
        const command = create();
        if (command.isNull()) throw new Error('Command allocation failed');
        command.add(0x20).writeU32(0);
        command.add(0x28).writePointer(target);
        return {command, validation: qsValidateWithReason(validate, command)};
    }
    const nativeColony = colonyForQueue(request.queue_id, request.colony_id, request.planet_id);
    if (nativeColony === null) throw new NativePreconditionError('No unique owned native colony matches the observed queue');
    const building = request.kind === 'queue_building';
    const target = building ? buildingType(request.building) : districtType(request.district);
    if (target === null) throw new NativePreconditionError('Database object unavailable');
    let orphan = building ? createBuilding() : createDistrict();
    if (orphan.isNull()) throw new Error('Buildable allocation failed');
    let command = null;
    try {
        orphan.add(8).writePointer(target);
        orphan.add(0x10).writeU32(nativeColony.handle);
        if (building) orphan.add(0x14).writeU32(Number(request.zone_id));
        command = createQueue();
        if (command.isNull()) throw new Error('Queue command allocation failed');
        command.add(0x20).writePointer(orphan);
        orphan = null;
        command.add(0x28).writeU32(0);
        command.add(0x2c).writeU32(Number(request.queue_id));
        return {command, validation: building ? validateBuilding(command) : qsValidateWithReason(validateQueue, command),
            meta: {native_colony_handle: nativeColony.handle}};
    } catch (error) {
        if (command !== null && !command.isNull()) {
            try { destroyUnsubmitted(command); } catch (_) { /* no second cleanup */ }
            command = null;
        }
        if (orphan !== null && !orphan.isNull()) {
            try { destroyUnsubmitted(orphan); } catch (_) { /* no second cleanup */ }
        }
        throw error;
    }
}

function qsAvailabilityIdentity(payload) {
    const identity = {kind: payload.kind};
    for (const key of ['planet_id', 'decision_id', 'policy', 'option', 'edict', 'species_id', 'right',
        'category', 'starbase_id', 'slot', 'module_id', 'building_id', 'type_id', 'building',
        'district', 'district_id', 'slot_index', 'source_zone', 'target_zone', 'zone_id',
        'queue_id', 'colony_id', 'target_country_id', 'war_goal',
        'area', 'technology']) {
        if (Object.prototype.hasOwnProperty.call(payload, key)) identity[key] = payload[key];
    }
    return identity;
}

function queryAvailability(request) {
    const results = [];
    for (const item of request.candidates) {
        const payload = Object.assign({}, item, {country_id: '0', token: request.token});
        let command = null;
        const identity = qsAvailabilityIdentity(payload);
        try {
            const prepared = qsPrepareAvailabilityCandidate(payload);
            command = prepared && prepared.command ? prepared.command : null;
            let validation = prepared && prepared.validation;
            if (!validation && prepared && typeof prepared.validate === 'function' && command && !command.isNull()) {
                validation = qsValidateWithReason(prepared.validate, command);
            }
            if (command !== null && !command.isNull()) {
                const rejected = command;
                command = null;
                destroyUnsubmitted(rejected);
            }
            const allowed = !!(validation && validation.allowed);
            const nativeReason = validation && typeof validation.native_reason === 'string' ? validation.native_reason : '';
            const reason = allowed ? '' : (nativeReason || (validation && validation.reason) || '');
            results.push(Object.assign({}, identity, {
                allowed, enactable: allowed, known: true,
                reason: allowed ? null : (reason || null),
            }));
        } catch (error) {
            if (command !== null && !command.isNull()) {
                try { destroyUnsubmitted(command); } catch (_) { /* no second cleanup attempt */ }
                command = null;
            }
            const precondition = error instanceof NativePreconditionError;
            results.push(Object.assign({}, identity, {
                allowed: false, enactable: false,
                known: precondition,
                reason: precondition ? String(error.message || '') : null,
                error: precondition ? 'precondition' : 'ambiguous',
            }));
        }
    }
    return results;
}
