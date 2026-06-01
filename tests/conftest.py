"""Global test fixtures.

- Caching is disabled by default so tests stay pure.
- A network guard fails fast if any test accidentally reaches yfinance.
"""
from __future__ import annotations

import pytest

import investdaytip.cache as cache_module
from investdaytip.cache import set_enabled


@pytest.fixture(autouse=True)
def disable_cache():
    set_enabled(False)
    try:
        yield
    finally:
        set_enabled(True)


@pytest.fixture(autouse=True)
def no_network(monkeypatch, request):
    """Fail fast if a test instantiates ``yf.Ticker`` without mocking it.

    Tests that legitimately patch ``yf.Ticker`` override this in their own
    scope, so the guard only fires on genuinely unmocked network access.
    Disabled for tests marked ``allow_network`` (none currently).
    """
    if request.node.get_closest_marker("allow_network"):
        return

    def _blocked(*args, **kwargs):  # pragma: no cover - only hit on misuse
        raise RuntimeError(
            "Unexpected yfinance network access in a test. "
            "Mock investdaytip.<module>.yf.Ticker instead."
        )

    import yfinance as yf

    monkeypatch.setattr(yf, "Ticker", _blocked)


@pytest.fixture
def enabled_temp_cache(tmp_path, monkeypatch):
    """Enable caching backed by a throwaway temp-dir DB (no real DB writes)."""
    from investdaytip.cache import CacheDB

    db = CacheDB(tmp_path / "cache.db")
    monkeypatch.setattr(cache_module, "_db", db)
    set_enabled(True)
    try:
        yield db
    finally:
        set_enabled(False)
        db.close_all()
