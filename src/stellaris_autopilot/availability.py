"""Build compact, game-validated availability for planning views.

Candidates are checked by the live native validator (potential/allow/cost).
This module never posts a command. Static always=no clauses are omitted
because they can never pass; every other yes/no comes from the game.
"""
from __future__ import annotations

from .country_commands import policy_options
from .decision_commands import _never
from .species_rights_commands import CATEGORIES as SPECIES_CATEGORIES, category_of

AVAILABILITY_LIMIT = 1500  # total per observation, sent in native batches of 500
NATIVE_BATCH = 500
GENERIC_REASON = "Normal native command validation rejected"


def _records(bundle, category: str) -> list[dict]:
    records = bundle.get("records") if isinstance(bundle, dict) else None
    if not isinstance(records, dict):
        return []
    prefix = category + ":"
    return [record for key, record in records.items()
            if isinstance(record, dict) and (key.startswith(prefix) or record.get("category") == category)]


def _definition(record: dict) -> dict:
    definition = record.get("definition")
    return definition if isinstance(definition, dict) else {}


def _cost(definition: dict) -> tuple[dict | None, bool]:
    resources = definition.get("resources")
    if resources is None:
        return {}, True
    if not isinstance(resources, dict):
        return None, False
    cost = resources.get("cost")
    if cost is None:
        return {}, True
    if isinstance(cost, dict) and all(isinstance(k, str) and isinstance(v, (int, float)) and not isinstance(v, bool)
                                      for k, v in cost.items() if k not in {"trigger", "multiplier", "mult"}):
        values = {k: v for k, v in cost.items() if k not in {"trigger", "multiplier", "mult"} and isinstance(v, (int, float))}
        if any(k in cost and k not in values and k not in {"trigger"} for k in cost):
            return values or None, False
        return values, True
    return None, False


def _id_of(record: dict) -> str | None:
    ident = record.get("id") or _definition(record).get("key") or _definition(record).get("name")
    return ident if isinstance(ident, str) else None


def planet_identities(state: dict) -> list[dict]:
    planets = state.get("planets") if isinstance(state, dict) else None
    if not isinstance(planets, list):
        return []
    items = []
    for planet in planets:
        if not isinstance(planet, dict) or planet.get("id") is None:
            continue
        colony = planet.get("colony")
        colony_id = colony if isinstance(colony, str) else (str(colony) if isinstance(colony, int) else None)
        if isinstance(colony, dict):
            colony_id = colony.get("id") or colony.get("colony_id")
        queue = planet.get("build_queue") or planet.get("queue")
        queue_id = queue if isinstance(queue, str) else (str(queue) if isinstance(queue, int) else None)
        if isinstance(queue, dict):
            queue_id = queue.get("id") or queue.get("queue_id")
        items.append({
            "planet_id": str(planet.get("id")),
            "name": planet.get("name"),
            "colony_id": str(colony_id) if colony_id is not None else None,
            "queue_id": str(queue_id) if queue_id is not None else None,
        })
    return items


def _construction_for_planet(state: dict, planet_id: str) -> list[dict]:
    construction = state.get("construction") if isinstance(state, dict) else None
    if not isinstance(construction, dict):
        return []
    items = construction.get("items_by_id") or {}
    if not isinstance(items, dict):
        return []
    found = []
    for key, item in items.items():
        if not isinstance(item, dict):
            continue
        target = item.get("planet_id") or (item.get("buildable") or {}).get("planet") if isinstance(item.get("buildable"), dict) else None
        if str(target) != planet_id:
            continue
        if item.get("buildable_type") == "buildable_decision":
            decision = (item.get("buildable") or {}).get("decision")
            found.append({"decision_id": decision, "item_id": item.get("id") or key, "queued": True})
    return found


def _active_modifiers(state: dict, planet_id: str):
    modifiers = state.get("planet_modifiers") if isinstance(state, dict) else None
    if not isinstance(modifiers, dict) or modifiers.get("known") is not True:
        return None
    names = (modifiers.get("by_planet") or {}).get(planet_id)
    return names if isinstance(names, list) else None


def build_candidates(state: dict, bundle: dict, *, limit: int = AVAILABILITY_LIMIT) -> tuple[list[dict], dict]:
    """Typed native candidates plus bookkeeping for groups that could not be queued."""
    candidates: list[dict] = []
    skipped: dict[str, str] = {}

    def add(item: dict) -> bool:
        if len(candidates) >= limit:
            return False
        candidates.append(item)
        return True

    # Decisions
    decision_records = [r for r in _records(bundle, "decisions")
                        if _definition(r).get("owned_planets_only") == "yes"
                        and not _never(_definition(r).get("potential"))
                        and not _never(_definition(r).get("allow"))
                        and _id_of(r)]
    planets = planet_identities(state)
    if not planets:
        skipped["decisions"] = "No owned planets were observed"
    else:
        for planet in planets:
            for record in decision_records:
                if not add({"kind": "enact_planet_decision", "planet_id": planet["planet_id"],
                            "decision_id": _id_of(record)}):
                    skipped.setdefault("truncated", "Availability query reached the candidate limit")
                    return candidates, skipped

    # Policy options for each currently observed policy
    controls = state.get("country_controls") if isinstance(state, dict) else None
    policies = controls.get("policies") if isinstance(controls, dict) and controls.get("known") is True else None
    if not isinstance(policies, dict):
        skipped["policies"] = "Active policies are not exactly observable"
    else:
        policy_records = {(_id_of(r) or ""): r for r in _records(bundle, "policies")}
        for policy, entry in policies.items():
            current = entry.get("selected") if isinstance(entry, dict) else None
            record = policy_records.get(policy)
            options = policy_options(record) if record else []
            names = [item["option"] for item in options if isinstance(item, dict) and isinstance(item.get("option"), str)]
            if current and current not in names:
                names.append(current)
            for option in names:
                if option == current:
                    continue  # already selected; listed as selected, not re-enacted
                if not add({"kind": "set_policy", "policy": policy, "option": option,
                            "current_option": current}):
                    skipped.setdefault("truncated", "Availability query reached the candidate limit")
                    return candidates, skipped

    # Edicts that are not already active
    edicts = controls.get("edicts") if isinstance(controls, dict) and controls.get("known") is True else None
    if not isinstance(controls, dict) or controls.get("known") is not True:
        skipped["edicts"] = "Active edicts are not exactly observable"
    else:
        active = set(edicts or {})
        for record in _records(bundle, "edicts"):
            ident = _id_of(record)
            if not ident or ident in active:
                continue
            if _never(_definition(record).get("potential")) or _never(_definition(record).get("allow")):
                continue
            if not add({"kind": "activate_edict", "edict": ident}):
                skipped.setdefault("truncated", "Availability query reached the candidate limit")
                return candidates, skipped

    # Species rights: validator is pinned for the primary species only
    rights = state.get("species_rights") if isinstance(state, dict) else None
    primary = (rights.get("entries") or {}).get("primary") if isinstance(rights, dict) and rights.get("known") is True else None
    right_records = _records(bundle, "species_rights")
    if not isinstance(primary, dict) or not primary.get("species_index"):
        skipped["species_rights"] = "Primary species rights are not exactly observable"
    else:
        species_id = str(primary["species_index"])
        known_by_category = {}
        for record in right_records:
            ident = _id_of(record)
            category = category_of(ident) if ident else None
            if not category:
                continue
            known_by_category.setdefault(category, []).append(ident)
        for category in SPECIES_CATEGORIES:
            current = primary.get(category)
            for right in known_by_category.get(category, []):
                if right == current:
                    continue
                if not add({"kind": "set_species_right", "species_id": species_id, "right": right,
                            "current_right": current, "category": category}):
                    skipped.setdefault("truncated", "Availability query reached the candidate limit")
                    return candidates, skipped

    # War goals against observed contacts (not self)
    contacts = _contact_ids(state)
    goals = [_id_of(r) for r in _records(bundle, "war_goals") if _id_of(r)]
    if not contacts:
        skipped["war_goals"] = "No contacted empires were observed"
    elif not goals:
        skipped["war_goals"] = "War goal definitions are not in the knowledge bundle"
    else:
        for country_id in contacts:
            for goal in goals:
                if not add({"kind": "declare_war", "target_country_id": country_id, "war_goal": goal}):
                    skipped.setdefault("truncated", "Availability query reached the candidate limit")
                    return candidates, skipped

    # Starbase modules/buildings per observed slot
    slots = state.get("starbase_slots") if isinstance(state, dict) else None
    if not isinstance(slots, dict) or slots.get("known") is not True:
        skipped["starbases"] = "Starbase slots are not exactly observable"
    else:
        modules = [_id_of(r) for r in _records(bundle, "starbase_modules") if _id_of(r)]
        buildings = [_id_of(r) for r in _records(bundle, "starbase_buildings") if _id_of(r)]
        for starbase_id, entry in (slots.get("by_id") or {}).items():
            if not isinstance(entry, dict):
                continue
            for slot in (entry.get("modules") or {}):
                for module_id in modules:
                    if not add({"kind": "queue_starbase_module", "starbase_id": str(starbase_id),
                                "slot": str(slot), "module_id": module_id}):
                        skipped.setdefault("truncated", "Availability query reached the candidate limit")
                        return candidates, skipped
            for slot in (entry.get("buildings") or {}):
                for building_id in buildings:
                    if not add({"kind": "queue_starbase_building", "starbase_id": str(starbase_id),
                                "slot": str(slot), "building_id": building_id}):
                        skipped.setdefault("truncated", "Availability query reached the candidate limit")
                        return candidates, skipped

    # Districts and buildings (need colony and queue identities)
    district_records = [r for r in _records(bundle, "districts") if _id_of(r) and not _never(_definition(r).get("potential"))]
    building_records = [r for r in _records(bundle, "buildings") if _id_of(r) and not _never(_definition(r).get("potential"))]
    for planet in planets:
        if not planet.get("colony_id") or not planet.get("queue_id"):
            skipped.setdefault("construction", "A planet is missing colony or build queue identity")
            continue
        for record in district_records:
            if not add({"kind": "queue_district", "planet_id": planet["planet_id"],
                        "colony_id": planet["colony_id"], "queue_id": planet["queue_id"],
                        "district": _id_of(record)}):
                skipped.setdefault("truncated", "Availability query reached the candidate limit")
                return candidates, skipped
        zones = []
        raw_planets = state.get("planets") if isinstance(state.get("planets"), list) else []
        raw = next((p for p in raw_planets if isinstance(p, dict) and str(p.get("id")) == planet["planet_id"]), {})
        if isinstance(raw.get("zones_by_id"), dict):
            zones = [str(zone.get("id") or key) for key, zone in raw["zones_by_id"].items()
                     if isinstance(zone, dict)]
        if not zones:
            skipped.setdefault("buildings", "Zone identities are not exactly observable on a planet")
            continue
        for zone_id in zones:
            for record in building_records:
                if not add({"kind": "queue_building", "planet_id": planet["planet_id"],
                            "colony_id": planet["colony_id"], "queue_id": planet["queue_id"],
                            "zone_id": zone_id, "building": _id_of(record)}):
                    skipped.setdefault("truncated", "Availability query reached the candidate limit")
                    return candidates, skipped

    return candidates, skipped


def mark_unqueried_groups(candidates: list[dict], skipped: dict) -> dict:
    """Groups with zero queued candidates after truncation are unknown, not empty."""
    if "truncated" not in skipped:
        return skipped
    kinds = {item.get("kind") for item in candidates}
    groups = {"war_goals": {"declare_war"}, "starbases": {"queue_starbase_module", "queue_starbase_building"},
              "construction": {"queue_district"}, "buildings": {"queue_building"},
              "species_rights": {"set_species_right"}, "edicts": {"activate_edict"}, "policies": {"set_policy"}}
    for group, group_kinds in groups.items():
        if group not in skipped and not (kinds & group_kinds):
            skipped[group] = skipped["truncated"]
    return skipped


def _contact_ids(state: dict) -> list[str]:
    diplomacy = state.get("diplomacy") if isinstance(state, dict) else None
    ids = set()
    by_country = diplomacy.get("by_country") if isinstance(diplomacy, dict) else None
    if isinstance(by_country, dict):
        for key, entry in by_country.items():
            if str(key) == "0":
                continue
            if isinstance(entry, dict) and entry.get("communications") is not True:
                continue
            ids.add(str(entry.get("country") or key))
    return sorted(ids, key=lambda value: int(value) if value.isdigit() else value)


def _index_results(results: list[dict]) -> dict:
    index = {}
    for item in results or []:
        if not isinstance(item, dict) or not item.get("kind"):
            continue
        key = _result_key(item)
        index[key] = item
    return index


def _result_key(item: dict) -> tuple:
    kind = item.get("kind")
    if kind == "enact_planet_decision":
        return (kind, str(item.get("planet_id")), item.get("decision_id"))
    if kind == "set_policy":
        return (kind, item.get("policy"), item.get("option"))
    if kind == "activate_edict":
        return (kind, item.get("edict"))
    if kind == "set_species_right":
        return (kind, str(item.get("species_id")), item.get("right"))
    if kind == "declare_war":
        return (kind, str(item.get("target_country_id")), item.get("war_goal"))
    if kind == "queue_starbase_module":
        return (kind, str(item.get("starbase_id")), str(item.get("slot")), item.get("module_id"))
    if kind == "queue_starbase_building":
        return (kind, str(item.get("starbase_id")), str(item.get("slot")), item.get("building_id"))
    if kind == "queue_district":
        return (kind, str(item.get("planet_id")), item.get("district"))
    if kind == "queue_building":
        return (kind, str(item.get("planet_id")), str(item.get("zone_id")), item.get("building"))
    return (kind, repr(item))


def _verdict(index: dict, key: tuple) -> dict | None:
    item = index.get(key)
    if not item:
        return None
    if item.get("error") == "ambiguous" or item.get("known") is False:
        return {"known": False}
    allowed = item.get("allowed") is True or item.get("enactable") is True
    reason = item.get("reason") if not allowed else None
    if isinstance(reason, str) and reason.startswith("Normal native command validation rejected: "):
        reason = reason[len("Normal native command validation rejected: "):]
    return {"known": True, "enactable": allowed, "reason": reason or None}


def shape_availability(state: dict, bundle: dict, results: list[dict] | None, *,
                       queried: bool, skipped: dict | None = None) -> dict:
    """Compact availability block for brief/full observations."""
    skipped = skipped or {}
    index = _index_results(results or [])
    planets_out = []
    decision_records = {(_id_of(r) or ""): r for r in _records(bundle, "decisions")}
    for planet in planet_identities(state):
        planet_id = planet["planet_id"]
        available = []
        seen_known = False
        seen_ambiguous = False
        group_ok = queried and "decisions" not in skipped
        if group_ok:
            for decision_id, record in sorted(decision_records.items()):
                if _definition(record).get("owned_planets_only") != "yes":
                    continue
                if _never(_definition(record).get("potential")) or _never(_definition(record).get("allow")):
                    continue
                verdict = _verdict(index, ("enact_planet_decision", planet_id, decision_id))
                if verdict is None:
                    continue
                if verdict.get("known") is False:
                    seen_ambiguous = True
                    continue
                seen_known = True
                cost, cost_known = _cost(_definition(record))
                # Return only items the validator could evaluate. Items that
                # clearly failed a NativePrecondition (not in DB / wrong owner)
                # stay omitted. Items rejected by the validator stay, with reason.
                if verdict.get("enactable") is False and verdict.get("reason") is None and not verdict.get("enactable"):
                    # precondition without being in index as validator-reject: omit if we marked known via precondition and no native reason
                    raw = index.get(("enact_planet_decision", planet_id, decision_id))
                    if raw and raw.get("error") == "precondition":
                        continue
                entry = {"decision_id": decision_id, "enactable": verdict.get("enactable") is True}
                if cost_known:
                    entry["cost"] = cost
                else:
                    entry["cost_known"] = False
                if not entry["enactable"] and verdict.get("reason"):
                    entry["reason"] = verdict["reason"]
                available.append(entry)
        known = group_ok and (seen_known or not seen_ambiguous)
        planets_out.append({
            "planet_id": planet_id,
            "available_decisions": available if known else None,
            "available_decisions_known": known,
            "active_decisions": {
                "queued": _construction_for_planet(state, planet_id),
                "modifiers": _active_modifiers(state, planet_id),
                "modifiers_known": _active_modifiers(state, planet_id) is not None,
            },
        })

    policies_out = _shape_policies(state, bundle, index, queried, skipped)
    edicts_out = _shape_edicts(state, bundle, index, queried, skipped)
    rights_out = _shape_rights(state, bundle, index, queried, skipped)
    war_out = _shape_war_goals(state, bundle, index, queried, skipped)
    starbases_out = _shape_starbases(state, bundle, index, queried, skipped)
    construction_out = _shape_construction(state, bundle, index, queried, skipped)
    research_out = _shape_research(state)

    return {
        "known": queried,
        "skipped": skipped or None,
        "planets": planets_out,
        "policies": policies_out,
        "edicts": edicts_out,
        "species_rights": rights_out,
        "war_goals": war_out,
        "starbases": starbases_out,
        "construction": construction_out,
        "research": research_out,
    }


def _shape_policies(state, bundle, index, queried, skipped):
    controls = state.get("country_controls") if isinstance(state, dict) else None
    if not isinstance(controls, dict) or controls.get("known") is not True:
        return {"known": False}
    if not queried or "policies" in skipped:
        return {"known": False, "reason": skipped.get("policies") or skipped.get("truncated")}
    policy_records = {(_id_of(r) or ""): r for r in _records(bundle, "policies")}
    listed = []
    known = True
    for policy, entry in sorted((controls.get("policies") or {}).items()):
        current = entry.get("selected") if isinstance(entry, dict) else None
        options = []
        names = [item["option"] for item in policy_options(policy_records.get(policy) or {})
                 if isinstance(item, dict) and isinstance(item.get("option"), str)]
        if current and current not in names:
            names.insert(0, current)
        for option in names:
            if option == current:
                options.append({"option": option, "enactable": False, "selected": True})
                continue
            verdict = _verdict(index, ("set_policy", policy, option))
            if verdict is None or verdict.get("known") is False:
                continue
            raw = index.get(("set_policy", policy, option))
            if raw and raw.get("error") == "precondition":
                continue
            item = {"option": option, "enactable": verdict.get("enactable") is True, "selected": False}
            if not item["enactable"] and verdict.get("reason"):
                item["reason"] = verdict["reason"]
            options.append(item)
        listed.append({"policy": policy, "selected": current, "options": options})
    return {"known": known, "policies": listed}


def _shape_edicts(state, bundle, index, queried, skipped):
    controls = state.get("country_controls") if isinstance(state, dict) else None
    if not isinstance(controls, dict) or controls.get("known") is not True:
        return {"known": False}
    active = [{"edict": key, **({k: v for k, v in entry.items() if k in {"date", "start_date", "end_date"}}
                                if isinstance(entry, dict) else {})}
              for key, entry in (controls.get("edicts") or {}).items()]
    if not queried or "edicts" in skipped:
        return {"known": False, "reason": skipped.get("edicts") or skipped.get("truncated"), "active": active}
    available = []
    known = True
    for record in _records(bundle, "edicts"):
        ident = _id_of(record)
        if not ident or ident in (controls.get("edicts") or {}):
            continue
        if _never(_definition(record).get("potential")) or _never(_definition(record).get("allow")):
            continue
        verdict = _verdict(index, ("activate_edict", ident))
        if verdict is None or verdict.get("known") is False:
            continue
        raw = index.get(("activate_edict", ident))
        if raw and raw.get("error") == "precondition":
            continue
        cost, cost_known = _cost(_definition(record))
        item = {"edict": ident, "enactable": verdict.get("enactable") is True}
        if cost_known:
            item["cost"] = cost
        else:
            item["cost_known"] = False
        if not item["enactable"] and verdict.get("reason"):
            item["reason"] = verdict["reason"]
        available.append(item)
    return {"known": known, "active": active, "activatable": available}


def _shape_rights(state, bundle, index, queried, skipped):
    rights = state.get("species_rights") if isinstance(state, dict) else None
    if not isinstance(rights, dict) or rights.get("known") is not True:
        return {"known": False}
    entries = []
    primary = (rights.get("entries") or {}).get("primary") if isinstance(rights.get("entries"), dict) else None
    known_by_category = {}
    for record in _records(bundle, "species_rights"):
        ident = _id_of(record)
        category = category_of(ident) if ident else None
        if category:
            known_by_category.setdefault(category, []).append(ident)
    query_ok = queried and "species_rights" not in skipped
    for name, entry in (rights.get("entries") or {}).items():
        if not isinstance(entry, dict):
            continue
        species_id = str(entry.get("species_index") or "")
        categories = {}
        options_known = query_ok and name == "primary"
        for category in SPECIES_CATEGORIES:
            current = entry.get(category)
            options = []
            if options_known:
                for right in known_by_category.get(category, []):
                    if right == current:
                        options.append({"right": right, "enactable": False, "selected": True})
                        continue
                    verdict = _verdict(index, ("set_species_right", species_id, right))
                    if verdict is None or verdict.get("known") is False:
                        continue
                    raw = index.get(("set_species_right", species_id, right))
                    if raw and raw.get("error") == "precondition":
                        continue
                    item = {"right": right, "enactable": verdict.get("enactable") is True, "selected": False}
                    if not item["enactable"] and verdict.get("reason"):
                        item["reason"] = verdict["reason"]
                    options.append(item)
            categories[category] = {"selected": current, "options": options if options_known else None,
                                    "options_known": options_known}
        entries.append({"entry": name, "species_id": species_id, "categories": categories,
                        "options_known": options_known})
    return {"known": True, "entries": entries,
            "options_source": "native_validator" if query_ok else None,
            "note": None if query_ok else skipped.get("species_rights") or "Option validity was not queried"}


def _shape_war_goals(state, bundle, index, queried, skipped):
    contacts = _contact_ids(state)
    goals = [_id_of(r) for r in _records(bundle, "war_goals") if _id_of(r)]
    if not queried or "war_goals" in skipped:
        return {"known": False, "reason": skipped.get("war_goals")}
    by_country = []
    known = True
    for country_id in contacts:
        available = []
        for goal in goals:
            verdict = _verdict(index, ("declare_war", country_id, goal))
            if verdict is None or verdict.get("known") is False:
                continue
            raw = index.get(("declare_war", country_id, goal))
            if raw and raw.get("error") == "precondition":
                continue
            item = {"war_goal": goal, "enactable": verdict.get("enactable") is True}
            if not item["enactable"] and verdict.get("reason"):
                item["reason"] = verdict["reason"]
            available.append(item)
        by_country.append({"country_id": country_id, "war_goals": available})
    return {"known": known, "by_country": by_country}


def _shape_starbases(state, bundle, index, queried, skipped):
    slots = state.get("starbase_slots") if isinstance(state, dict) else None
    if not isinstance(slots, dict) or slots.get("known") is not True:
        return {"known": False}
    if not queried or "starbases" in skipped:
        return {"known": False, "reason": skipped.get("starbases")}
    modules = [_id_of(r) for r in _records(bundle, "starbase_modules") if _id_of(r)]
    buildings = [_id_of(r) for r in _records(bundle, "starbase_buildings") if _id_of(r)]
    listed = []
    known = True
    for starbase_id, entry in (slots.get("by_id") or {}).items():
        if not isinstance(entry, dict):
            continue
        module_slots = []
        for slot in (entry.get("modules") or {}):
            available = []
            current = (entry.get("modules") or {}).get(slot) if isinstance(entry.get("modules"), dict) else slot
            for module_id in modules:
                verdict = _verdict(index, ("queue_starbase_module", str(starbase_id), str(slot), module_id))
                if verdict is None or verdict.get("known") is False:
                    continue
                raw = index.get(("queue_starbase_module", str(starbase_id), str(slot), module_id))
                if raw and raw.get("error") == "precondition":
                    continue
                item = {"module_id": module_id, "enactable": verdict.get("enactable") is True}
                if not item["enactable"] and verdict.get("reason"):
                    item["reason"] = verdict["reason"]
                available.append(item)
            module_slots.append({"slot": str(slot), "current": current if not isinstance(current, dict) else current,
                                 "available": available})
        building_slots = []
        for slot in (entry.get("buildings") or {}):
            available = []
            current = (entry.get("buildings") or {}).get(slot) if isinstance(entry.get("buildings"), dict) else slot
            for building_id in buildings:
                verdict = _verdict(index, ("queue_starbase_building", str(starbase_id), str(slot), building_id))
                if verdict is None or verdict.get("known") is False:
                    continue
                raw = index.get(("queue_starbase_building", str(starbase_id), str(slot), building_id))
                if raw and raw.get("error") == "precondition":
                    continue
                item = {"building_id": building_id, "enactable": verdict.get("enactable") is True}
                if not item["enactable"] and verdict.get("reason"):
                    item["reason"] = verdict["reason"]
                available.append(item)
            building_slots.append({"slot": str(slot), "current": current if not isinstance(current, dict) else current,
                                   "available": available})
        listed.append({"starbase_id": str(starbase_id), "modules": module_slots, "buildings": building_slots})
    return {"known": known, "starbases": listed}


def _shape_construction(state, bundle, index, queried, skipped):
    if not queried:
        return {"known": False}
    planets = []
    known = "construction" not in skipped and "buildings" not in skipped
    for planet in planet_identities(state):
        planet_id = planet["planet_id"]
        districts = []
        for record in _records(bundle, "districts"):
            ident = _id_of(record)
            if not ident or _never(_definition(record).get("potential")):
                continue
            verdict = _verdict(index, ("queue_district", planet_id, ident))
            if verdict is None:
                continue
            if verdict.get("known") is False:
                continue
            raw = index.get(("queue_district", planet_id, ident))
            if raw and raw.get("error") == "precondition":
                continue
            cost, cost_known = _cost(_definition(record))
            item = {"district_id": ident, "enactable": verdict.get("enactable") is True}
            if cost_known:
                item["cost"] = cost
            else:
                item["cost_known"] = False
            if not item["enactable"] and verdict.get("reason"):
                item["reason"] = verdict["reason"]
            districts.append(item)
        raw_planets = state.get("planets") if isinstance(state.get("planets"), list) else []
        raw = next((p for p in raw_planets if isinstance(p, dict) and str(p.get("id")) == planet_id), {})
        zones = []
        if isinstance(raw.get("zones_by_id"), dict):
            for key, zone in raw["zones_by_id"].items():
                if not isinstance(zone, dict):
                    continue
                zone_id = str(zone.get("id") or key)
                buildings = []
                for record in _records(bundle, "buildings"):
                    ident = _id_of(record)
                    if not ident or _never(_definition(record).get("potential")):
                        continue
                    verdict = _verdict(index, ("queue_building", planet_id, zone_id, ident))
                    if verdict is None:
                        continue
                    if verdict.get("known") is False:
                        known = False
                        continue
                    raw_item = index.get(("queue_building", planet_id, zone_id, ident))
                    if raw_item and raw_item.get("error") == "precondition":
                        continue
                    cost, cost_known = _cost(_definition(record))
                    item = {"building_id": ident, "enactable": verdict.get("enactable") is True}
                    if cost_known:
                        item["cost"] = cost
                    else:
                        item["cost_known"] = False
                    if not item["enactable"] and verdict.get("reason"):
                        item["reason"] = verdict["reason"]
                    buildings.append(item)
                zones.append({"zone_id": zone_id, "available_buildings": buildings})
        planets.append({"planet_id": planet_id, "available_districts": districts, "zones": zones})
    return {"known": known, "reason": skipped.get("construction") or skipped.get("buildings"), "planets": planets}


def _shape_research(state):
    status = state.get("research_status") if isinstance(state, dict) else None
    if not isinstance(status, dict):
        return {"known": False}
    areas = {}
    raw_areas = status.get("areas") if isinstance(status.get("areas"), dict) else {}
    known = True
    for name, area in raw_areas.items():
        if not isinstance(area, dict):
            known = False
            continue
        current = area.get("current")
        alternatives = area.get("alternatives")
        if area.get("alternatives_known") is False:
            known = False
        areas[name] = {
            "current": current,
            "available": alternatives if isinstance(alternatives, list) else None,
            "available_known": area.get("alternatives_known") is not False and isinstance(alternatives, list),
            "auto_researching": area.get("auto_researching"),
            "source": "observation",
        }
    return {"known": known, "areas": areas, "note": "Available technologies are the game's currently visible alternatives"}
