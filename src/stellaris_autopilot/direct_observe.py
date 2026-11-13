"""Read immutable snapshots of the direct bridge's frequently rewritten dump."""
from __future__ import annotations

from collections import Counter
import copy
import hashlib
import os
from pathlib import Path
import re
import stat
import sys
import threading
import time

from .observe import Observation, _windows_change_time
from .pdx import PDXError, PDXNode, loads, loads_selected, without_cyclic_gc
from .saves import SaveError, identity_spec, normalize_gamestate, normalize_identity


_BRIDGE_FLAG = re.compile(r"qs_direct_(session|done|targets)_[0-9a-f]{32}\Z")
_CHANGED = "Direct dump changed during observation; wait for the next stable snapshot"


# Process-wide caches. Every key includes the SHA-256 of the exact immutable
# byte copy, so a hit returns what parsing those same bytes would return. Only
# the most recent snapshot is retained to bound memory (a full tree is roughly
# ten times the dump size).
_CACHE_LOCK = threading.Lock()
_STATE_CACHE: dict[tuple, dict] = {}       # (digest, country_id, resolved path) -> normalized state
_ROOT_CACHE: dict[str, PDXNode] = {}       # digest -> parsed tree, reused by checkpoint packaging
_STATS: Counter = Counter()


def parse_stats() -> dict:
    """Counts of full parses, targeted extractions and cache hits since the last reset."""
    with _CACHE_LOCK:
        return dict(_STATS)


def reset_parse_stats() -> None:
    with _CACHE_LOCK:
        _STATS.clear()


def clear_observation_caches() -> None:
    """Drop cached trees and states (tests, session close, memory pressure)."""
    with _CACHE_LOCK:
        _STATE_CACHE.clear()
        _ROOT_CACHE.clear()


def count(event: str) -> None:
    with _CACHE_LOCK:
        _STATS[event] += 1


def cached_root(digest: str) -> PDXNode | None:
    """The parsed tree for exactly these bytes, if the last full parse produced it."""
    with _CACHE_LOCK:
        root = _ROOT_CACHE.get(digest)
        if root is not None:
            _STATS["root_cache_hit"] += 1
        return root


def cached_state(digest: str, country_id: str, path: Path) -> dict | None:
    """A copy of the normalized state an observation produced from exactly these bytes."""
    key = (digest, str(country_id), str(Path(path).resolve()))
    with _CACHE_LOCK:
        state = _STATE_CACHE.get(key)
        if state is None:
            return None
        _STATS["state_cache_hit"] += 1
        return copy.deepcopy(state)


def parse_full(data: bytes, digest: str) -> PDXNode:
    """Full parse of exact bytes; decode/parse errors propagate unchanged."""
    root = cached_root(digest)
    if root is not None:
        return root
    root = loads(data.decode("utf-8-sig"))
    with _CACHE_LOCK:
        _STATS["full_parse"] += 1
        _ROOT_CACHE.clear()
        _ROOT_CACHE[digest] = root
    return root


def _flag_timestamp(value) -> bool:
    """Recognize observed permanent and timed country flag representations."""
    if isinstance(value, PDXNode):
        dates = value.get_all("flag_date")
        value = dates[0] if len(dates) == 1 else None
    return type(value) is int and value >= 0


def _bridge_receipts(root: PDXNode, country_id: str) -> dict:
    collections = root.get_all("country")
    countries = collections[0] if len(collections) == 1 else None
    selected = countries.get_all(country_id) if isinstance(countries, PDXNode) else []
    country = selected[0] if len(selected) == 1 else None
    blocks = country.get_all("flags") if isinstance(country, PDXNode) else []
    receipts: dict = {"session_flags": [], "done_flags": [], "target_flags": [], "receipt_only": True}
    # Repeated or non-object flag containers cannot establish an acknowledgement.
    if len(blocks) != 1 or not isinstance(blocks[0], PDXNode):
        return receipts
    flags = blocks[0]
    for name, value in flags.items():
        match = _BRIDGE_FLAG.fullmatch(name)
        if match and len(flags.get_all(name)) == 1 and _flag_timestamp(value):
            bucket = {"session": "session_flags", "done": "done_flags", "targets": "target_flags"}[match[1]]
            receipts[bucket].append(name)
    for bucket in ("session_flags", "done_flags", "target_flags"):
        receipts[bucket] = sorted(set(receipts[bucket]))
    return receipts


class DirectObserver:
    """Observe a complete UTF-8 gamestate dump without waiting through parsing.

    Only the byte-copy stage must see one file generation. Once copied, parsing
    and hashing use those same immutable bytes even if the next daily dump is
    already being written. Bridge flags acknowledge processing, never success
    of a gameplay action.
    """

    def __init__(self, path: Path, country_id: str | None = None, *, max_bytes: int = 128 * 1024 * 1024):
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        self.path, self.country_id, self.max_bytes = Path(path), country_id, max_bytes
        self._cached_signature: tuple | None = None
        self._cached: Observation | None = None

    @staticmethod
    def _signature(info: os.stat_result) -> tuple:
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns

    def _generation(self, info: os.stat_result, stream) -> tuple:
        signature = self._signature(info)
        return (*signature, _windows_change_time(stream)) if sys.platform == "win32" else signature

    def _validate_file(self, info: os.stat_result) -> None:
        if not stat.S_ISREG(info.st_mode):
            raise SaveError("Direct dump must be a regular file")
        if info.st_size > self.max_bytes:
            raise SaveError(f"Direct dump exceeds the {self.max_bytes}-byte input limit")
        if info.st_mtime > time.time() + 5:
            raise SaveError("Direct dump modification time is in the future")

    def _copy_snapshot(self) -> tuple[bytes, tuple, float]:
        try:
            initial = self.path.stat()
            self._validate_file(initial)
            with self.path.open("rb") as stream:
                before = os.fstat(stream.fileno())
                self._validate_file(before)
                # Windows path stat() and fstat() may report different ctime
                # semantics. Compare that field only between open handles;
                # _generation also reads the real Windows change time.
                if self._signature(initial)[:4] != self._signature(before)[:4]:
                    raise SaveError(_CHANGED)
                generation = self._generation(before, stream)
                data = stream.read(self.max_bytes + 1)
                if len(data) > self.max_bytes:
                    raise SaveError(f"Direct dump exceeds the {self.max_bytes}-byte input limit")
                if len(data) != before.st_size or self._generation(os.fstat(stream.fileno()), stream) != generation:
                    raise SaveError(_CHANGED)
                # Also reject replacement of the path while the original file
                # handle stayed open. No generation checks happen after this stage.
                with self.path.open("rb") as probe:
                    after = os.fstat(probe.fileno())
                    if self._generation(after, probe) != generation:
                        raise SaveError(_CHANGED)
            return data, generation, before.st_mtime
        except SaveError:
            raise
        except OSError as exc:
            raise SaveError(f"Cannot read direct dump {self.path.name}: {exc}") from exc

    def is_stale(self, not_before: float) -> bool:
        """True only when stat() proves the current dump predates ``not_before``.

        Lets polling loops skip copying and parsing a generation that can never
        satisfy a freshness requirement. Unreadable files return False so the
        caller's normal read path reports them.
        """
        try:
            return self.path.stat().st_mtime < not_before
        except OSError:
            return False

    def read(self) -> Observation:
        data, generation, modified_at = self._copy_snapshot()
        signature = (*generation, self.country_id)
        if signature == self._cached_signature and self._cached is not None:
            count("generation_cache_hit")
            return Observation(copy.deepcopy(self._cached.state), self._cached.digest, self._cached.modified_at)
        digest = hashlib.sha256(data).hexdigest()
        key = (digest, self.country_id, str(self.path.resolve()))
        with _CACHE_LOCK:
            shared = _STATE_CACHE.get(key)
            if shared is not None:
                _STATS["state_cache_hit"] += 1
                state = copy.deepcopy(shared)
        if shared is None:
            try:
                root = parse_full(data, digest)
            except (UnicodeError, PDXError) as exc:
                raise SaveError(f"Cannot read textual direct dump {self.path.name}: {exc}") from exc
            with without_cyclic_gc():
                state = normalize_gamestate(root, self.path, country_id=self.country_id)
                state["direct_bridge"] = _bridge_receipts(root, state["country_id"])
            with _CACHE_LOCK:
                _STATE_CACHE.clear()
                _STATE_CACHE[key] = copy.deepcopy(state)
        observation = Observation(state, digest, modified_at)
        self._cached_signature = signature
        self._cached = Observation(copy.deepcopy(state), observation.digest, observation.modified_at)
        return observation

    def read_identity(self) -> Observation:
        """Targeted read of only what connection validation inspects.

        Uses the same stable byte copy, digest and modification time as
        :meth:`read`; the state holds ``date``, ``country_id``, the identity
        metadata and ``direct_bridge`` receipts, identical to a full read of the
        same bytes. Callers needing planets, fleets or economy use ``read``.
        """
        data, _, modified_at = self._copy_snapshot()
        if self.country_id is None:
            raise ValueError("A targeted identity read requires an explicit country_id")
        digest = hashlib.sha256(data).hexdigest()
        try:
            root = loads_selected(data.decode("utf-8-sig"), identity_spec(self.country_id))
        except (UnicodeError, PDXError) as exc:
            raise SaveError(f"Cannot read textual direct dump {self.path.name}: {exc}") from exc
        state = normalize_identity(root, self.path, country_id=self.country_id)
        state["direct_bridge"] = _bridge_receipts(root, state["country_id"])
        count("targeted_parse")
        return Observation(state, digest, modified_at)
