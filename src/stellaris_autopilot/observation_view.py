"""Compact planning views of an already-read observation. Never requests a dump."""
from __future__ import annotations

VIEW_BRIEF = "brief"
_GENERIC = "Normal native command validation rejected"


def _split(items, key="enactable"):
    """Brief keeps enactable items and rejections with a specific game reason; generic rejections are counted."""
    if not isinstance(items, list):
        return items, None
    kept, generic = [], 0
    for item in items:
        if isinstance(item, dict) and item.get(key) is False and not item.get("selected") and item.get("reason") in (None, _GENERIC):
            generic += 1
            continue
        kept.append(item)
    return kept, generic
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
    "availability",
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
    availability = _brief_availability(state)
    if availability is not None:
        compact["availability"] = availability
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
        info = _availability_planet(state, planet_id)
        if info is not None:
            if info.get("available_decisions_known") is True:
                kept, generic = _split(info.get("available_decisions"))
                item["available_decisions"] = kept
                if generic:
                    item["decisions_rejected_without_reason"] = generic
            else:
                item["available_decisions_known"] = False
            if info.get("active_decisions") is not None:
                item["active_decisions"] = info.get("active_decisions")
        construction = _availability_construction_planet(state, planet_id)
        if construction is not None:
            item["available_districts"] = construction.get("available_districts")
            item["available_buildings"] = construction.get("available_buildings")
            if construction.get("districts_rejected_without_reason"):
                item["districts_rejected_without_reason"] = construction["districts_rejected_without_reason"]
            if construction.get("buildings_rejected_without_reason"):
                item["buildings_rejected_without_reason"] = construction["buildings_rejected_without_reason"]
            if construction.get("known") is False:
                item["available_construction_known"] = False
        zones = _availability_zone_slots(state, planet_id)
        if zones is not None:
            item["zone_slots"] = zones.get("districts")
            if zones.get("known") is False:
                item["available_zones_known"] = False
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
    availability = state.get("availability") if isinstance(state.get("availability"), dict) else {}
    rights = availability.get("species_rights") if isinstance(availability.get("species_rights"), dict) else state.get("species_rights")
    if rights is None:
        return None
    if not isinstance(rights, dict):
        return rights
    if isinstance(rights.get("entries"), list):
        return {"known": rights.get("known"), "entries": rights.get("entries"),
                "options_source": rights.get("options_source"), "note": rights.get("note")}
    entries = rights.get("entries") if isinstance(rights.get("entries"), dict) else {}
    primary = entries.get("primary") if isinstance(entries.get("primary"), dict) else None
    result = {"known": rights.get("known"), "primary": primary}
    others = {name: entry for name, entry in entries.items() if name != "primary"}
    if others:
        result["other_species"] = others
    return result


def _brief_policies(state: dict):
    availability = state.get("availability") if isinstance(state.get("availability"), dict) else {}
    policies_block = availability.get("policies") if isinstance(availability.get("policies"), dict) else None
    edicts_block = availability.get("edicts") if isinstance(availability.get("edicts"), dict) else None
    if policies_block is not None or edicts_block is not None:
        policies = []
        rejected = 0
        for entry in (policies_block or {}).get("policies") or []:
            if not isinstance(entry, dict):
                continue
            kept, generic = _split(entry.get("options"))
            rejected += generic or 0
            policies.append({**entry, "options": kept})
        edicts, edicts_rejected = _split((edicts_block or {}).get("activatable"))
        result = {
            "known": (policies_block or {}).get("known") if policies_block is not None else (edicts_block or {}).get("known"),
            "policies": policies if policies_block is not None else None,
            "edicts_active": (edicts_block or {}).get("active"),
            "edicts_activatable": edicts,
        }
        if rejected:
            result["policy_options_rejected_without_reason"] = rejected
        if edicts_rejected:
            result["edicts_rejected_without_reason"] = edicts_rejected
        if (policies_block or {}).get("reason"):
            result["policies_reason"] = policies_block["reason"]
        if (edicts_block or {}).get("reason"):
            result["edicts_reason"] = edicts_block["reason"]
        return result
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


def _availability_planet(state: dict, planet_id: str):
    availability = state.get("availability") if isinstance(state.get("availability"), dict) else None
    if not isinstance(availability, dict) or not isinstance(availability.get("planets"), list):
        return None
    for item in availability["planets"]:
        if isinstance(item, dict) and str(item.get("planet_id")) == str(planet_id):
            return item
    return None


def _availability_construction_planet(state: dict, planet_id: str):
    availability = state.get("availability") if isinstance(state.get("availability"), dict) else None
    construction = availability.get("construction") if isinstance(availability, dict) else None
    if not isinstance(construction, dict) or not isinstance(construction.get("planets"), list):
        return None
    for item in construction["planets"]:
        if not isinstance(item, dict) or str(item.get("planet_id")) != str(planet_id):
            continue
        districts, district_generic = _split(item.get("available_districts") or [])
        buildings = []
        building_generic = 0
        for zone in item.get("zones") or []:
            if not isinstance(zone, dict):
                continue
            available, generic = _split(zone.get("available_buildings") or [])
            if generic:
                building_generic += generic
            if available:
                buildings.append({"zone_id": zone.get("zone_id"), "available_buildings": available})
        result = {"known": construction.get("known"), "available_districts": districts,
                  "available_buildings": buildings}
        if district_generic:
            result["districts_rejected_without_reason"] = district_generic
        if building_generic:
            result["buildings_rejected_without_reason"] = building_generic
        return result
    return None


def _availability_zone_slots(state: dict, planet_id: str):
    availability = state.get("availability") if isinstance(state.get("availability"), dict) else None
    zones = availability.get("zones") if isinstance(availability, dict) else None
    if not isinstance(zones, dict) or not isinstance(zones.get("planets"), list):
        return None
    for item in zones["planets"]:
        if not isinstance(item, dict) or str(item.get("planet_id")) != str(planet_id):
            continue
        districts = []
        generic_total = 0
        for district in item.get("districts") or []:
            if not isinstance(district, dict):
                continue
            slots = []
            for slot in district.get("slots") or []:
                if not isinstance(slot, dict):
                    continue
                available, generic = _split(slot.get("available_zones") or [])
                if generic:
                    generic_total += generic
                entry = {key: slot[key] for key in ("slot_index", "slot_key", "occupied", "current")
                         if key in slot}
                entry["available_zones"] = available
                if generic:
                    entry["zones_rejected_without_reason"] = generic
                slots.append(entry)
            districts.append({"district_id": district.get("district_id"),
                              "district_type": district.get("district_type"), "slots": slots})
        result = {"known": zones.get("known"), "districts": districts}
        if generic_total:
            result["zones_rejected_without_reason"] = generic_total
        return result
    return None


def _brief_availability(state: dict):
    availability = state.get("availability")
    if availability is None:
        return None
    if not isinstance(availability, dict):
        return availability
    result = {"known": availability.get("known")}
    if availability.get("reason"):
        result["reason"] = availability["reason"]
    if availability.get("skipped"):
        result["skipped"] = availability["skipped"]
    for key in ("candidates_checked", "query_seconds"):
        if key in availability:
            result[key] = availability[key]
    war = availability.get("war_goals")
    if isinstance(war, dict):
        result["war_goals"] = {
            "known": war.get("known"),
            "by_country": [
                {"country_id": entry.get("country_id"),
                 "war_goals": [goal for goal in entry.get("war_goals") or []
                               if isinstance(goal, dict) and goal.get("enactable") is True]}
                for entry in war.get("by_country") or [] if isinstance(entry, dict)
            ] if war.get("known") is True else None,
        }
        if war.get("reason"):
            result["war_goals"]["reason"] = war["reason"]
    starbases = availability.get("starbases")
    if isinstance(starbases, dict):
        listed = []
        if starbases.get("known") is True:
            for entry in starbases.get("starbases") or []:
                if not isinstance(entry, dict):
                    continue
                modules = []
                for slot in entry.get("modules") or []:
                    if not isinstance(slot, dict):
                        continue
                    available = [item for item in slot.get("available") or []
                                 if isinstance(item, dict) and item.get("enactable") is True]
                    if available:
                        modules.append({"slot": slot.get("slot"), "current": slot.get("current"),
                                        "available": available})
                buildings = []
                for slot in entry.get("buildings") or []:
                    if not isinstance(slot, dict):
                        continue
                    available = [item for item in slot.get("available") or []
                                 if isinstance(item, dict) and item.get("enactable") is True]
                    if available:
                        buildings.append({"slot": slot.get("slot"), "current": slot.get("current"),
                                          "available": available})
                listed.append({"starbase_id": entry.get("starbase_id"), "modules": modules,
                               "buildings": buildings})
        result["starbases"] = {"known": starbases.get("known"), "starbases": listed if starbases.get("known") is True else None,
                               **({"reason": starbases["reason"]} if starbases.get("reason") else {})}
    return result
