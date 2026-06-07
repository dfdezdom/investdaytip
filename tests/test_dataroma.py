"""Tests for DataRoma superinvestor holdings integration.

These mock the DataRoma fetch pipeline to verify normalization logic
(e.g. GOOG -> GOOGL merge) without hitting the network.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from investdaytip.dataroma import (
    CACHE_KEY,
    TTL_SUPERINVESTOR,
    fetch_superinvestor_universe,
    get_superinvestor_data,
)


# ---------------------------------------------------------------------------
# GOOG -> GOOGL merge
# ---------------------------------------------------------------------------


def _make_mock_db(aggregate: dict, weights: dict, manager_count: int = 10) -> MagicMock:
    """Return a mock cache DB that stores the given superinvestor data."""
    db = MagicMock()
    data = {
        "aggregate": aggregate,
        "weights": weights,
        "manager_count": manager_count,
    }
    db.get.return_value = json.dumps(data)
    return db


class TestGoogMerge:
    """GOOG holdings should be merged into GOOGL so Alphabet is not double-counted."""

    def test_goog_merged_into_googl(self):
        """When GOOG and GOOGL both appear, GOOG is merged into GOOGL."""
        aggregate = {"GOOGL": 12, "AAPL": 10}
        weights = {"GOOGL": 60.0, "AAPL": 50.0}
        db = _make_mock_db(aggregate, weights)

        with patch("investdaytip.dataroma.get_db", return_value=db):
            tickers = fetch_superinvestor_universe(min_overlap=1)

        assert "GOOGL" in tickers
        assert "GOOG" not in tickers
        # GOOGL count should be 12 (already merged in cache v2)
        assert tickers.index("GOOGL") < tickers.index("AAPL")

    def test_goog_only_also_merged(self):
        """When only GOOG appears (no GOOGL), it is renamed to GOOGL."""
        aggregate = {"GOOG": 12, "AAPL": 10}
        weights = {"GOOG": 60.0, "AAPL": 50.0}
        db = _make_mock_db(aggregate, weights)

        with patch("investdaytip.dataroma.get_db", return_value=db):
            tickers = fetch_superinvestor_universe(min_overlap=1)

        assert "GOOGL" in tickers
        assert "GOOG" not in tickers

    def test_googl_only_unchanged(self):
        """When only GOOGL appears, nothing is merged."""
        aggregate = {"GOOGL": 7, "AAPL": 10}
        weights = {"GOOGL": 35.0, "AAPL": 50.0}
        db = _make_mock_db(aggregate, weights)

        with patch("investdaytip.dataroma.get_db", return_value=db):
            tickers = fetch_superinvestor_universe(min_overlap=1)

        assert "GOOGL" in tickers
        assert "GOOG" not in tickers

    def test_superinvestor_data_reflects_merge(self):
        """get_superinvestor_data() should report merged counts."""
        aggregate = {"GOOGL": 8, "AAPL": 10}
        weights = {"GOOGL": 40.0, "AAPL": 50.0}
        db = _make_mock_db(aggregate, weights)

        with patch("investdaytip.dataroma.get_db", return_value=db):
            data = get_superinvestor_data()

        assert "GOOGL" in data
        assert "GOOG" not in data
        # 8 managers, 40.0 total weight
        assert data["GOOGL"]["manager_count"] == 8
        assert data["GOOGL"]["total_weight"] == 40.0

    def test_goog_merge_preserves_min_overlap_filter(self):
        """After merge, GOOGL must still pass the min_overlap threshold."""
        aggregate = {"GOOGL": 3, "AAPL": 10}
        weights = {"GOOGL": 15.0, "AAPL": 50.0}
        db = _make_mock_db(aggregate, weights)

        with patch("investdaytip.dataroma.get_db", return_value=db):
            # min_overlap=4: GOOGL (3) should be excluded, AAPL (10) included
            tickers = fetch_superinvestor_universe(min_overlap=4)

        assert "GOOGL" not in tickers
        assert "AAPL" in tickers


# ---------------------------------------------------------------------------
# General fetch_superinvestor_universe behavior
# ---------------------------------------------------------------------------


class TestFetchSuperinvestorUniverse:
    """Edge cases and filtering logic."""

    def test_empty_cache_triggers_fetch(self):
        """When cache is empty, fetch from DataRoma and merge GOOG->GOOGL."""
        db = MagicMock()
        db.get.return_value = None
        # Simulate fetch result with GOOG and GOOGL
        fetched_data = {
            "aggregate": {"GOOGL": 5, "GOOG": 3, "AAPL": 10},
            "weights": {"GOOGL": 25.0, "GOOG": 15.0, "AAPL": 50.0},
            "manager_count": 10,
        }
        db.get.return_value = json.dumps(fetched_data)

        with patch("investdaytip.dataroma.get_db", return_value=db):
            tickers = fetch_superinvestor_universe(min_overlap=1)

        assert "GOOGL" in tickers
        assert "GOOG" not in tickers

    def test_min_overlap_filter(self):
        """Only tickers with count >= min_overlap are returned."""
        aggregate = {"AAPL": 10, "MSFT": 3, "TSLA": 1}
        weights = {"AAPL": 50.0, "MSFT": 15.0, "TSLA": 5.0}
        db = _make_mock_db(aggregate, weights)

        with patch("investdaytip.dataroma.get_db", return_value=db):
            tickers = fetch_superinvestor_universe(min_overlap=2)

        assert "AAPL" in tickers
        assert "MSFT" in tickers
        assert "TSLA" not in tickers

    def test_sorting_by_count_then_weight(self):
        """Tickers are sorted by manager count desc, then weight desc."""
        aggregate = {"MSFT": 8, "AAPL": 8, "TSLA": 5}
        weights = {"MSFT": 40.0, "AAPL": 35.0, "TSLA": 25.0}
        db = _make_mock_db(aggregate, weights)

        with patch("investdaytip.dataroma.get_db", return_value=db):
            tickers = fetch_superinvestor_universe(min_overlap=1)

        assert tickers.index("MSFT") < tickers.index("AAPL")
        assert tickers.index("AAPL") < tickers.index("TSLA")

    def test_malformed_tickers_filtered(self):
        """Tickers with invalid characters are dropped."""
        aggregate = {"AAPL": 10, "BAD TICKER": 5, "MSFT": 3}
        weights = {"AAPL": 50.0, "BAD TICKER": 25.0, "MSFT": 15.0}
        db = _make_mock_db(aggregate, weights)

        with patch("investdaytip.dataroma.get_db", return_value=db):
            tickers = fetch_superinvestor_universe(min_overlap=1)

        assert "AAPL" in tickers
        assert "MSFT" in tickers
        assert "BAD TICKER" not in tickers
