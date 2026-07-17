"""Tests for the SQLite cache layer — no yfinance calls."""
from __future__ import annotations

import time
from io import StringIO
from pathlib import Path

import pandas as pd
import pytest

from investdaytip.cache import (
    CacheDB,
    cache_fmp_info_get,
    cache_fmp_info_set,
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

    def test_fmp_info_uses_isolated_cache_key(self, enabled_temp_cache):
        """FMP's {"profile", "ratios_ttm"} schema must not poison the shared
        yfinance-style ``{ticker}:info`` entry (and vice versa)."""
        fmp_info = {"profile": {"companyName": "Apple"}, "ratios_ttm": {"peTTM": 30.0}}
        cache_fmp_info_set("AAPL", fmp_info)
        assert cache_fmp_info_get("AAPL") == fmp_info
        assert cache_info_get("AAPL") is None

        yf_info = {"quoteType": "EQUITY", "trailingPE": 30.0}
        cache_info_set("MSFT", yf_info)
        assert cache_info_get("MSFT") == yf_info
        assert cache_fmp_info_get("MSFT") is None

    def test_fmp_info_roundtrip_and_disabled(self, enabled_temp_cache):
        cache_fmp_info_set("TEST", {"profile": {"x": 1}})
        assert cache_fmp_info_get("TEST") == {"profile": {"x": 1}}
        # Corrupt JSON tolerated
        from investdaytip.cache import _cache_key, get_db

        get_db().set(_cache_key("BROKEN", "fmp_info"), "{not json", ttl=300)
        assert cache_fmp_info_get("BROKEN") is None

    def test_close_all_closes_connections(self, tmp_path):
        db = CacheDB(tmp_path / "closeall.db")
        db.set("k", "v", ttl=300)
        assert db.get("k") == "v"
        db.close_all()
        # close_all() drops the calling thread's connection and clears the
        # tracked list but does NOT prevent future access (a fresh connection
        # is lazily recreated on the next _connect() call).
        assert db.get("k") == "v"
        db.close_all()


# =========================================================================
# Financial statement cache round-trip
# =========================================================================


class TestFinancialCache:
    def test_dataframe_round_trip(self):
        df = pd.DataFrame(
            {"Net Income": [100, 80], "Total Revenue": [1000, 850]},
            index=pd.DatetimeIndex(["2024-12-31", "2023-12-31"]),
        )
        json_str = df.to_json(date_format="iso")
        restored = pd.read_json(StringIO(json_str))
        assert df.equals(restored)

    def test_dividends_series_round_trip(self):
        s = pd.Series(
            [0.25, 0.25, 0.25],
            index=pd.DatetimeIndex(["2024-09-01", "2024-06-01", "2024-03-01"]),
        )
        json_str = s.to_json(date_format="iso")
        restored = pd.read_json(StringIO(json_str), typ="series")
        assert s.equals(restored)

    def test_financial_set_get(self, enabled_temp_cache):
        from investdaytip.cache import cache_financial_get, cache_financial_set

        df = pd.DataFrame(
            {"Net Income": [100]},
            index=pd.DatetimeIndex(["2024-12-31"]),
        )
        cache_financial_set("TEST", "income_stmt", df.to_json(date_format="iso"))
        raw = cache_financial_get("TEST", "income_stmt")
        assert raw is not None
        restored = pd.read_json(StringIO(raw))
        assert df.equals(restored)

    def test_dividends_set_get(self, enabled_temp_cache):
        from investdaytip.cache import cache_dividends_get, cache_dividends_set

        s = pd.Series([0.25], index=pd.DatetimeIndex(["2024-12-01"]))
        cache_dividends_set("TEST", s.to_json(date_format="iso"))
        raw = cache_dividends_get("TEST")
        assert raw is not None
        restored = pd.read_json(StringIO(raw), typ="series")
        assert s.equals(restored)

    def test_financial_missing_returns_none(self):
        from investdaytip.cache import cache_financial_get

        assert cache_financial_get("NONEXISTENT", "income_stmt") is None
