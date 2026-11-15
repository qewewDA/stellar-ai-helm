"""Fast (default) vs strict postcondition verification for write tools."""
from __future__ import annotations

from contextvars import ContextVar
import os

# None: follow STELLARIS_AUTOPILOT_VERIFY; True: strict dumps; False: fast accept.
_VERIFY: ContextVar[bool | None] = ContextVar("stellaris_autopilot_verify", default=None)

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def use_verification(value: bool | None):
    """Set the per-call override and return a reset token."""
    return _VERIFY.set(value)


def reset_verification(token) -> None:
    _VERIFY.reset(token)


def verification_required(explicit: bool | None = None) -> bool:
    """True when this write should take the old per-op dump path.

    An explicit True/False wins. Otherwise the contextvar (MCP ``verify=`` /
    execute_plan) wins. Otherwise STELLARIS_AUTOPILOT_VERIFY=1|true|yes|on
    selects the strict path. The default, with none of those set, is fast.
    """
    if explicit is not None:
        return bool(explicit)
    current = _VERIFY.get()
    if current is not None:
        return bool(current)
    return os.environ.get("STELLARIS_AUTOPILOT_VERIFY", "").strip().lower() in _TRUTHY
