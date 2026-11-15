"""Ship size, section and component reference compiled from the knowledge bundle.

Never invents numeric values: a missing, scripted or still-symbolic field is
reported with known=false (or omitted) rather than a guessed default.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from pathlib import Path
from typing import Any


_NUMERIC = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")
_SYMBOL = re.compile(r"^@")


def _scalar(value):
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)) and type(value) is not bool:
        return value
    if isinstance(value, str):
        text = value.strip().strip('"')
        if _SYMBOL.search(text) or text.startswith("[") or "\n" in text:
            return None
        if _NUMERIC.fullmatch(text):
            return float(text) if "." in text else int(text)
        return text
    return None


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        # Repeated clausewitz keys may be stored as {"0": "a", "1": "b"} or a bare token.
        if all(str(key).isdigit() or key is None for key in value):
            return list(value.values())
        return [value]
    return [value]


def _strings(value) -> list[str]:
    result = []
    for item in _as_list(value):
        if isinstance(item, str) and not _SYMBOL.search(item):
            result.append(item.strip().strip('"'))
        elif isinstance(item, dict):
            for key in ("name", "key", "id"):
                if isinstance(item.get(key), str):
                    result.append(item[key])
                    break
    return [item for item in result if item]


def _prereqs(definition: dict) -> list[str]:
    return _strings(definition.get("prerequisites") or definition.get("prerequisite"))


def _resources(definition: dict) -> dict | None:
    raw = definition.get("resources")
    if not isinstance(raw, dict):
        return None
    out = {}
    for bucket in ("cost", "upkeep", "logistics", "produces"):
        block = raw.get(bucket)
        if not isinstance(block, dict):
            continue
        values = {}
        known = True
        for key, value in block.items():
            if key in {"category", "trigger", "potential", "mult"} or str(key).startswith("inline"):
                if isinstance(value, dict):
                    known = False
                continue
            number = _scalar(value)
            if number is None:
                known = False
                continue
            if isinstance(number, (int, float)) and type(number) is not bool:
                values[key] = number
        if values:
            out[bucket] = values
        if not known:
            out.setdefault("known", True)
            out[bucket + "_known"] = False if bucket not in out else out.get(bucket + "_known", True)
    if not out:
        return None
    out.setdefault("known", True)
    return out


def _modifiers(definition: dict) -> dict:
    found = {}
    for key in ("modifier", "ship_modifier", "station_modifier", "triggered_ship_modifier"):
        raw = definition.get(key)
        if raw is None:
            continue
        if not isinstance(raw, dict):
            found[key + "_known"] = False
            continue
        values, known = {}, True
        for name, value in raw.items():
            if name in {"potential", "trigger", "mult"} or str(name).startswith("inline"):
                known = False
                continue
            number = _scalar(value)
            if number is None:
                known = False
                continue
            values[name] = number
        entry = {"effects": values, "known": known}
        found[key] = entry
    return found


def _csv_stats(path: Path) -> dict[str, dict]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    rows = {}
    header = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.lstrip().startswith("#"):
            continue
        parts = next(csv.reader(io.StringIO(line), delimiter=";"))
        if header is None:
            header = [part.strip() for part in parts]
            continue
        if not parts or not parts[0] or parts[0].startswith("#"):
            continue
        entry = {}
        known = True
        for name, value in zip(header, parts):
            if not name or name == "end":
                continue
            if name == "key":
                entry["key"] = value
                continue
            if value in {"", None}:
                continue
            number = _scalar(value)
            if number is None:
                known = False
                continue
            entry[name] = number
        if "key" in entry:
            entry["weapon_stats_known"] = known
            entry["source"] = path.name
            rows[entry["key"]] = entry
    return rows


def load_weapon_stats(game_dir: Path | None) -> dict[str, dict]:
    if game_dir is None:
        return {}
    folder = Path(game_dir) / "common" / "component_templates"
    if not folder.is_dir():
        return {}
    merged = {}
    for path in sorted(folder.glob("*.csv")):
        try:
            merged.update(_csv_stats(path))
        except OSError:
            continue
    return merged


def _records(bundle: dict, category: str) -> list[dict]:
    records = bundle.get("records") if isinstance(bundle, dict) else None
    if not isinstance(records, dict):
        return []
    return [record for record in records.values()
            if isinstance(record, dict) and record.get("category") == category]


def _definition(record: dict) -> dict:
    definition = record.get("definition")
    return definition if isinstance(definition, dict) else {}


def compile_ship_reference(bundle: dict, *, weapon_stats: dict[str, dict] | None = None,
                           sources: list[str] | None = None) -> dict:
    """Build a structured, exact-number ship index from already-expanded records."""
    weapon_stats = weapon_stats or {}
    sizes, sections, components = [], [], []
    for record in _records(bundle, "ship_sizes"):
        definition = _definition(record)
        slots = definition.get("section_slots")
        section_slots = []
        slots_known = isinstance(slots, dict)
        if isinstance(slots, dict):
            for name, value in slots.items():
                locator = None
                if isinstance(value, dict):
                    locator = value.get("locator")
                    if isinstance(locator, dict):
                        locator = locator.get("name") or next(iter(locator.values()), None)
                section_slots.append({"slot": name, "locator": locator if isinstance(locator, str) else None})
        required = _strings(definition.get("required_component_set"))
        item = {
            "id": record.get("id"),
            "class": _scalar(definition.get("class")),
            "section_slots": section_slots,
            "section_slots_known": slots_known,
            "required_component_sets": required,
            "prerequisites": _prereqs(definition),
            "resources": _resources(definition),
            "max_hitpoints": _scalar(definition.get("max_hitpoints")),
            "max_speed": _scalar(definition.get("max_speed")),
            "acceleration": _scalar(definition.get("acceleration")),
            "rotation_speed": _scalar(definition.get("rotation_speed")),
            "size_multiplier": _scalar(definition.get("size_multiplier")),
            "fleet_slot_size": _scalar(definition.get("fleet_slot_size")),
            "base_buildtime": _scalar(definition.get("base_buildtime")),
            "combat_disengage_chance": _scalar(definition.get("combat_disengage_chance")),
            "is_space_station": _scalar(definition.get("is_space_station")),
            "names": record.get("names") if isinstance(record.get("names"), dict) else {},
            "sources": list(record.get("sources") or []),
        }
        for key in ("max_hitpoints", "max_speed", "acceleration", "rotation_speed",
                    "size_multiplier", "fleet_slot_size", "base_buildtime", "combat_disengage_chance"):
            if item[key] is None and definition.get(key) is not None:
                item[key + "_known"] = False
        sizes.append(item)
    for record in _records(bundle, "section_templates"):
        definition = _definition(record)
        ship_sizes = _strings(definition.get("ship_size") or definition.get("ship_sizes"))
        slots = []
        slots_known = True
        raw_slots = definition.get("component_slot")
        for slot in _as_list(raw_slots):
            if not isinstance(slot, dict):
                slots_known = False
                continue
            slot_size = slot.get("slot_size") or slot.get("size")
            if slot_size is None and isinstance(slot.get("template"), str):
                slot_size = slot.get("template")
            slots.append({
                "name": slot.get("name") if isinstance(slot.get("name"), str) else None,
                "slot_size": slot_size if isinstance(slot_size, str) else None,
                "template": slot.get("template") if isinstance(slot.get("template"), str) else None,
                "locator": slot.get("locator") if isinstance(slot.get("locator"), str) else None,
            })
            if slot.get("name") is not None and not isinstance(slot.get("name"), str):
                slots_known = False
        if raw_slots is None:
            slots_known = False
        sections.append({
            "id": record.get("id") or definition.get("key"),
            "key": definition.get("key") if isinstance(definition.get("key"), str) else record.get("id"),
            "ship_sizes": ship_sizes,
            "fits_on_entity": definition.get("fits_on_entity") if isinstance(definition.get("fits_on_entity"), str) else None,
            "component_slots": slots,
            "component_slots_known": slots_known,
            "prerequisites": _prereqs(definition),
            "names": record.get("names") if isinstance(record.get("names"), dict) else {},
            "sources": list(record.get("sources") or []),
        })
    for record in _records(bundle, "component_templates"):
        definition = _definition(record)
        key = definition.get("key") if isinstance(definition.get("key"), str) else record.get("id")
        size = definition.get("size") if isinstance(definition.get("size"), str) else None
        typ = definition.get("type") if isinstance(definition.get("type"), str) else None
        tags = _strings(definition.get("tags"))
        stats = dict(weapon_stats.get(key) or {})
        item = {
            "id": key,
            "slot_size": size,
            "component_type": typ,
            "component_set": definition.get("component_set") if isinstance(definition.get("component_set"), str) else None,
            "tags": tags,
            "prerequisites": _prereqs(definition),
            "upgrades_to": definition.get("upgrades_to") if isinstance(definition.get("upgrades_to"), str) else None,
            "resources": _resources(definition),
            "names": record.get("names") if isinstance(record.get("names"), dict) else {},
            "sources": list(record.get("sources") or []),
        }
        item.update(_modifiers(definition))
        if stats:
            for field in ("cost", "power", "min_damage", "max_damage", "hull_damage", "shield_damage",
                          "shield_penetration", "armor_damage", "armor_penetration", "min_windup",
                          "max_windup", "cooldown", "range", "accuracy", "tracking", "size_damage_factor",
                          "military_power_multiplier", "missile_speed", "missile_evasion",
                          "missile_shield", "missile_armor", "missile_health", "missile_retarget_range"):
                if field in stats:
                    item[field] = stats[field]
            item["weapon_stats_known"] = stats.get("weapon_stats_known", True)
            item["weapon_stats_source"] = stats.get("source")
        elif typ in {"instant", "triggered", "missile", "strike_craft"} or any(
                tag.startswith("weapon") or tag.endswith("_slot") and "weapon" in " ".join(tags)
                for tag in tags):
            item["weapon_stats_known"] = False
        components.append(item)
    payload = {
        "schema": "qs-ship-reference-v1",
        "game_version": bundle.get("game_version"),
        "ship_sizes": sizes,
        "section_templates": sections,
        "component_templates": components,
        "size_count": len(sizes),
        "section_count": len(sections),
        "component_count": len(components),
        "weapon_stat_count": len(weapon_stats),
    }
    if sources:
        payload["provenance_sha256"] = hashlib.sha256("\n".join(sorted(sources)).encode("utf-8")).hexdigest()
    return payload


def ship_reference(bundle: dict, *, game_dir: Path | None = None) -> dict:
    cached = bundle.get("ship_reference") if isinstance(bundle, dict) else None
    if isinstance(cached, dict) and cached.get("schema") == "qs-ship-reference-v1":
        return cached
    stats = load_weapon_stats(game_dir) if game_dir is not None else {}
    return compile_ship_reference(bundle, weapon_stats=stats)


def list_ship_sizes(bundle: dict, *, game_dir: Path | None = None) -> dict:
    reference = ship_reference(bundle, game_dir=game_dir)
    return {
        "status": "listed",
        "game_version": reference.get("game_version") or bundle.get("game_version"),
        "bundle_revision": bundle.get("bundle_revision"),
        "ship_sizes": reference.get("ship_sizes") or [],
        "known": True,
    }


def list_ship_sections(bundle: dict, ship_size: str, *, game_dir: Path | None = None) -> dict:
    if not isinstance(ship_size, str) or not ship_size.strip():
        return {"status": "blocked", "reason": "ship_size is required"}
    wanted = ship_size.strip()
    reference = ship_reference(bundle, game_dir=game_dir)
    sections = [section for section in reference.get("section_templates") or []
                if wanted in (section.get("ship_sizes") or []) or not section.get("ship_sizes") and wanted == section.get("id")]
    size = next((item for item in reference.get("ship_sizes") or [] if item.get("id") == wanted), None)
    if size is None and not sections:
        return {"status": "not_found", "ship_size": wanted, "known": False,
                "reason": "No ship size or section template matched that id"}
    return {
        "status": "listed",
        "game_version": reference.get("game_version") or bundle.get("game_version"),
        "bundle_revision": bundle.get("bundle_revision"),
        "ship_size": wanted,
        "section_slots": (size or {}).get("section_slots"),
        "section_slots_known": (size or {}).get("section_slots_known"),
        "required_component_sets": (size or {}).get("required_component_sets"),
        "sections": sections,
        "known": True,
    }


def list_ship_components(bundle: dict, *, slot_size: str | None = None, component_type: str | None = None,
                         query: str | None = None, game_dir: Path | None = None) -> dict:
    reference = ship_reference(bundle, game_dir=game_dir)
    items = list(reference.get("component_templates") or [])
    if isinstance(slot_size, str) and slot_size.strip():
        token = slot_size.strip().casefold()
        items = [item for item in items if str(item.get("slot_size") or "").casefold() == token
                 or token in {tag.casefold() for tag in item.get("tags") or []}]
    if isinstance(component_type, str) and component_type.strip():
        token = component_type.strip().casefold()
        items = [item for item in items if str(item.get("component_type") or "").casefold() == token
                 or str(item.get("component_set") or "").casefold() == token
                 or token in {tag.casefold() for tag in item.get("tags") or []}]
    if isinstance(query, str) and query.strip():
        terms = query.casefold().split()
        filtered = []
        for item in items:
            names = " ".join(str(value) for value in (item.get("names") or {}).values())
            haystack = " ".join([str(item.get("id") or ""), names, str(item.get("component_set") or ""),
                                 " ".join(item.get("tags") or [])]).casefold()
            if all(term in haystack for term in terms):
                filtered.append(item)
        items = filtered
    return {
        "status": "listed",
        "game_version": reference.get("game_version") or bundle.get("game_version"),
        "bundle_revision": bundle.get("bundle_revision"),
        "slot_size": slot_size,
        "component_type": component_type,
        "query": query,
        "components": items,
        "count": len(items),
        "known": True,
    }


def _completed_techs(state: dict) -> tuple[set[str] | None, bool]:
    status = state.get("research_status") if isinstance(state, dict) else None
    if not isinstance(status, dict):
        return None, False
    completed = status.get("completed") or status.get("completed_technologies") or status.get("technologies")
    if isinstance(completed, list) and all(isinstance(item, str) for item in completed):
        return set(completed), True
    if isinstance(completed, dict):
        names = [key for key, value in completed.items() if value in (True, "yes", 1)]
        if names:
            return set(names), True
    return None, False


def list_live_ship_designs(observation: dict, bundle: dict, *, game_dir: Path | None = None) -> dict:
    state = observation.get("state") if isinstance(observation, dict) else None
    if not isinstance(state, dict):
        return {"status": "blocked", "reason": "No current observation", "known": False}
    shipbuilding = state.get("shipbuilding") if isinstance(state.get("shipbuilding"), dict) else {}
    designs_known = shipbuilding.get("designs_known") is True
    designs = []
    by_id = shipbuilding.get("designs_by_id") if isinstance(shipbuilding.get("designs_by_id"), dict) else {}
    for design_id, design in by_id.items():
        if not isinstance(design, dict):
            designs_known = False
            continue
        parsed = _parse_design(design)
        parsed["id"] = str(design.get("id") or design_id)
        designs.append(parsed)
    techs, techs_known = _completed_techs(state)
    reference = ship_reference(bundle, game_dir=game_dir)
    unlocked, locked = [], []
    for component in reference.get("component_templates") or []:
        prereqs = component.get("prerequisites") or []
        entry = {"id": component.get("id"), "slot_size": component.get("slot_size"),
                 "component_type": component.get("component_type"), "prerequisites": prereqs}
        if not techs_known:
            entry["unlocked_known"] = False
            unlocked.append(entry)  # listed but not claimed unlocked
            continue
        missing = [tech for tech in prereqs if tech not in techs]
        if missing:
            entry["missing_prerequisites"] = missing
            entry["unlocked"] = False
            locked.append(entry)
        else:
            entry["unlocked"] = True
            unlocked.append(entry)
    return {
        "status": "listed" if designs_known else "partial",
        "known": designs_known,
        "designs_known": designs_known,
        "designs": designs,
        "unlocked_components_known": techs_known,
        "unlocked_components": [item for item in unlocked if item.get("unlocked") is True] if techs_known else [],
        "locked_components": locked if techs_known else [],
        "component_unlocks_omitted": None if techs_known else "Research completion is not exactly observable in this snapshot",
        "game_version": bundle.get("game_version"),
        "snapshot": observation.get("snapshot"),
        "date": state.get("date"),
    }


def _parse_design(design: dict) -> dict:
    """Extract size/sections/components from a live design; never fill gaps."""
    result = {"ship_size": design.get("ship_size") if isinstance(design.get("ship_size"), str) else None,
              "sections_known": False, "sections": []}
    definition = design.get("definition")
    if not isinstance(definition, dict):
        return result
    stages = definition.get("growth_stages") or definition
    stage = None
    if isinstance(stages, dict):
        values = list(stages.values()) if not any(key in stages for key in ("section", "ship_size", "section_data")) else [stages]
        stage = next((item for item in values if isinstance(item, dict)), None)
    if not isinstance(stage, dict):
        stage = definition
    if result["ship_size"] is None and isinstance(stage.get("ship_size"), str):
        result["ship_size"] = stage.get("ship_size")
    sections = stage.get("section") or stage.get("sections") or definition.get("section")
    parsed = []
    known = True
    for section in _as_list(sections):
        if not isinstance(section, dict):
            known = False
            continue
        slots = []
        raw_slots = section.get("slot") or section.get("component") or section.get("components")
        if raw_slots is None and isinstance(section, dict):
            reserved = {"template", "section", "ship_size", "key", "id", "name"}
            mapped = {key: value for key, value in section.items() if key not in reserved}
            slot_source = mapped if mapped else None
        else:
            slot_source = raw_slots
        if isinstance(slot_source, dict) and all(not str(key).isdigit() for key in slot_source):
            # map of slot_name -> component key or {component=...}
            for name, value in slot_source.items():
                if name in {"template", "section", "ship_size", "key"}:
                    continue
                component = value if isinstance(value, str) else (value.get("component") or value.get("template") or value.get("key")) if isinstance(value, dict) else None
                if component is None:
                    known = False
                    continue
                slots.append({"slot": name, "component": component if isinstance(component, str) else None})
        else:
            for item in _as_list(slot_source):
                if isinstance(item, str):
                    slots.append({"slot": None, "component": item})
                    known = False  # slot name missing
                elif isinstance(item, dict):
                    slots.append({
                        "slot": item.get("slot") or item.get("name") or item.get("locator"),
                        "component": item.get("component") or item.get("template") or item.get("key"),
                    })
                else:
                    known = False
        parsed.append({
            "template": section.get("template") or section.get("section") or section.get("key"),
            "slots": slots,
        })
    if sections is None:
        known = False
    result["sections"] = parsed
    result["sections_known"] = known and bool(parsed)
    return result
