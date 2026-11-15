"""Compact planning views of an already-read observation. Never requests a dump."""
from __future__ import annotations

VIEW_BRIEF = "brief"
VIEW_FULL = "full"
VIEWS = frozenset({VIEW_BRIEF, VIEW_FULL})

# Keep these state fields when present. Missing keys are omitted; a present
# value with known=false is kept as-is and never rewritten as an empty list.
# Planets, research, events, rights and policies are compacted first.
_BRIEF_STATE = (
    "date", "country_id", "name", "resources", "paused", "pause_known",
    "fleets", "ships", "wars", "claims", "construction", "diplomacy",
    "army_queues", "starbase_slots", "control_state",
    "planets", "research", "pending_events", "species_rights", "policies_edicts",
)
_BRIEF_TOP = ("status", "snapshot", "modified_at", "date", "deferred_verification")


def observation_view(value: str | None) -> str:
    if value is None:
        return VIEW_BRIEF
    if not isinstance(value, str) or value not in VIEWS:
        raise ValueError("view must be 'brief' or 'full'")
    return value


def apply_observation_view(observed, view: str = VIEW_BRIEF):
    view = observation_view(view)
    if view == VIEW_FULL or not isinstance(observed, dict):
        return observed
    brief = {key: observed[key] for key in _BRIEF_TOP if key in observed}
    state = observed.get("state")
    if isinstance(state, dict):
        compact = _compact_brief_state(state)
        sliced = {key: compact[key] for key in _BRIEF_STATE if key in compact}
        control = sliced.get("control_state")
        if isinstance(control, dict):
            sliced["control_state"] = {key: control[key] for key in
                                       ("fleet_stances", "queue_orders", "starbase_queues") if key in control}
        brief["state"] = sliced
    return brief


def _compact_brief_state(state: dict) -> dict:
    compact = dict(state)
    planets = _brief_planets(state)
    if planets is not None:
        compact["planets"] = planets
    research = _brief_research(state)
    if research is not None:
        compact["research"] = research
    events = _brief_events(state)
    if events is not None:
        compact["pending_events"] = events
    rights = _brief_species_rights(state)
    if rights is not None:
        compact["species_rights"] = rights
    policies = _brief_policies(state)
    if policies is not None:
        compact["policies_edicts"] = policies
    return compact


def _brief_planets(state: dict):
    planets = state.get("planets")
    if planets is None:
        return None
    if not isinstance(planets, list):
        # Unknown shape: keep as-is rather than invent a list.
        return planets
    queues = _construction_by_planet(state.get("construction"))
    compact = []
    for planet in planets:
        if not isinstance(planet, dict):
            compact.append(planet)
            continue
        item = {"planet_id": planet.get("id"), "name": planet.get("name")}
        if planet.get("districts_known") is True:
            item["districts"] = planet.get("district_levels")
        elif "districts_known" in planet:
            item["districts_known"] = planet.get("districts_known")
        if planet.get("zones_known") is True:
            zones = []
            for zone_id, zone in (planet.get("zones_by_id") or {}).items() if isinstance(planet.get("zones_by_id"), dict) else []:
                if not isinstance(zone, dict):
                    continue
                entry = {
                    "zone_id": zone.get("id") or zone_id,
                    "district_id": zone.get("district_id"),
                    "type": zone.get("type"),
                }
                if zone.get("buildings_known") is True and isinstance(zone.get("buildings_by_id"), dict):
                    entry["buildings"] = [
                        {"building_id": building.get("id") or key, "type": building.get("type"),
                         "position": building.get("position")}
                        for key, building in zone["buildings_by_id"].items() if isinstance(building, dict)
                    ]
                elif "buildings_known" in zone:
                    entry["buildings_known"] = zone.get("buildings_known")
                zones.append(entry)
            item["zones"] = zones
        elif "zones_known" in planet:
            item["zones_known"] = planet.get("zones_known")
        planet_id = str(planet.get("id") or "")
        if queues is not None and planet_id in queues:
            item["construction_queue"] = queues[planet_id]
        compact.append(item)
    return compact


def _construction_by_planet(construction):
    if not isinstance(construction, dict):
        return None
    items = construction.get("items_by_id") or construction.get("by_id")
    if not isinstance(items, dict):
        return None
    queues = {}
    for key, item in items.items():
        if not isinstance(item, dict):
            continue
        planet_id = item.get("planet_id") or item.get("planet")
        if planet_id is None:
            continue
        planet_id = str(planet_id)
        queues.setdefault(planet_id, []).append({"id": item.get("id") or key, **{
            field: item[field] for field in ("type", "building", "district", "progress", "queue_id") if field in item
        }})
    return queues


def _brief_research(state: dict):
    status = state.get("research_status")
    if status is None:
        return None
    if not isinstance(status, dict):
        return status
    areas = {}
    raw_areas = status.get("areas") if isinstance(status.get("areas"), dict) else {}
    for name, area in raw_areas.items():
        if not isinstance(area, dict):
            areas[name] = area
            continue
        entry = {}
        for key in ("current", "alternatives", "queue", "auto_researching"):
            if key in area:
                entry[key] = area[key]
        for flag in ("alternatives_known", "queue_known", "current_known", "known"):
            if area.get(flag) is False:
                entry[flag] = False
        areas[name] = entry
    result = {"areas": areas} if areas else {}
    for key in ("known", "completed_known"):
        if key in status:
            result[key] = status[key]
    return result or status


def _brief_events(state: dict):
    described = state.get("pending_events_described")
    if isinstance(described, dict):
        events = []
        for event in described.get("events") or described.get("by_id") or []:
            if isinstance(event, dict):
                events.append(_compact_event(event))
            elif isinstance(event, str) and isinstance(described.get("by_id"), dict):
                events.append(_compact_event({"id": event, **described["by_id"][event]} if isinstance(described["by_id"].get(event), dict) else {"id": event}))
        if not events and isinstance(described.get("by_id"), dict):
            events = [_compact_event({"id": key, **value} if isinstance(value, dict) else {"id": key})
                      for key, value in described["by_id"].items()]
        return {"known": described.get("known"), "events": events,
                **({"other_country_event_count": described["other_country_event_count"]}
                   if "other_country_event_count" in described else {})}
    pending = state.get("pending_events")
    if pending is None:
        return None
    if not isinstance(pending, dict):
        return pending
    events = []
    by_id = pending.get("by_id") if isinstance(pending.get("by_id"), dict) else {}
    for key, value in by_id.items():
        if not isinstance(value, dict):
            continue
        country = str(value.get("country") or "")
        if country and country != "0":
            continue
        events.append(_compact_event({"id": value.get("id") or key, **value}))
    others = sum(1 for value in by_id.values()
                 if isinstance(value, dict) and str(value.get("country") or "") not in {"", "0"})
    result = {"known": pending.get("known"), "events": events}
    if others:
        result["other_country_event_count"] = others
    return result


def _compact_event(event: dict) -> dict:
    definition = event.get("definition") if isinstance(event.get("definition"), dict) else {}
    item = {
        "player_event_id": event.get("player_event_id") or event.get("id"),
        "event_id": event.get("event_id") or event.get("event"),
        "title": event.get("title") or definition.get("title"),
        "date": event.get("date"),
    }
    options = event.get("options") or definition.get("options")
    if isinstance(options, list):
        item["options"] = [
            {
                "index": option.get("index") if isinstance(option, dict) else None,
                "name": (option.get("name") or option.get("title") or option.get("id")) if isinstance(option, dict) else option,
            }
            for option in options
        ]
    elif "option" in event:
        item["selected_option"] = event.get("option")
    return {key: value for key, value in item.items() if value is not None}


def _brief_species_rights(state: dict):
    rights = state.get("species_rights")
    if rights is None:
        return None
    if not isinstance(rights, dict):
        return rights
    entries = rights.get("entries") if isinstance(rights.get("entries"), dict) else {}
    primary = entries.get("primary") if isinstance(entries.get("primary"), dict) else None
    result = {"known": rights.get("known"), "primary": primary}
    return result


def _brief_policies(state: dict):
    controls = state.get("country_controls")
    if controls is None:
        return None
    if not isinstance(controls, dict):
        return controls
    policies = []
    for policy, entry in (controls.get("policies") or {}).items() if isinstance(controls.get("policies"), dict) else []:
        if not isinstance(entry, dict):
            policies.append({"policy": policy, "selected": entry})
            continue
        policies.append({"policy": policy, "selected": entry.get("selected"), "changed_on": entry.get("date")})
    edicts = []
    for edict, entry in (controls.get("edicts") or {}).items() if isinstance(controls.get("edicts"), dict) else []:
        item = {"edict": edict}
        if isinstance(entry, dict):
            for key in ("date", "start_date", "end_date"):
                if key in entry:
                    item[key] = entry[key]
        edicts.append(item)
    return {"known": controls.get("known"), "policies": policies, "edicts": edicts}
