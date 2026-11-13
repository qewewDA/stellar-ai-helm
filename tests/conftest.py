import pytest

from stellaris_autopilot.direct_observe import clear_observation_caches, reset_parse_stats


@pytest.fixture(autouse=True)
def isolated_observation_caches():
    """Process-wide snapshot caches must not leak parses between tests."""
    clear_observation_caches()
    reset_parse_stats()
    yield
    clear_observation_caches()
