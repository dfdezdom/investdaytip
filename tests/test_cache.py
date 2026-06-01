"""Tests for the SQLite cache layer — no yfinance calls."""
from __future__ import annotations

import time
from io import StringIO
from pathlib import Path

import pandas as pd
import pytest

from investdaytip.cache import (
    CacheDB,
    cache_history_get,
    cache_history_set,
    cache_info_get,
    cache_info_set,
    clear_cache,
)


@pytest.fixture
def tmp_db(tmp_path: Path) -> CacheDB:
    db = CacheDB(tmp_path / "test.db")
    yield db
    db.close()


class TestCacheDB:
    def test_set_and_get(self, tmp_db: CacheDB):
        tmp_db.set("k1", "hello", ttl=300)
        assert tmp_db.get("k1") == "hello"

    def test_get_missing(self, tmp_db: CacheDB):
        assert tmp_db.get("nonexistent") is None

    def test_get_expired(self, tmp_db: CacheDB):
        tmp_db.set("k1", "data", ttl=0)
        time.sleep(0.01)
        assert tmp_db.get("k1") is None

    def test_overwrite(self, tmp_db: CacheDB):
        tmp_db.set("k1", "old", ttl=300)
        tmp_db.set("k1", "new", ttl=300)
        assert tmp_db.get("k1") == "new"

    def test_clear(self, tmp_db: CacheDB):
        tmp_db.set("k1", "data", ttl=300)
        tmp_db.clear()
        assert tmp_db.get("k1") is None

    def test_multiple_keys(self, tmp_db: CacheDB):
        tmp_db.set("a", "1", ttl=300)
        tmp_db.set("b", "2", ttl=300)
        assert tmp_db.get("a") == "1"
        assert tmp_db.get("b") == "2"


class TestCacheHelpers:
    def test_info_roundtrip(self, enabled_temp_cache):
        info = {"shortName": "TestCo", "marketCap": 1e9, "quoteType": "STOCK"}
        cache_info_set("TEST", info)
        retrieved = cache_info_get("TEST")
        assert retrieved == info

    def test_history_roundtrip(self, enabled_temp_cache):
        df = pd.DataFrame({"Close": [100.5, 101.2, 102.8]})
        cache_history_set("TEST_HIST", df.to_json())
        raw = cache_history_get("TEST_HIST")
        assert raw is not None
        restored = pd.read_json(StringIO(raw))
        pd.testing.assert_frame_equal(restored, df)

    def test_disabled_returns_none(self):
        # disable_cache autouse fixture keeps caching off here.
        assert cache_info_get("ANY") is None
        assert cache_history_get("ANY") is None
        cache_info_set("ANY", {})
        cache_history_set("ANY", "{}")

    def test_clear_cache(self, enabled_temp_cache):
        cache_info_set("T", {"a": 1})
        assert cache_info_get("T") is not None
        clear_cache()
        assert cache_info_get("T") is None

    def test_close_all_closes_connections(self, tmp_path):
        db = CacheDB(tmp_path / "closeall.db")
        db.set("k", "v", ttl=300)
        assert db.get("k") == "v"
        db.close_all()
        # A fresh connection is lazily recreated on next access.
        assert db.get("k") == "v"
        db.close_all()
