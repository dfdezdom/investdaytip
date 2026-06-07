"""Tests for data_source — yfinance mocking only, no live network."""

from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest
from yfinance.exceptions import YFRateLimitError

from investdaytip.data_source import (
    EtfData,
    StockData,
    _first,
    _safe_get,
    _technical_indicators,
    fetch_asset,
)


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


def test_fetch_asset_below_market_cap_still_fetches_history(mocker, stock_info):
    stock_info["marketCap"] = 500_000_000  # $500M — below $2B default
    mock = _mock_ticker(stock_info)
    mocker.patch("investdaytip.data_source.yf.Ticker", return_value=mock)

    result = fetch_asset("BIGC", min_market_cap=2_000_000_000)

    assert isinstance(result, StockData)
    assert result.ticker == "BIGC"
    assert result.market_cap == 500_000_000
    mock.history.assert_called_once()
    assert result.current_price is not None


def test_fetch_asset_fetches_history_above_market_cap(mocker, stock_info):
    # marketCap is 10B — above $2B default
    mock = _mock_ticker(stock_info)
    mocker.patch("investdaytip.data_source.yf.Ticker", return_value=mock)

    result = fetch_asset("BIGC", min_market_cap=2_000_000_000)

    assert isinstance(result, StockData)
    assert result.ticker == "BIGC"
    mock.history.assert_called_once()


def test_fetch_asset_unknown_market_cap_still_fetches_history(mocker):
    # Market cap is missing but history is still fetched (no early return).
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
    mock.history.assert_called_once()
    assert result.current_price is not None


def test_fetch_asset_unknown_market_cap_passes_when_filter_disabled(mocker):
    # With min_market_cap=0, history is still fetched (same as default behavior).
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


def test_technical_indicators_returns_none_for_short_series():
    short = pd.Series([100.0] * 10)
    rsi, macd = _technical_indicators(short)
    assert rsi is None
    assert macd is None


def test_technical_indicators_computes_rsi_and_macd():
    # Build a 40-day downtrend to get RSI below 50
    prices = [100.0]
    for _ in range(39):
        prices.append(prices[-1] * 0.99)  # ~1% daily decline
    close = pd.Series(prices)
    rsi, macd = _technical_indicators(close)

    assert rsi is not None
    assert 0.0 <= rsi <= 100.0
    assert rsi < 50.0  # downtrend → RSI below 50

    assert macd is not None
    assert isinstance(macd, float)


def test_technical_indicators_oversold_rsi():
    # Sharp 40-day drop to drive RSI very low
    prices = np.linspace(100, 50, 40)
    close = pd.Series(prices)
    rsi, _ = _technical_indicators(close)
    assert rsi is not None
    assert rsi < 30.0  # clearly oversold


# ── _safe_get() ──────────────────────────────────────────────────────────────


class TestSafeGet:
    def test_returns_none_for_missing_key(self):
        assert _safe_get({}, "foo") is None

    def test_returns_none_for_none_value(self):
        assert _safe_get({"foo": None}, "foo") is None

    def test_returns_float_for_valid_number(self):
        assert _safe_get({"foo": 42}, "foo") == 42.0
        assert _safe_get({"foo": "3.14"}, "foo") == 3.14

    def test_returns_none_for_nan(self):
        assert _safe_get({"foo": float("nan")}, "foo") is None

    def test_returns_none_for_positive_inf(self):
        assert _safe_get({"foo": float("inf")}, "foo") is None

    def test_returns_none_for_negative_inf(self):
        assert _safe_get({"foo": float("-inf")}, "foo") is None

    def test_returns_none_for_non_numeric_string(self):
        assert _safe_get({"foo": "not a number"}, "foo") is None

    def test_returns_zero_for_zero(self):
        assert _safe_get({"foo": 0}, "foo") == 0.0
        assert _safe_get({"foo": 0.0}, "foo") == 0.0


# ── _first() ─────────────────────────────────────────────────────────────────


class TestFirst:
    def test_returns_first_non_none(self):
        assert _first(None, None, 3.0) == 3.0

    def test_preserves_zero(self):
        assert _first(0.0, 1.0) == 0.0

    def test_returns_none_when_all_none(self):
        assert _first(None, None, None) is None

    def test_returns_first_value(self):
        assert _first(1.0, 2.0, 3.0) == 1.0


# ── ETF fetch path ───────────────────────────────────────────────────────────


def _mock_etf_ticker(info: dict, history: pd.DataFrame | None = None) -> MagicMock:
    mock = MagicMock()
    mock.info = info
    mock.history.return_value = history if history is not None else pd.DataFrame({"Close": [100] * 300})
    return mock


def test_fetch_asset_detects_etf_quote_type(mocker):
    info = {
        "quoteType": "ETF",
        "longName": "Vanguard S&P 500 ETF",
        "currency": "USD",
        "exchange": "PCX",
        "totalAssets": 400_000_000_000,
        "annualReportExpenseRatio": 0.0003,
        "threeYearAverageReturn": 0.12,
        "fiveYearAverageReturn": 0.14,
        "yield": 0.015,
    }
    mock = _mock_etf_ticker(info)
    mocker.patch("investdaytip.data_source.yf.Ticker", return_value=mock)

    result = fetch_asset("VOO")
    assert isinstance(result, EtfData)
    assert result.ticker == "VOO"
    assert result.total_assets == 400_000_000_000
    assert result.expense_ratio == 0.0003


def test_fetch_asset_etf_expense_ratio_fallback_chain(mocker):
    info = {
        "quoteType": "ETF",
        "longName": "Test ETF",
        "currency": "USD",
        "totalAssets": 1_000_000_000,
        "netExpenseRatio": 0.0010,
    }
    mock = _mock_etf_ticker(info)
    mocker.patch("investdaytip.data_source.yf.Ticker", return_value=mock)

    result = fetch_asset("TEST")
    assert isinstance(result, EtfData)
    assert result.expense_ratio == 0.0010


def test_fetch_asset_etf_sharpe_proxy_computed(mocker):
    info = {
        "quoteType": "ETF",
        "longName": "Test ETF",
        "currency": "USD",
        "totalAssets": 1_000_000_000,
    }
    prices = np.linspace(100, 130, 300)
    history = pd.DataFrame({"Close": prices})
    mock = _mock_etf_ticker(info, history)
    mocker.patch("investdaytip.data_source.yf.Ticker", return_value=mock)

    result = fetch_asset("TEST")
    assert isinstance(result, EtfData)
    assert result.return_12m is not None
    assert result.volatility_1y is not None
    assert result.sharpe_proxy is not None
