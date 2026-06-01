"""Tests for data_source — yfinance mocking only, no live network."""

from unittest.mock import MagicMock

import pandas as pd
import pytest
from yfinance.exceptions import YFRateLimitError

from investdaytip.data_source import StockData, fetch_asset


def _mock_ticker(info: dict, history: pd.DataFrame | None = None) -> MagicMock:
    """Build a mock yf.Ticker returning the given info dict and history."""
    mock = MagicMock()
    mock.info = info
    mock.history.return_value = history if history is not None else pd.DataFrame({"Close": [100] * 300})
    return mock


@pytest.fixture
def stock_info() -> dict:
    return {
        "quoteType": "STOCK",
        "marketCap": 10_000_000_000,
        "shortName": "BigCo",
        "longName": "Big Corp",
        "currency": "USD",
        "exchange": "NMS",
        "sector": "Technology",
    }


def test_fetch_asset_skips_history_below_market_cap(mocker, stock_info):
    stock_info["marketCap"] = 500_000_000  # $500M — below $2B default
    mock = _mock_ticker(stock_info)
    mocker.patch("investdaytip.data_source.yf.Ticker", return_value=mock)

    result = fetch_asset("BIGC", min_market_cap=2_000_000_000)

    assert isinstance(result, StockData)
    assert result.ticker == "BIGC"
    assert result.market_cap == 500_000_000
    mock.history.assert_not_called()


def test_fetch_asset_fetches_history_above_market_cap(mocker, stock_info):
    # marketCap is 10B — above $2B default
    mock = _mock_ticker(stock_info)
    mocker.patch("investdaytip.data_source.yf.Ticker", return_value=mock)

    result = fetch_asset("BIGC", min_market_cap=2_000_000_000)

    assert isinstance(result, StockData)
    assert result.ticker == "BIGC"
    mock.history.assert_called_once()


def test_fetch_asset_unknown_market_cap_is_excluded(mocker):
    # When market cap is missing it cannot satisfy a min_market_cap filter,
    # so the expensive history fetch is skipped and a minimal record returned.
    info = {
        "quoteType": "STOCK",
        "shortName": "NoMktCap",
        "currency": "USD",
    }
    mock = _mock_ticker(info)
    mocker.patch("investdaytip.data_source.yf.Ticker", return_value=mock)

    result = fetch_asset("NOMKT", min_market_cap=2_000_000_000)

    assert isinstance(result, StockData)
    assert result.market_cap is None
    mock.history.assert_not_called()


def test_fetch_asset_unknown_market_cap_passes_when_filter_disabled(mocker):
    # With min_market_cap=0 the filter is off, so history is still fetched.
    info = {
        "quoteType": "STOCK",
        "shortName": "NoMktCap",
        "currency": "USD",
    }
    mock = _mock_ticker(info)
    mocker.patch("investdaytip.data_source.yf.Ticker", return_value=mock)

    result = fetch_asset("NOMKT", min_market_cap=0)

    assert isinstance(result, StockData)
    mock.history.assert_called_once()


def test_fetch_asset_rate_limit_retries_then_returns_error(mocker):
    mocker.patch("investdaytip.data_source.yf.Ticker", side_effect=YFRateLimitError())
    sleep = mocker.patch("investdaytip.data_source.time.sleep")

    result = fetch_asset("RATELIM")

    assert isinstance(result, StockData)
    assert any("rate limited" in e for e in result.errors)
    assert sleep.call_count == 3


def test_fetch_asset_generic_error_returns_error_dataclass(mocker):
    mocker.patch("investdaytip.data_source.yf.Ticker", side_effect=ValueError("bad data"))
    sleep = mocker.patch("investdaytip.data_source.time.sleep")

    result = fetch_asset("BROKEN")

    assert isinstance(result, StockData)
    assert any("info fetch failed" in e for e in result.errors)
    sleep.assert_not_called()
