"""Tests for data_source_fmp — mocked HTTP, no live network."""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from investdaytip.data_source import StockData
from investdaytip.data_source_fmp import fetch_asset


def _mock_history_data(days: int = 400) -> list[dict]:
    from datetime import datetime, timedelta

    records = []
    today = datetime.now()
    price = 100.0
    for i in range(days, 0, -1):
        d = today - timedelta(days=i)
        records.append({
            "date": d.strftime("%Y-%m-%d"),
            "open": price,
            "high": price * 1.01,
            "low": price * 0.99,
            "close": price,
            "volume": 1000000,
        })
        price *= 1.0005  # slight uptrend
    return records


PROFILE = {
    "symbol": "TEST",
    "companyName": "Test Corp",
    "sector": "Technology",
    "exchange": "NYSE",
    "currency": "USD",
    "price": 150.0,
    "marketCap": 10_000_000_000,
    "isEtf": False,
}

RATIOS_TTM = {
    "symbol": "TEST",
    "priceToEarningsRatioTTM": 25.0,
    "priceToBookRatioTTM": 5.0,
    "priceToEarningsGrowthRatioTTM": 1.5,
    "netProfitMarginTTM": 0.20,
    "debtToEquityRatioTTM": 1.5,
    "currentRatioTTM": 2.0,
    "freeCashFlowPerShareTTM": 5.0,
    "dividendYieldTTM": 0.015,
    "dividendPayoutRatioTTM": 0.30,
}

KEY_METRICS_TTM = {
    "symbol": "TEST",
    "returnOnEquityTTM": 0.30,
    "returnOnAssetsTTM": 0.12,
}

FINANCIAL_GROWTH = {
    "symbol": "TEST",
    "date": "2025-09-30",
    "epsgrowth": 0.10,
    "revenueGrowth": 0.08,
}

EARNINGS_SURPRISES = [
    {"date": "2025-10-30", "symbol": "TEST",
     "actualEarningResult": 1.10, "estimatedEarning": 1.00},
    {"date": "2025-07-30", "symbol": "TEST",
     "actualEarningResult": 0.95, "estimatedEarning": 1.00},
    {"date": "2025-04-30", "symbol": "TEST",
     "actualEarningResult": 1.05, "estimatedEarning": 1.00},
    {"date": "2025-01-30", "symbol": "TEST",
     "actualEarningResult": 1.20, "estimatedEarning": 1.10},
]


def _responses(history_days: int = 400) -> dict[tuple[str, str], list]:
    return {
        ("profile", "TEST"): [PROFILE],
        ("ratios-ttm", "TEST"): [RATIOS_TTM],
        ("key-metrics-ttm", "TEST"): [KEY_METRICS_TTM],
        ("financial-growth", "TEST"): [FINANCIAL_GROWTH],
        ("earnings-surprises", "TEST"): EARNINGS_SURPRISES,
        ("historical-price-eod/full", "TEST"): _mock_history_data(history_days),
    }


def _build_mock_get(responses: dict[tuple[str, str], list]) -> MagicMock:
    """Build a mock ``_get`` that returns canned data based on endpoint + symbol."""

    def side_effect(path: str, params: dict | None = None) -> list[dict]:
        symbol = (params or {}).get("symbol") if params else None
        key = (path, symbol or "")
        if key in responses:
            return responses[key]
        raise RuntimeError(f"Unexpected _get path: {path} params: {params}")

    mock = MagicMock(side_effect=side_effect)
    return mock


# ── Tests ──────────────────────────────────────────────────────────────────


def test_fetch_asset_basic(mocker):
    """Happy path: returns a StockData with expected fields."""
    mocker.patch("investdaytip.data_source_fmp._get", _build_mock_get(_responses()))
    mocker.patch.dict(os.environ, {"FMP_API_KEY": "test_key"}, clear=False)

    result = fetch_asset("TEST")

    assert isinstance(result, StockData)
    assert result.ticker == "TEST"
    assert result.name == "Test Corp"
    assert result.sector == "Technology"
    assert result.currency == "USD"
    assert result.exchange == "NYSE"
    assert result.current_price == 150.0
    assert result.market_cap == 10_000_000_000
    # Valuation
    assert result.trailing_pe == 25.0
    assert result.price_to_book == 5.0
    assert result.peg_ratio == 1.5
    # Quality
    assert result.return_on_equity == 0.30
    assert result.return_on_assets == 0.12
    assert result.profit_margin == 0.20
    assert result.earnings_growth == 0.10
    assert result.revenue_growth == 0.08
    # Health — FMP's pure ratio (1.5x) is now scaled to yfinance's
    # percentage convention (150.0 == 1.5x) for consistent scoring.
    assert result.debt_to_equity == 150.0
    assert result.current_ratio == 2.0
    # FCF: 5.0 * (10B / 150) = 333.33M
    assert result.free_cashflow == pytest.approx(333_333_333.33, rel=1e-4)
    # Income
    assert result.dividend_yield == 0.015
    assert result.payout_ratio == 0.30
    # Trend + technicals (from price history)
    assert result.price_vs_sma200 is not None
    assert result.return_1m is not None
    assert result.return_12m is not None
    assert result.sma200_slope is not None
    assert result.daily_change is not None
    assert result.rsi_14 is not None
    assert result.macd_histogram is not None


def test_fetch_asset_etf_returns_error(mocker):
    """ETF tickers return a StockData with an error message."""
    profile_etf = dict(PROFILE, isEtf=True)
    responses = {
        ("profile", "TEST"): [profile_etf],
        ("ratios-ttm", "TEST"): [RATIOS_TTM],
    }
    mocker.patch("investdaytip.data_source_fmp._get", _build_mock_get(responses))
    mocker.patch.dict(os.environ, {"FMP_API_KEY": "test_key"}, clear=False)

    result = fetch_asset("TEST")

    assert isinstance(result, StockData)
    assert any("ETF" in e for e in (result.errors or []))


def test_fetch_asset_missing_api_key(mocker):
    """Missing FMP_API_KEY returns an error dataclass."""
    mocker.patch.dict(os.environ, {}, clear=True)

    result = fetch_asset("TEST")

    assert isinstance(result, StockData)
    assert result.errors


def test_fetch_asset_below_market_cap(mocker):
    """Below-threshold market cap returns early with only market_cap set."""
    small = dict(PROFILE, marketCap=100_000_000, price=10.0)
    responses = {
        ("profile", "TEST"): [small],
        ("ratios-ttm", "TEST"): [RATIOS_TTM],
    }
    mocker.patch("investdaytip.data_source_fmp._get", _build_mock_get(responses))
    mocker.patch.dict(os.environ, {"FMP_API_KEY": "test_key"}, clear=False)

    result = fetch_asset("TEST", min_market_cap=1_000_000_000)

    assert isinstance(result, StockData)
    assert result.market_cap == 100_000_000
    assert "market cap below threshold" in (result.errors or [])


def test_fetch_asset_short_history(mocker):
    """Short history still produces valid StockData (some trend fields None)."""
    short_history = _mock_history_data(30)
    responses = _responses(history_days=30)
    responses[("historical-price-eod/full", "TEST")] = short_history
    mocker.patch("investdaytip.data_source_fmp._get", _build_mock_get(responses))
    mocker.patch.dict(os.environ, {"FMP_API_KEY": "test_key"}, clear=False)

    result = fetch_asset("TEST")

    # Fields needing >= 200 data points
    assert result.price_vs_sma200 is None
    assert result.return_12m is None
    assert result.sma200_slope is None
    # Fields needing minimal data
    assert result.daily_change is not None
    assert result.return_1m is not None
    # RSI/MACD need >= 35 data points, 30 is too few
    assert result.rsi_14 is None
    assert result.macd_histogram is None


def test_fetch_asset_rate_limit_propagates(mocker):
    """FmpRateLimitError propagates through fetch_asset."""
    from investdaytip.data_source_fmp import FmpRateLimitError

    def raise_limit(*args: object, **kwargs: object) -> list[dict]:
        raise FmpRateLimitError("FMP rate limit: limit reached")

    mocker.patch("investdaytip.data_source_fmp._get", side_effect=raise_limit)
    mocker.patch.dict(os.environ, {"FMP_API_KEY": "test_key"}, clear=False)

    with pytest.raises(FmpRateLimitError):
        fetch_asset("TEST")


def test_recommend_rate_limit_fallback_automatic(mocker):
    """Rate-limited tickers automatically fall back to yfinance."""
    from investdaytip.recommender import recommend

    def raise_limit(*args: object, **kwargs: object) -> list[dict]:
        from investdaytip.data_source_fmp import FmpRateLimitError
        raise FmpRateLimitError("FMP rate limit: limit reached")

    mocker.patch("investdaytip.data_source_fmp._get", side_effect=raise_limit)
    mocker.patch.dict(os.environ, {"FMP_API_KEY": "test_key"}, clear=False)
    mocker.patch("investdaytip.recommender.get_superinvestor_data", return_value={})
    mocker.patch("investdaytip.recommender._log_fallback")  # silence log

    data = StockData(ticker="TEST", name="Test YF", sector="Technology")
    mocker.patch("investdaytip.recommender.fetch_asset", return_value=data)

    results = recommend(
        tickers=["TEST"],
        top_n=5,
        min_market_cap=0,
        scoring_model="classic",
        data_source="fmp",
    )

    assert len(results) == 1
    assert results[0].data.ticker == "TEST"
    assert results[0].data.name == "Test YF"


def test_recommend_dispatches_to_fmp(mocker, monkeypatch):
    """recommend() uses FMP fetcher when data_source='fmp'."""
    from investdaytip.recommender import recommend

    # Pre-flight check needs SPY profile responses
    spy_responses = _responses()
    spy_responses[("profile", "SPY")] = [dict(PROFILE, symbol="SPY")]
    responses = _responses()
    responses[("profile", "SPY")] = [dict(PROFILE, symbol="SPY")]

    mocker.patch("investdaytip.data_source_fmp._get", _build_mock_get(responses))
    mocker.patch.dict(os.environ, {"FMP_API_KEY": "test_key"}, clear=False)
    mocker.patch("investdaytip.recommender.get_superinvestor_data", return_value={})
    mocker.patch("investdaytip.recommender._log_fallback")  # silence log

    results = recommend(
        tickers=["TEST"],
        top_n=5,
        min_market_cap=0,
        scoring_model="classic",
        data_source="fmp",
    )

    assert len(results) == 1
    assert results[0].data.ticker == "TEST"
    assert results[0].data.name == "Test Corp"
    assert results[0].total >= 0


def test_recommend_preflight_rate_limit_auto_fallback(mocker):
    """Pre-flight rate limit triggers automatic yfinance fallback."""
    from investdaytip.recommender import recommend

    def raise_limit(*args: object, **kwargs: object) -> list[dict]:
        from investdaytip.data_source_fmp import FmpRateLimitError
        raise FmpRateLimitError("FMP rate limit: limit reached")

    mocker.patch("investdaytip.data_source_fmp._get", side_effect=raise_limit)
    mocker.patch.dict(os.environ, {"FMP_API_KEY": "test_key"}, clear=False)
    mocker.patch("investdaytip.recommender.get_superinvestor_data", return_value={})
    mocker.patch("investdaytip.recommender._log_fallback")  # silence log

    data = StockData(ticker="AAPL", name="Apple YF", sector="Technology")
    mocker.patch("investdaytip.recommender.fetch_asset", return_value=data)

    results = recommend(
        tickers=["AAPL"],
        top_n=5,
        min_market_cap=0,
        scoring_model="classic",
        data_source="fmp",
    )

    assert len(results) == 1
    assert results[0].data.ticker == "AAPL"
    assert results[0].data.name == "Apple YF"


def test_recommend_preflight_fmp_error_auto_fallback(mocker):
    """Pre-flight FmpError (FMP down / invalid key) falls back to yfinance.

    AGENTS.md: 'FMP unavailable (network, API key invalid) → caught by
    pre-flight, all tickers fallback to yfinance'.
    """
    from investdaytip.data_source_fmp import FmpError
    from investdaytip.recommender import recommend

    def raise_error(*args: object, **kwargs: object) -> list[dict]:
        raise FmpError("FMP unavailable")

    mocker.patch("investdaytip.data_source_fmp._get", side_effect=raise_error)
    mocker.patch.dict(os.environ, {"FMP_API_KEY": "test_key"}, clear=False)
    mocker.patch("investdaytip.recommender.get_superinvestor_data", return_value={})
    mocker.patch("investdaytip.recommender._log_fallback")  # silence log

    data = StockData(ticker="AAPL", name="Apple YF", sector="Technology")
    fetch = mocker.patch("investdaytip.recommender.fetch_asset", return_value=data)

    results = recommend(
        tickers=["AAPL"],
        top_n=5,
        min_market_cap=0,
        scoring_model="classic",
        data_source="fmp",
    )

    assert len(results) == 1
    assert results[0].data.ticker == "AAPL"
    assert results[0].data.name == "Apple YF"
    fetch.assert_called_once()


def test_fetch_asset_caches_under_fmp_key_only(mocker, enabled_temp_cache):
    """FMP-cached info must not leak into the shared yfinance info cache key."""
    from investdaytip.cache import cache_fmp_info_get, cache_info_get

    mocker.patch("investdaytip.data_source_fmp._get", _build_mock_get(_responses()))
    mocker.patch.dict(os.environ, {"FMP_API_KEY": "test_key"}, clear=False)

    fetch_asset("TEST")

    fmp_cached = cache_fmp_info_get("TEST")
    assert fmp_cached is not None
    assert "profile" in fmp_cached
    assert fmp_cached["profile"]["companyName"] == "Test Corp"
    # The shared yfinance-style info key stays untouched.
    assert cache_info_get("TEST") is None
