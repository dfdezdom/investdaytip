"""Global test fixtures — disable caching so tests remain pure."""
import pytest
from investdaytip.cache import set_enabled


@pytest.fixture(autouse=True)
def disable_cache():
    set_enabled(False)
    yield
    set_enabled(True)
