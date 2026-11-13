"""Pending event popups and the normal select-event-option command.

Pinned to the 4.5.2 build. These helpers read snapshots and game files,
prepare typed requests and verify outcomes; they never attach to a process.

Static evidence (4.5.2):
* The game state writer (0x2460d0, at 0x247c92) writes every open player
  event (game state +0x180, count +0x18c) as a top-level ``player_event``
  block, then ``open_player_event_selection_history`` (entries written by
  0x9032c0: ``player_event``, ``human``, ``option``). A live 4.5.2 dump shows
  the open event block as ``id``, ``event``, ``date``, ``scope``, ``country``;
  ``id`` (runtime +0x180) is the command's +0x28 field and ``country`` is
  runtime +0x184. (0x8da160 / vtable 0x233a000 writes a different,
  similarly named structure and is not used.)
* The event window option button (0x1589820) and the open-event chooser
  (0x8e2270) build ``select_event_option`` (vtable 0x23abdd8, ctor 0x99bfb0,
  validate 0x99cd60, execute 0x99c090, writer 0x99cbf0: ``event``,
  ``option``, ``scope``, ``id``, ``human``). The validator checks that the
  option is allowed in the event scope; execute removes the open event and
  runs the option's effects.
* Every open event needs an option; the window has no dismiss path besides
  an option, so each pending event is reported as requiring a choice.

Option indexes follow the ``option`` blocks of the event definition; that
matches the native option vector order only by static reading (unproven
until a live test). Text comes from game files and localisation and is
reference data: conditional titles, descriptions and options are reported
as conditional instead of being guessed.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import threading
from pathlib import Path

from .pdx import PDXError, PDXNode, loads
from .ship_commands import _handle, _id, _one


KINDS = frozenset({"choose_event_option"})
MAX_OPTIONS = 64
_EVENT_ID = re.compile(r"[A-Za-z0-9_.-]{1,96}\Z", re.ASCII)
_DATE = re.compile(r"[0-9]{4}\.[0-9]{2}\.[0-9]{2}\Z", re.ASCII)
_EVENT_TYPES = ("country_event", "planet_event", "fleet_event", "ship_event", "pop_event", "pop_faction_event",
                "observer_event", "leader_event", "astral_rift_event", "first_contact_event",
                "situation_event", "espionage_event", "archaeology_event", "event")
_DEFINITION_ID = re.compile(r"^\s*id\s*=\s*\"?([A-Za-z0-9_.-]{1,96})\"?", re.ASCII | re.MULTILINE)
_LOCALISATION = re.compile(r'^\s*([A-Za-z0-9_.\-]+):\d*\s*"(.*)"\s*(?:#.*)?$')
_REFERENCE = re.compile(r"\$([A-Za-z0-9_.\-]+)\$")
_FORMAT = re.compile(r"\u00a7[A-Za-z!]|\u00a3[A-Za-z0-9_]+\u00a3?")
_TEXT_LIMIT = 4000


def _scope(node) -> dict | None:
    if not isinstance(node, PDXNode):
        return None
    kind, identifier = _one(node, "type"), _handle(_one(node, "id"))
    return {"type": kind if isinstance(kind, str) else None, "id": identifier}


def _selections(history) -> list[dict] | None:
    """``open_player_event_selection_history = { selected = { {player_event human option} ... } }``."""
    if not isinstance(history, PDXNode):
        return None
    selected = history.get_all("selected")
    if not selected:
        return [] if not history.entries else None
    if len(selected) != 1 or not isinstance(selected[0], PDXNode):
        return None
    result = []
    for key, entry in selected[0].entries:
        if key is not None or not isinstance(entry, PDXNode):
            return None
        values = {name: _one(entry, name) for name in ("player_event", "human", "option")}
        if any(type(value) is not int for value in values.values()):
            return None
        result.append({"player_event": str(values["player_event"]), "human": values["human"],
                       "option": values["option"]})
    return result


def observe_pending_events(root: PDXNode) -> dict:
    """Open player events from the game state writer's top-level ``player_event`` blocks.

    Live 4.5.2 dump (2207.01.11): ``player_event = { id=36 event="first_contact.1"
    date="2207.05.07" scope={...} country=0 }``. ``date`` is passed through
    unchanged; its meaning is unproven (an AI empire's event dated 2209.12.16
    was resolved by 2207.10.07). The history records game/AI choices as
    ``human=-1`` and a choice made through this command as ``human=1``.
    """
    result = {"known": False, "by_id": {}, "selections": []}
    if not isinstance(root, PDXNode) or len(root.get_all("open_player_event_selection_history")) != 1:
        return result
    found = {}
    for node in root.get_all("player_event"):
        if not isinstance(node, PDXNode):
            return result
        identifier = _handle(_one(node, "id"))
        event_id = _one(node, "event")
        country = _handle(_one(node, "country"))
        date = _one(node, "date")
        if (identifier is None or identifier in found or not isinstance(event_id, str)
                or not _EVENT_ID.fullmatch(event_id) or country is None
                or (date is not None and (not isinstance(date, str) or not _DATE.fullmatch(date)))):
            return result
        found[identifier] = {"id": identifier, "event_id": event_id, "country": country,
                             "date": date, "scope": _scope(_one(node, "scope"))}
    selections = _selections(_one(root, "open_player_event_selection_history"))
    if selections is None:
        return result
    return {"known": True, "by_id": found, "selections": selections}


def _clean(text: str) -> str:
    text = re.sub(r"[ \t]{2,}", " ", _FORMAT.sub("", text).replace("\\n", "\n"))
    return "\n".join(line.strip() for line in text.split("\n")).strip()[:_TEXT_LIMIT]


class EventCatalog:
    """Lazy index of event definitions and localisation for one language."""

    def __init__(self, game_dir: Path, language: str = "english"):
        if not re.fullmatch(r"[a-z_]{2,24}", language, re.ASCII):
            raise ValueError("Unsupported localisation language")
        self.game_dir, self.language = Path(game_dir), language
        self._lock = threading.Lock()
        self._files: dict[str, Path] | None = None
        self._parsed: dict[Path, dict[str, PDXNode]] = {}
        self._labels: dict[str, str] | None = None

    @classmethod
    def for_user_dir(cls, game_dir: Path, user_dir: Path | None) -> EventCatalog:
        language = "english"
        try:
            settings = (Path(user_dir) / "settings.txt").read_text(encoding="utf-8-sig") if user_dir else ""
            match = re.search(r'^\s*language\s*=\s*"?l_([a-z_]{2,24})"?', settings, re.MULTILINE)
            if match and (Path(game_dir) / "localisation" / match.group(1)).is_dir():
                language = match.group(1)
        except OSError:
            pass
        return cls(game_dir, language)

    def _index(self) -> dict[str, Path]:
        if self._files is None:
            files = {}
            for path in sorted((self.game_dir / "events").glob("*.txt")):
                try:
                    text = path.read_text(encoding="utf-8-sig", errors="replace")
                except OSError:
                    continue
                for match in _DEFINITION_ID.finditer(text):
                    files.setdefault(match.group(1), path)
            self._files = files
        return self._files

    def _definition(self, event_id: str) -> PDXNode | None:
        path = self._index().get(event_id)
        if path is None:
            return None
        if path not in self._parsed:
            events = {}
            try:
                root = loads(path.read_text(encoding="utf-8-sig", errors="replace"))
            except (PDXError, OSError, ValueError):
                root = None
            for key, value in (root.entries if isinstance(root, PDXNode) else []):
                if key in _EVENT_TYPES and isinstance(value, PDXNode):
                    identifier = _one(value, "id")
                    if isinstance(identifier, str):
                        events.setdefault(identifier, (key, value))
            self._parsed[path] = events
        return self._parsed[path].get(event_id)

    def _localisation(self) -> dict[str, str]:
        if self._labels is None:
            labels = {}
            folder = self.game_dir / "localisation" / self.language
            fallback = self.game_dir / "localisation" / "english"
            for directory in ([fallback] if fallback != folder else []) + [folder]:
                for path in sorted(directory.rglob("*.yml")) if directory.is_dir() else []:
                    try:
                        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
                    except OSError:
                        continue
                    for line in lines:
                        match = _LOCALISATION.match(line)
                        if match:
                            labels[match.group(1)] = match.group(2)
            self._labels = labels
        return self._labels

    def text(self, key) -> dict:
        """Resolve one localisation key; unknown keys stay visible as keys."""
        if not isinstance(key, str):
            return {"key": None, "text": None, "conditional": isinstance(key, PDXNode)}
        labels = self._localisation()
        value = labels.get(key)
        if value is None:
            return {"key": key, "text": None, "conditional": False}
        for _ in range(3):
            value = _REFERENCE.sub(lambda match: labels.get(match.group(1), match.group(0)), value)
        return {"key": key, "text": _clean(value), "conditional": False}

    def _localised(self, value) -> dict:
        if isinstance(value, PDXNode):
            text = _one(value, "text")
            resolved = self.text(text) if isinstance(text, str) else {"key": None, "text": None}
            return {**resolved, "conditional": _one(value, "trigger") is not None}
        return self.text(value)

    def describe(self, event_id: str) -> dict:
        with self._lock:
            found = self._definition(event_id) if _EVENT_ID.fullmatch(event_id or "") else None
            if found is None:
                return {"event_id": event_id, "definition_found": False, "language": self.language}
            kind, node = found
            descs = node.get_all("desc")
            options = []
            for index, option in enumerate(node.get_all("option")):
                if not isinstance(option, PDXNode):
                    continue
                tooltips = [self._localised(value) for value in option.get_all("custom_tooltip")]
                options.append({
                    "index": index, "name": self._localised(_one(option, "name")),
                    "custom_tooltips": tooltips,
                    "conditional": _one(option, "trigger") is not None,
                    "has_allow": _one(option, "allow") is not None,
                    "default_hide_option": _one(option, "default_hide_option") == "yes",
                    "tag": _one(option, "tag") if isinstance(_one(option, "tag"), str) else None,
                    "effect_keys": sorted({key for key, _ in option.entries if key not in {
                        "name", "custom_tooltip", "trigger", "allow", "ai_chance", "default_hide_option", "tag"}}),
                })
            flags = {key: _one(node, key) == "yes" for key in (
                "hide_window", "is_triggered_only", "diplomatic", "auto_opens", "force_open", "fire_only_once")}
            return {"event_id": event_id, "definition_found": True, "event_type": kind, "language": self.language,
                    "title": self._localised(_one(node, "title")),
                    "descriptions": [self._localised(value) for value in descs],
                    "picture": _one(node, "picture") if isinstance(_one(node, "picture"), str) else None,
                    "flags": flags, "options": options, "requires_option": True,
                    "note": "Option indexes follow the definition order; conditional options may be hidden in game."}


def describe_pending_events(state: dict, catalog: EventCatalog | None) -> dict:
    pending = state.get("pending_events") if isinstance(state, dict) else None
    if not isinstance(pending, dict) or pending.get("known") is not True:
        return {"known": False, "events": [], "reason": "Pending events are not exactly observable in this snapshot"}
    country = str(state.get("country_id", "0"))
    events = []
    others = 0
    for identifier, entry in sorted(pending["by_id"].items(), key=lambda item: int(item[0])):
        # The save also lists events open for AI empires (live: country=16777222); only ours are choosable.
        if entry["country"] != country:
            others += 1
            continue
        detail = catalog.describe(entry["event_id"]) if catalog is not None else {"definition_found": False}
        events.append({**entry, "player_event_id": identifier, "definition": detail})
    return {"known": True, "date": state.get("date"), "country_id": country, "events": events,
            "other_country_event_count": others}


@dataclass(frozen=True)
class EventRequest:
    kind: str
    player_event_id: str
    option_index: str

    def to_dict(self) -> dict:
        return {"player_event_id": self.player_event_id, "option_index": self.option_index}


def event_request(kind: str, *, player_event_id, option_index) -> EventRequest:
    if kind != "choose_event_option":
        raise ValueError("Unsupported event command")
    if type(option_index) is int:
        option_index = str(option_index)
    if not isinstance(option_index, str) or not re.fullmatch(r"0|[1-9][0-9]?", option_index, re.ASCII) \
            or int(option_index) >= MAX_OPTIONS:
        raise ValueError("Option index must be an integer from 0 to 63")
    identifier = _id(player_event_id)
    if int(identifier) > 0x7FFFFFFF:
        raise ValueError("Open player event id is out of range")
    return EventRequest(kind, identifier, option_index)


@dataclass(frozen=True)
class ResolvedEventCommand:
    request: EventRequest
    event_id: str

    @property
    def kind(self) -> str:
        return self.request.kind

    def to_dict(self) -> dict:
        return {**self.request.to_dict(), "event_id": self.event_id}


def _again(request: EventRequest) -> EventRequest:
    if type(request) is not EventRequest:
        raise ValueError("A typed event request is required")
    return event_request(request.kind, **request.to_dict())


def _pending(state: dict) -> dict:
    if state.get("country_id") != "0":
        raise ValueError("Event options have only been verified for country 0")
    pending = state.get("pending_events")
    if not isinstance(pending, dict) or pending.get("known") is not True or not isinstance(pending.get("by_id"), dict):
        raise ValueError("Pending events are not exactly observable in this snapshot")
    return pending["by_id"]


def resolve_event_command(state: dict, request: EventRequest) -> ResolvedEventCommand:
    request = _again(request)
    entry = _pending(state).get(request.player_event_id)
    if not isinstance(entry, dict) or not _EVENT_ID.fullmatch(entry.get("event_id") or ""):
        raise ValueError("Select an observed pending event by its player_event_id")
    # Only the local player's events are ours; other empires' events stay with the game's own AI.
    if entry.get("country") != "0" or str(state.get("country_id")) != "0":
        raise ValueError("The open event belongs to another country; its choice is left to the game AI")
    return ResolvedEventCommand(request, entry["event_id"])


def native_event_payload(command: ResolvedEventCommand, token: str) -> dict:
    if type(command) is not ResolvedEventCommand:
        raise ValueError("A resolved event command is required")
    request = _again(command.request)
    if not isinstance(command.event_id, str) or not _EVENT_ID.fullmatch(command.event_id):
        raise ValueError("Unexpected resolved event id")
    return {"kind": request.kind, "country_id": "0", "token": token, **request.to_dict(),
            "event_id": command.event_id}


def verify_event_command(before: dict, after: dict, command: ResolvedEventCommand) -> bool:
    """The chosen open event is gone on the same paused day; others are untouched.

    Option effects are arbitrary script, so they are reported, not required:
    new open events (follow-ups) may appear, but every other event that was
    already pending must still be pending unchanged.
    """
    if type(command) is not ResolvedEventCommand:
        return False
    try:
        request = _again(command.request)
        if (not isinstance(before.get("date"), str) or _DATE.fullmatch(before["date"]) is None
                or after.get("date") != before["date"] or after.get("country_id") != "0"
                or before.get("metadata", {}).get("campaign_id") != after.get("metadata", {}).get("campaign_id")):
            return False
        first, second = _pending(before), _pending(after)
        chosen = first.get(request.player_event_id)
        if not isinstance(chosen, dict) or chosen.get("event_id") != command.event_id:
            return False
        if request.player_event_id in second:
            return False
        if any(second.get(key) != value for key, value in first.items() if key != request.player_event_id):
            return False
        # The history may record this choice; it must never record another option.
        history_before, history_after = before["pending_events"]["selections"], after["pending_events"]["selections"]
        if history_after[:len(history_before)] != history_before:
            return False
        recorded = [entry for entry in history_after[len(history_before):]
                    if entry.get("player_event") == request.player_event_id]
        return all(entry.get("option") == int(request.option_index) for entry in recorded)
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def observed_effects(before: dict, after: dict) -> dict:
    """Report what changed after an option, for the caller to judge."""
    resources = {}
    first, second = before.get("resources") or {}, after.get("resources") or {}
    for key in sorted(set(first) | set(second)):
        if first.get(key) != second.get(key):
            resources[key] = {"before": first.get(key), "after": second.get(key)}
    old, new = before.get("pending_events", {}).get("by_id", {}), after.get("pending_events", {}).get("by_id", {})
    changed = sorted(key for key in set(before) | set(after)
                     if key not in {"resources", "pending_events", "_native_operation", "_direct_operation",
                                    "direct_bridge", "metadata"} and before.get(key) != after.get(key))
    history = [entry for entry in after.get("pending_events", {}).get("selections", [])[
        len(before.get("pending_events", {}).get("selections", [])):]]
    return {"resources": resources, "new_selections": history,
            "new_pending_events": sorted(set(new) - set(old), key=int),
            "removed_pending_events": sorted(set(old) - set(new), key=int), "changed_sections": changed}
