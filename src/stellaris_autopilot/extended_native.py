"""Typed extension registry for normal planetary, fleet and shipyard commands."""
from __future__ import annotations

import re

from .ship_commands import ShipRequest, ShipConstruction, ship_request, resolve_ship, verify_ship, _id
from .planet_commands import (PlanetRequest, ResolvedPlanetCommand, resolve_planet_command,
                              verify_planet_command, native_planet_payload)
from .fleet_native import (FleetRequest, FleetOperation, fleet_request, resolve_fleet, verify_fleet)
from .colony_ships import (ColonyShipRequest, ColonyShipConstruction, colony_ship_request,
                           resolve_colony_ship, verify_colony_ship, colony_ship_payload)
from .country_commands import (CountryRequest, ResolvedCountryCommand, resolve_country_command,
                               verify_country_command, native_country_payload)
from .control_commands import (ControlRequest, ResolvedControlCommand, resolve_control_command,
                               verify_control_command, native_control_payload)
from .event_commands import (EventRequest, ResolvedEventCommand, resolve_event_command,
                             verify_event_command, native_event_payload)
from .diplomacy_commands import (DiplomacyRequest, ResolvedDiplomacyCommand, resolve_diplomacy_command,
                                 verify_diplomacy_command, native_diplomacy_payload)
from .decision_commands import (DecisionRequest, ResolvedDecisionCommand, resolve_decision_command,
                                verify_decision_command, native_decision_payload)


REQUEST_TYPES = (ShipRequest, PlanetRequest, FleetRequest, ColonyShipRequest, CountryRequest, ControlRequest,
                 EventRequest, DiplomacyRequest, DecisionRequest)
RESOLVED_TYPES = (ShipConstruction, ResolvedPlanetCommand, FleetOperation, ColonyShipConstruction,
                  ResolvedCountryCommand, ResolvedControlCommand, ResolvedEventCommand, ResolvedDiplomacyCommand,
                  ResolvedDecisionCommand)


def resolve_extended(state, request):
    if isinstance(request, ColonyShipRequest):
        return resolve_colony_ship(state, request)
    if isinstance(request, ShipRequest):
        return resolve_ship(state, request)
    if isinstance(request, PlanetRequest):
        return resolve_planet_command(state, request)
    if isinstance(request, FleetRequest):
        return resolve_fleet(state, request)
    if isinstance(request, CountryRequest):
        return resolve_country_command(state, request)
    if isinstance(request, ControlRequest):
        return resolve_control_command(state, request)
    if isinstance(request, EventRequest):
        return resolve_event_command(state, request)
    if isinstance(request, DiplomacyRequest):
        return resolve_diplomacy_command(state, request)
    if isinstance(request, DecisionRequest):
        return resolve_decision_command(state, request)
    raise ValueError("Unsupported extended native request")


def verify_extended(before, after, command):
    if isinstance(command, ColonyShipConstruction):
        return verify_colony_ship(before, after, command)
    if isinstance(command, ShipConstruction):
        return verify_ship(before, after, command)
    if isinstance(command, ResolvedPlanetCommand):
        return verify_planet_command(before, after, command)
    if isinstance(command, FleetOperation):
        return verify_fleet(before, after, command)
    if isinstance(command, ResolvedCountryCommand):
        return verify_country_command(before, after, command)
    if isinstance(command, ResolvedControlCommand):
        return verify_control_command(before, after, command)
    if isinstance(command, ResolvedEventCommand):
        return verify_event_command(before, after, command)
    if isinstance(command, ResolvedDiplomacyCommand):
        return verify_diplomacy_command(before, after, command)
    if isinstance(command, ResolvedDecisionCommand):
        return verify_decision_command(before, after, command)
    return False


def extended_payload(command, token):
    if not isinstance(token, str) or re.fullmatch(r"[0-9a-f]{32}", token, re.ASCII) is None:
        raise ValueError("Invalid native operation token")
    if isinstance(command, ColonyShipConstruction):
        colony_ship_request(command.starbase_id, command.design_id, command.species_id)
        return colony_ship_payload(command, token)
    if isinstance(command, ShipConstruction):
        ship_request(command.starbase_id, command.design_id)
        _id(command.station_id)
        _id(command.queue_id)
        return {"kind": command.kind, "country_id": "0", "token": token, **command.to_dict()}
    if isinstance(command, ResolvedPlanetCommand):
        return native_planet_payload(command, token)
    if isinstance(command, FleetOperation):
        fleet_request(command.kind, command.fleet_id, command.planet_id, command.station_type)
        _id(command.system_id)
        return {"kind": command.kind, "country_id": "0", "token": token, **command.to_dict()}
    if isinstance(command, ResolvedCountryCommand):
        return native_country_payload(command, token)
    if isinstance(command, ResolvedControlCommand):
        return native_control_payload(command, token)
    if isinstance(command, ResolvedEventCommand):
        return native_event_payload(command, token)
    if isinstance(command, ResolvedDiplomacyCommand):
        return native_diplomacy_payload(command, token)
    if isinstance(command, ResolvedDecisionCommand):
        return native_decision_payload(command, token)
    raise ValueError("Unsupported resolved native command")
