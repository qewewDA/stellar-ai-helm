"""Deferred settlement of fast-path journal rows against one later observation."""
from __future__ import annotations

import json
from pathlib import Path

from .journal import Journal


def evaluate_effect(before: dict, after: dict, action: str) -> tuple[bool | None, str]:
    """Positive postcondition only. True/False when clear; None if ambiguous."""
    if not isinstance(before, dict) or not isinstance(after, dict):
        return None, "Before or after state is not a dictionary"
    kind = action.split(":", 1)[0] if isinstance(action, str) else ""
    payload = before.get("_native_operation") if isinstance(before.get("_native_operation"), dict) else {}
    if not payload and ":" in action:
        try:
            payload = json.loads(action.split(":", 1)[1])
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
    checker = _CHECKERS.get(kind)
    if checker is None:
        return None, f"No deferred positive postcondition is declared for {kind or 'this action'}"
    try:
        verdict = checker(before, after, payload if isinstance(payload, dict) else {})
    except (TypeError, ValueError, KeyError, AttributeError) as exc:
        return None, f"Deferred check could not be evaluated: {exc}"
    if verdict is True:
        return True, "Positive postcondition is present in the later observation"
    if verdict is False:
        return False, "Positive postcondition is clearly absent in the later observation"
    return None, "Positive postcondition is ambiguous in the later observation; do not replay"


def _known(block) -> bool:
    return isinstance(block, dict) and block.get("known") is True


def _claim(before, after, payload):
    system_id = str(payload.get("system_id", ""))
    if not system_id:
        return None
    claims = after.get("claims")
    if not _known(claims):
        return None
    by_system = claims.get("by_system")
    if not isinstance(by_system, dict):
        return None
    return system_id in by_system


def _species_right(before, after, payload):
    species_id = str(payload.get("species_id", ""))
    right = payload.get("right")
    category = payload.get("category")
    rights = after.get("species_rights")
    if not _known(rights) or not isinstance(right, str):
        return None
    entries = rights.get("entries")
    if not isinstance(entries, dict):
        return None
    entry = None
    if species_id:
        for value in entries.values():
            if isinstance(value, dict) and str(value.get("species_index") or value.get("species_id") or "") == species_id:
                entry = value
                break
    if entry is None:
        entry = entries.get("primary") if isinstance(entries.get("primary"), dict) else None
    if not isinstance(entry, dict):
        return None
    categories = entry.get("categories") if isinstance(entry.get("categories"), dict) else entry
    if category and isinstance(categories, dict) and category in categories:
        current = categories[category]
        if isinstance(current, dict):
            current = current.get("right") or current.get("selected") or current.get("value")
        return current == right
    # Fall back: the requested token appears as the active right in this entry.
    blob = json.dumps(entry, sort_keys=True, default=str)
    present = right in blob
    previous = json.dumps(before.get("species_rights") or {}, sort_keys=True, default=str)
    if present and right not in previous:
        return True
    if present:
        return True
    if not present and _known(before.get("species_rights")):
        return False
    return None


def _ship_design(before, after, payload):
    ship_id = str(payload.get("ship_id", ""))
    design_id = str(payload.get("design_id", ""))
    if not ship_id or not design_id:
        return None
    ships = after.get("ships")
    if not isinstance(ships, dict):
        return None
    by_id = ships.get("by_id") if isinstance(ships.get("by_id"), dict) else {}
    ship = by_id.get(ship_id)
    if not isinstance(ship, dict):
        return None
    current = ship.get("design_id") or ship.get("ship_design") or ship.get("design")
    if current is None:
        return None
    return str(current) == design_id


def _war_declared(before, after, payload):
    target = str(payload.get("target_country_id", ""))
    if not target:
        return None
    wars = after.get("wars")
    if not _known(wars):
        return None
    by_id = wars.get("by_id") if isinstance(wars.get("by_id"), dict) else {}
    for war in by_id.values():
        if not isinstance(war, dict):
            continue
        sides = []
        for key in ("attackers", "defenders", "enemies", "participants"):
            value = war.get(key)
            if isinstance(value, list):
                sides.extend(str(item) for item in value)
            elif isinstance(value, dict):
                sides.extend(str(item) for item in value)
        if target in sides or target == str(war.get("defender") or "") or target == str(war.get("target") or ""):
            return True
    before_wars = before.get("wars") if _known(before.get("wars")) else None
    if before_wars is not None and len(by_id) > len((before_wars.get("by_id") or {})):
        return True
    if _known(before.get("wars")):
        return False
    return None


def _peace(before, after, payload):
    war_id = payload.get("war_id")
    target = str(payload.get("target_country_id", ""))
    wars = after.get("wars")
    if not _known(wars):
        return None
    by_id = wars.get("by_id") if isinstance(wars.get("by_id"), dict) else {}
    before_wars = before.get("wars") if _known(before.get("wars")) else {}
    before_ids = before_wars.get("by_id") if isinstance(before_wars.get("by_id"), dict) else {}
    if war_id is not None and str(war_id) in before_ids:
        return str(war_id) not in by_id
    if target:
        def _involves(collection, country):
            for war in (collection or {}).values():
                if not isinstance(war, dict):
                    continue
                blob = json.dumps(war, default=str)
                if country in blob:
                    return True
            return False
        if _involves(before_ids, target) and not _involves(by_id, target):
            return True
        if _involves(before_ids, target) and _involves(by_id, target):
            return False
    return None


def _policy(before, after, payload):
    policy = payload.get("policy")
    option = payload.get("option")
    controls = after.get("country_controls")
    if not _known(controls) or not policy:
        return None
    entry = (controls.get("policies") or {}).get(policy)
    if not isinstance(entry, dict):
        return False if _known(controls) else None
    return entry.get("selected") == option


def _edict_on(before, after, payload):
    edict = payload.get("edict")
    controls = after.get("country_controls")
    if not _known(controls) or not edict:
        return None
    return edict in (controls.get("edicts") or {})


def _edict_off(before, after, payload):
    edict = payload.get("edict")
    controls = after.get("country_controls")
    if not _known(controls) or not edict:
        return None
    return edict not in (controls.get("edicts") or {})


def _event_option(before, after, payload):
    player_event_id = str(payload.get("player_event_id") or payload.get("id") or "")
    events = after.get("pending_events")
    if not _known(events) or not player_event_id:
        return None
    by_id = events.get("by_id") if isinstance(events.get("by_id"), dict) else {}
    return player_event_id not in by_id


def _research(before, after, payload):
    tech = payload.get("technology") or payload.get("tech") or payload.get("id")
    area = payload.get("area")
    status = after.get("research_status")
    if not isinstance(status, dict) or not tech:
        return None
    areas = status.get("areas") if isinstance(status.get("areas"), dict) else {}
    if area and area in areas:
        current = areas[area].get("current") if isinstance(areas[area], dict) else None
        if isinstance(current, dict):
            current = current.get("id") or current.get("technology") or current.get("tech")
        return current == tech
    for item in areas.values():
        if not isinstance(item, dict):
            continue
        current = item.get("current")
        if isinstance(current, dict):
            current = current.get("id") or current.get("technology") or current.get("tech")
        if current == tech:
            return True
    return None


def _army(before, after, payload):
    planet_id = str(payload.get("planet_id", ""))
    queues = after.get("army_queues")
    if not isinstance(queues, dict):
        return None
    by_planet = queues.get("by_planet") or queues.get("by_id") or queues
    if not isinstance(by_planet, dict):
        return None
    if planet_id and planet_id in by_planet:
        entry = by_planet[planet_id]
        if isinstance(entry, dict) and entry.get("items"):
            return True
        if isinstance(entry, list) and entry:
            return True
        return None
    return None


_CHECKERS = {
    "add_system_claim": _claim,
    "set_species_right": _species_right,
    "set_ship_design": _ship_design,
    "declare_war": _war_declared,
    "offer_peace": _peace,
    "set_policy": _policy,
    "activate_edict": _edict_on,
    "deactivate_edict": _edict_off,
    "choose_event_option": _event_option,
    "select_research": _research,
    "recruit_army": _army,
}


def pending_reason() -> str:
    return ("Commands were accepted without a fresh dump. This snapshot may predate those writes. "
            "Call request_observation or session_brief to settle deferred_verification.")


def attach_pending_flag(observation: dict, journal: Journal) -> dict:
    rows = journal.accepted()
    if not rows:
        return observation
    observation["deferred_verification"] = {
        "pending": True,
        "verifications": [],
        "reason": pending_reason(),
    }
    return observation


def settle_accepted(observation: dict, journal: Journal) -> dict:
    rows = journal.accepted()
    if not rows:
        if "deferred_verification" not in observation:
            return observation
        return observation
    after = observation.get("state") if isinstance(observation.get("state"), dict) else None
    verifications = []
    for row in rows:
        action = row.get("action") or ""
        try:
            before = json.loads(row["before_json"])
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            before = None
        if after is None or not isinstance(before, dict):
            verdict, reason = None, "The later observation or saved before-state could not be read"
        else:
            verdict, reason = evaluate_effect(before, after, action)
        status = "verified" if verdict is True else "failed" if verdict is False else "unverified"
        journal.update(row["id"], status, reason)
        kind = action.split(":", 1)[0] if isinstance(action, str) else action
        verifications.append({
            "id": row["id"], "action": kind, "status": status, "reason": reason,
        })
    pending = any(item["status"] == "unverified" for item in verifications)
    observation["deferred_verification"] = {"pending": pending, "verifications": verifications}
    return observation


def journal_for(config_path: Path) -> Journal:
    from .direct_mcp import _configuration
    config = _configuration(Path(config_path))
    return Journal(config.state_dir / "journal.sqlite")
