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
    "mktCap": 10_000_000_000,
    "isEtf": False,
}

RATIOS_TTM = {
    "symbol": "TEST",
    "priceEarningsRatio": 25.0,
    "priceToBookRatio": 5.0,
    "pegRatio": 1.5,
    "returnOnEquity": 0.30,
    "returnOnAssets": 0.12,
    "netProfitMargin": 0.20,
    "earningsGrowth": 0.10,
    "revenueGrowth": 0.08,
    "debtToEquity": 1.5,
    "currentRatio": 2.0,
    "freeCashFlowPerShare": 5.0,
    "totalSharesOutstanding": 100_000_000,
    "dividendYield": 0.015,
    "payoutRatio": 0.30,
}

EARNINGS_SURPRISES = [
    {"symbol": "TEST", "date": "2025-11-01", "actualEarningResult": 1.10, "estimatedEarning": 1.00, "surprise": 0.10},
    {"symbol": "TEST", "date": "2025-08-01", "actualEarningResult": 1.05, "estimatedEarning": 1.00, "surprise": 0.05},
    {"symbol": "TEST", "date": "2025-05-01", "actualEarningResult": 0.98, "estimatedEarning": 1.00, "surprise": -0.02},
    {"symbol": "TEST", "date": "2025-02-01", "actualEarningResult": 1.02, "estimatedEarning": 1.00, "surprise": 0.02},
]


def _responses(history_days: int = 400) -> dict[str, list]:
    return {
        "profile/TEST": [PROFILE],
        "ratios-ttm/TEST": [RATIOS_TTM],
        "historical-price-eod/TEST": _mock_history_data(history_days),
        "earnings-surprises/TEST": EARNINGS_SURPRISES,
    }


def _build_mock_get(responses: dict[str, list]) -> MagicMock:
    """Build a mock ``_get`` that returns canned data based on URL suffix."""

    def side_effect(path: str, params: dict | None = None) -> list[dict]:
        for suffix, data in responses.items():
            if suffix in path:
                return data
        raise RuntimeError(f"Unexpected _get path: {path}")

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
    assert result.profit_margin == 0.20
    # Health
    assert result.debt_to_equity == 1.5
    assert result.current_ratio == 2.0
    # FCF: 5.0 * 100M = 500M
    assert result.free_cashflow == 500_000_000
    # Income
    assert result.dividend_yield == 0.015
    assert result.payout_ratio == 0.30
    # EPS surprise: avg of [10.0, 5.0, -2.0, 2.0] = 3.75
    assert result.eps_surprise == pytest.approx(3.75)
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
        "profile/TEST": [profile_etf],
        "ratios-ttm/TEST": [RATIOS_TTM],
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
    small = dict(PROFILE, mktCap=100_000_000, price=10.0)
    responses = {
        "profile/TEST": [small],
        "ratios-ttm/TEST": [RATIOS_TTM],
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
    responses["historical-price-eod/TEST"] = short_history
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


def test_fetch_asset_no_earnings_surprises(mocker):
    """Missing earnings-surprises data leaves eps_surprise as None."""
    responses = _responses()
    # Override earnings-surprises endpoint to return empty
    responses["earnings-surprises/TEST"] = []

    mocker.patch("investdaytip.data_source_fmp._get", _build_mock_get(responses))
    mocker.patch.dict(os.environ, {"FMP_API_KEY": "test_key"}, clear=False)

    result = fetch_asset("TEST")

    assert result.eps_surprise is None


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
    spy_responses["profile/SPY"] = [dict(PROFILE, symbol="SPY")]
    responses = _responses()
    responses["profile/SPY"] = [dict(PROFILE, symbol="SPY")]

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
