import pytest

from stellaris_autopilot.direct_observe import clear_observation_caches, reset_parse_stats


@pytest.fixture(autouse=True)
def isolated_observation_caches():
    """Process-wide snapshot caches must not leak parses between tests."""
    clear_observation_caches()
    reset_parse_stats()
    yield
    clear_observation_caches()


@pytest.fixture(autouse=True)
def default_strict_verification(monkeypatch):
    """Existing tests expect the per-op dump path; production default remains fast."""
    monkeypatch.setenv("STELLARIS_AUTOPILOT_VERIFY", "1")
