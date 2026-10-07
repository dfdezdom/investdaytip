"""Tests for data_source — yfinance mocking only, no live network."""

from io import StringIO
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest
from yfinance.exceptions import YFRateLimitError

from investdaytip.data_source import (
    EtfData,
    StockData,
    _apply_history_common,
    _compute_eps_surprise,
    _first,
    _history_lags_quote,
    _safe_get,
    _sanitize_yield,
    _technical_indicators,
    _ttm_dividend_yield,
    fetch_asset,
)


def _mock_ticker(
    info: dict,
    history: pd.DataFrame | None = None,
    earnings_dates: pd.DataFrame | None = None,
) -> MagicMock:
    """Build a mock yf.Ticker returning the given info dict and history."""
    mock = MagicMock()
    mock.info = info
    mock.history.return_value = history if history is not None else pd.DataFrame({"Close": [100] * 300})
    mock.dividends = pd.Series(dtype=float)
    mock.earnings_dates = earnings_dates if earnings_dates is not None else pd.DataFrame()
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


def test_fetch_asset_below_market_cap_skips_history(mocker, stock_info):
    stock_info["marketCap"] = 500_000_000  # $500M — below $2B default
    mock = _mock_ticker(stock_info)
    mocker.patch("investdaytip.data_source.yf.Ticker", return_value=mock)

    result = fetch_asset("BIGC", min_market_cap=2_000_000_000)

    assert isinstance(result, StockData)
    assert result.ticker == "BIGC"
    assert result.market_cap == 500_000_000
    # History must be skipped for below-threshold tickers (optimization).
    mock.history.assert_not_called()
    assert any("threshold" in e.lower() for e in (result.errors or []))


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
    assert sleep.call_count == 4


def test_fetch_asset_generic_error_returns_error_dataclass(mocker):
    mocker.patch("investdaytip.data_source.yf.Ticker", side_effect=ValueError("bad data"))
    sleep = mocker.patch("investdaytip.data_source.time.sleep")

    result = fetch_asset("BROKEN")

    assert isinstance(result, StockData)
    assert any("info fetch failed" in e for e in result.errors)
    sleep.assert_not_called()


def test_fetch_stock_unified_statement_derivation():
    """Statement-derived fundamentals override the Yahoo info fields so every
    path shares one definition (2026-10-02 unification)."""
    from investdaytip.data_source import _fetch_stock

    income = pd.DataFrame(
        {
            pd.Timestamp("2022-12-31"): {"Net Income": 60.0, "Total Revenue": 300.0,
                                          "Basic EPS": 5.0},
            pd.Timestamp("2023-12-31"): {"Net Income": 80.0, "Total Revenue": 350.0,
                                          "Basic EPS": 6.0},
            pd.Timestamp("2024-12-31"): {"Net Income": 100.0, "Total Revenue": 400.0,
                                          "Basic EPS": 7.0},
        }
    )
    balance = pd.DataFrame(
        {
            pd.Timestamp("2023-12-31"): {"Total Assets": 950.0},
            pd.Timestamp("2024-12-31"): {
                "Total Assets": 1000.0, "Stockholders Equity": 200.0,
                "Total Debt": 50.0, "Current Assets": 400.0,
                "Current Liabilities": 200.0,
            },
        }
    )
    info = {
        "trailingPE": 20.0,
        "earningsGrowth": 5.0, "revenueGrowth": 9.0, "returnOnEquity": 9.99,
        "returnOnAssets": 9.99, "profitMargins": 0.99, "debtToEquity": 999.0,
        "currentRatio": 9.99, "freeCashflow": 1.0,
        "operatingCashflow": 100.0, "capitalExpenditures": -30.0,
    }
    data = _fetch_stock("TEST", info, pd.DataFrame(),
                        income_stmt=income, balance_sheet=balance)
    assert data.earnings_growth == pytest.approx((100 - 80) / 80)   # FY-vs-FY
    assert data.revenue_growth == pytest.approx((400 - 350) / 350)
    assert data.return_on_equity == pytest.approx(100 / 200)        # our formula
    assert data.return_on_assets == pytest.approx(100 / 1000)
    assert data.profit_margin == pytest.approx(100 / 400)
    assert data.debt_to_equity == pytest.approx(50 / 200 * 100)
    assert data.current_ratio == pytest.approx(400 / 200)
    assert data.free_cashflow == pytest.approx(70.0)                # OCF - |capex|
    assert data.eps_acceleration == pytest.approx((7 / 6 - 1) - (6 / 5 - 1))
    assert data.peg_ratio == pytest.approx(20.0 / (((100 - 80) / 80) * 100))  # PEG in percent units


def test_fetch_stock_keeps_info_values_without_statements():
    """No statements → the info fields survive unchanged (graceful fallback)."""
    from investdaytip.data_source import _fetch_stock

    info = {"earningsGrowth": 0.5, "returnOnEquity": 0.2, "freeCashflow": 7.0}
    data = _fetch_stock("TEST", info, pd.DataFrame())
    assert data.earnings_growth == 0.5
    assert data.return_on_equity == 0.2
    assert data.free_cashflow == 7.0
    assert data.margin_improving is None
def test_fetch_stock_flags_from_statements():
    """Statements present → YoY-improvement flags computed; absent → None."""
    from investdaytip.data_source import _fetch_stock

    income = pd.DataFrame(
        {
            pd.Timestamp("2023-12-31"): {
                "Net Income": 80.0, "Total Revenue": 350.0, "Gross Profit": 157.5,
            },
            pd.Timestamp("2024-12-31"): {
                "Net Income": 100.0, "Total Revenue": 400.0, "Gross Profit": 200.0,
            },
        }
    )
    balance = pd.DataFrame(
        {
            pd.Timestamp("2023-12-31"): {"Total Assets": 1000.0},
            pd.Timestamp("2024-12-31"): {"Total Assets": 1000.0},
        }
    )
    data = _fetch_stock(
        "TEST", {}, pd.DataFrame(), income_stmt=income, balance_sheet=balance
    )
    assert data.margin_improving is True  # 0.50 > 0.45
    assert data.roa_improving is True     # 0.10 > 0.08

    plain = _fetch_stock("TEST", {}, pd.DataFrame())
    assert plain.margin_improving is None
    assert plain.roa_improving is None


def test_derived_peg_requires_positive_pe_and_growth():
    """Derived PEG = P/E ÷ growth *in percent* (classic PEG convention —
    the scorer's best=0.8/worst=3.0 thresholds assume that scale), and only
    with positive growth (decliners have no meaningful PEG — they stay
    None → neutral in Value)."""
    from investdaytip.data_source import _derive_stock_data, _Fundamentals

    declining = _Fundamentals(ni=80.0, eps=6.0, ni_prev=100.0, rev=300.0, rev_prev=350.0)
    d = _derive_stock_data(
        "T", {"shortName": "T"}, 100.0, (None, None, None, None, None, None),
        None, None, declining, None, None,
    )
    assert d.earnings_growth == pytest.approx(-0.2)
    assert d.peg_ratio is None

    growing = _Fundamentals(ni=100.0, eps=7.0, ni_prev=80.0, rev=400.0, rev_prev=350.0)
    d2 = _derive_stock_data(
        "T", {"shortName": "T"}, 100.0, (None, None, None, None, None, None),
        None, None, growing, None, None,
    )
    assert d2.peg_ratio == pytest.approx(
        d2.trailing_pe / (d2.earnings_growth * 100.0)
    )
    assert d2.peg_ratio < 5.0     # percent units, not a decimal-division ×100


def test_fetch_stock_ttm_levels_from_quarterly_frames(mocker):
    """Levels read TTM (4-quarter sums / latest balances) when quarterly
    frames exist; comparisons stay FY-vs-FY; TTM FCF wins over info."""
    from investdaytip.data_source import _fetch_stock

    income = pd.DataFrame(
        {
            pd.Timestamp("2023-12-31"): {"Net Income": 60.0, "Total Revenue": 300.0,
                                          "Basic EPS": 5.0},
            pd.Timestamp("2024-12-31"): {"Net Income": 80.0, "Total Revenue": 350.0,
                                          "Basic EPS": 6.0},
        }
    )
    balance = pd.DataFrame(
        {pd.Timestamp("2024-12-31"): {"Total Assets": 1000.0, "Stockholders Equity": 200.0}}
    )
    q_income = pd.DataFrame(
        {
            pd.Timestamp("2024-03-31"): {"Net Income": 10.0, "Total Revenue": 90.0},
            pd.Timestamp("2024-06-30"): {"Net Income": 12.0, "Total Revenue": 95.0},
            pd.Timestamp("2024-09-30"): {"Net Income": 14.0, "Total Revenue": 100.0},
            pd.Timestamp("2024-12-31"): {"Net Income": 16.0, "Total Revenue": 105.0},
        }
    )
    q_balance = pd.DataFrame(
        {pd.Timestamp("2024-12-31"): {"Total Assets": 1100.0, "Stockholders Equity": 220.0}}
    )
    q_cash = pd.DataFrame(
        {
            pd.Timestamp("2024-03-31"): {"Free Cash Flow": 5.0},
            pd.Timestamp("2024-06-30"): {"Free Cash Flow": 6.0},
            pd.Timestamp("2024-09-30"): {"Free Cash Flow": 7.0},
            pd.Timestamp("2024-12-31"): {"Free Cash Flow": 8.0},
        }
    )
    mocker.patch(
        "investdaytip.data_source.fetch_quarterly_frames",
        return_value=(q_income, q_balance, q_cash),
    )
    info = {"trailingPE": 10.0, "operatingCashflow": 100.0, "capitalExpenditures": -30.0}
    data = _fetch_stock("TEST", info, pd.DataFrame(),
                        income_stmt=income, balance_sheet=balance)

    # levels: TTM sums / latest quarter balances
    assert data.return_on_equity == pytest.approx(52.0 / 220.0)   # TTM NI / TTM equity
    assert data.profit_margin == pytest.approx(52.0 / 390.0)
    assert data.debt_to_equity is None or data.debt_to_equity >= 0
    # comparisons: still FY-vs-FY (80 vs 60)
    assert data.earnings_growth == pytest.approx((80 - 60) / 60)
    # FCF: quarterly sum beats the info OCF−capex fallback
    assert data.free_cashflow == pytest.approx(26.0)


def test_suppress_stderr_is_thread_safe():
    """Concurrent _suppress_stderr() must never leave sys.stderr pointing at a
    closed devnull (regression: parallel fetch threads corrupted the stream —
    'I/O operation on closed file' at interpreter shutdown)."""
    import sys
    import threading

    from investdaytip.data_source import _suppress_stderr

    real_stderr = sys.stderr

    def worker():
        for _ in range(25):
            with _suppress_stderr():
                pass

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sys.stderr is real_stderr
    sys.stderr.write("")  # still usable


def test_eps_acceleration_second_derivative():
    from investdaytip.data_source import _eps_acceleration

    # eps 5 → 6 → 7: growth 20% then 16.7% → deceleration of 3.3pp
    assert _eps_acceleration(7.0, 6.0, 5.0) == pytest.approx((7 / 6 - 1) - (6 / 5 - 1))
    assert _eps_acceleration(5.0, 5.0, 5.0) == pytest.approx(0.0)
    assert _eps_acceleration(None, 6.0, 5.0) is None
    assert _eps_acceleration(7.0, 6.0, None) is None
    assert _eps_acceleration(7.0, 0.0, 5.0) is None  # zero denominator


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


class TestSanitizeYield:
    def test_decimal_yield_preserved(self):
        assert _sanitize_yield(0.054) == 0.054

    def test_percentage_yield_divided_by_100(self):
        assert _sanitize_yield(5.4) == pytest.approx(0.054)

    def test_zero_yield_preserved(self):
        assert _sanitize_yield(0.0) == 0.0

    def test_none_returns_none(self):
        assert _sanitize_yield(None) is None

    def test_non_finite_returns_none(self):
        assert _sanitize_yield(float("nan")) is None
        assert _sanitize_yield(float("inf")) is None


class TestTtmDividendYield:
    def test_ttm_yield_from_dividends(self):
        today = pd.Timestamp.now().normalize()
        dates = pd.date_range(end=today, periods=4, freq="91D")
        dividends = pd.Series([0.25, 0.25, 0.25, 0.25], index=dates)
        assert _ttm_dividend_yield(dividends, 100.0) == pytest.approx(0.01)

    def test_ignores_dividends_older_than_one_year(self):
        today = pd.Timestamp.now().normalize()
        old = pd.Series([10.0], index=[today - pd.Timedelta(days=400)])
        assert _ttm_dividend_yield(old, 100.0) is None

    def test_none_or_empty_returns_none(self):
        assert _ttm_dividend_yield(None, 100.0) is None
        assert _ttm_dividend_yield(pd.Series(dtype=float), 100.0) is None

    def test_zero_price_returns_none(self):
        today = pd.Timestamp.now().normalize()
        dividends = pd.Series([1.0], index=[today])
        assert _ttm_dividend_yield(dividends, 0.0) is None
        assert _ttm_dividend_yield(dividends, None) is None


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


# ── _compute_eps_surprise() ──────────────────────────────────────────────────


def _earnings_dates_df(surprises: list[float]) -> pd.DataFrame:
    today = pd.Timestamp.now().normalize()
    dates = pd.date_range(end=today, periods=len(surprises), freq="91D")
    return pd.DataFrame(
        {
            "EPS Estimate": [1.0] * len(surprises),
            "Reported EPS": [1.0] * len(surprises),
            "Surprise(%)": surprises,
        },
        index=dates,
    )


def test_compute_eps_surprise_averages_last_four_quarters():
    df = _earnings_dates_df([2.0, 4.0, 6.0, 8.0, 10.0])
    assert _compute_eps_surprise(df) == pytest.approx(7.0)


def test_compute_eps_surprise_ignores_future_rows():
    today = pd.Timestamp.now().normalize()
    future = today + pd.Timedelta(days=30)
    past = pd.date_range(end=today, periods=4, freq="91D")
    df = pd.DataFrame(
        {
            "EPS Estimate": [1.0] * 5,
            "Reported EPS": [1.0] * 4 + [None],
            "Surprise(%)": [2.0, 4.0, 6.0, 8.0, None],
        },
        index=pd.DatetimeIndex(list(past) + [future]),
    )
    assert _compute_eps_surprise(df) == pytest.approx(5.0)


def test_compute_eps_surprise_returns_none_when_empty():
    assert _compute_eps_surprise(pd.DataFrame()) is None
    assert _compute_eps_surprise(None) is None


def test_compute_eps_surprise_deduplicates_same_report_day():
    today = pd.Timestamp.now().normalize()
    idx = pd.DatetimeIndex(
        [today, today - pd.Timedelta(days=1), today - pd.Timedelta(days=1)]
    )
    df = pd.DataFrame(
        {
            "EPS Estimate": [1.0, 1.0, 1.0],
            "Reported EPS": [1.0, 1.0, 1.0],
            "Surprise(%)": [10.0, 5.0, 7.0],
        },
        index=idx,
    )
    # Latest entry for the duplicate day should be kept.
    assert _compute_eps_surprise(df, lookback_quarters=2) == pytest.approx(8.5)


def test_fetch_asset_populates_eps_surprise(mocker, stock_info):
    earnings_dates = _earnings_dates_df([5.0, 5.0, 5.0, 5.0])
    mock = _mock_ticker(stock_info, earnings_dates=earnings_dates)
    mocker.patch("investdaytip.data_source.yf.Ticker", return_value=mock)

    result = fetch_asset("BIGC")
    assert isinstance(result, StockData)
    assert result.eps_surprise == pytest.approx(5.0)


def test_fetch_asset_deduplicates_earnings_dates_index(mocker, stock_info):
    """Duplicate report days from yfinance must not crash JSON serialization."""
    today = pd.Timestamp.now().normalize()
    idx = pd.DatetimeIndex([today, today - pd.Timedelta(days=1), today - pd.Timedelta(days=1)])
    earnings_dates = pd.DataFrame(
        {
            "EPS Estimate": [1.0, 1.0, 1.0],
            "Reported EPS": [1.0, 1.0, 1.0],
            "Surprise(%)": [10.0, 5.0, 7.0],
        },
        index=idx,
    )
    mock = _mock_ticker(stock_info, earnings_dates=earnings_dates)
    mocker.patch("investdaytip.data_source.yf.Ticker", return_value=mock)
    cache_set = mocker.patch("investdaytip.cache.cache_earnings_dates_set")

    result = fetch_asset("BIGC")

    assert isinstance(result, StockData)
    assert result.eps_surprise == pytest.approx(8.5)
    cache_set.assert_called_once()
    # The JSON written to cache must have a unique index.
    written_json = cache_set.call_args[0][1]
    cached = pd.read_json(StringIO(written_json))
    assert not cached.index.duplicated().any()


# ── Price / history session consistency ──────────────────────────────────────
#
# The quote (``info``, cached a day) and the price history (cached 15 min)
# come from different Yahoo endpoints, so a chart response can arrive a
# session behind.  A row must never pair today's price with a 1M /
# daily-change / RSI window ending yesterday — the 2026-10-06 run shipped 18
# of the top-100 tickers that way (FIVE: price 209.92 next to -10.01% 1M, the
# Oct-5 numbers).


def _session_history(last_date: str, bars: int = 40) -> pd.DataFrame:
    """Daily closes of *bars* sessions ending on *last_date* (tz-aware)."""
    idx = pd.date_range(end=last_date, periods=bars, freq="D", tz="America/New_York")
    return pd.DataFrame({"Close": [100.0 + i for i in range(bars)]}, index=idx)


def _quote_epoch(iso: str) -> float:
    """Epoch seconds for *iso* (a UTC wall time), as ``regularMarketTime``."""
    return float(pd.Timestamp(iso, tz="UTC").timestamp())


class TestHistoryLagsQuote:
    def test_detects_chart_one_session_behind_the_quote(self):
        history = _session_history("2026-10-05")
        info = {"regularMarketTime": _quote_epoch("2026-10-06 20:00")}

        assert _history_lags_quote(info, history) is True

    def test_same_session_does_not_lag(self):
        # Intraday the quote and the chart's partial bar share a date.
        history = _session_history("2026-10-05")
        info = {"regularMarketTime": _quote_epoch("2026-10-05 20:00")}

        assert _history_lags_quote(info, history) is False

    def test_naive_index_from_the_cache_is_read_as_utc(self):
        # to_json → read_json keeps the dates but drops the timezone.
        naive = _session_history("2026-10-05").tz_convert("UTC").tz_localize(None)
        info = {"regularMarketTime": _quote_epoch("2026-10-06 20:00")}

        assert _history_lags_quote(info, naive) is True

    def test_missing_or_unusable_quote_time_skips_the_check(self):
        history = _session_history("2026-10-05")

        assert _history_lags_quote({}, history) is False
        assert _history_lags_quote({"regularMarketTime": "soon"}, history) is False

    def test_empty_history_never_lags(self):
        info = {"regularMarketTime": _quote_epoch("2026-10-06 20:00")}

        assert _history_lags_quote(info, pd.DataFrame()) is False
        assert _history_lags_quote(info, pd.DataFrame({"Close": []})) is False


def test_price_comes_from_the_history_the_metrics_are_built_on():
    # The quote is a session ahead of the chart: the row stays on one session,
    # price equal to the bar every trend metric was computed against.
    history = _session_history("2026-10-05")
    data = StockData(ticker="FIVE", current_price=215.93)

    _apply_history_common(data, history)

    close = history["Close"]
    assert data.current_price == float(close.iloc[-1])
    assert data.return_1m == pytest.approx(float(close.iloc[-1] / close.iloc[-22] - 1))
    assert data.daily_change == pytest.approx(float(close.iloc[-1] / close.iloc[-2] - 1))
    assert data.rsi_14 is not None


def test_quote_price_survives_when_there_is_no_history():
    # Market-cap threshold / failed fetch: there is no series to align to.
    data = StockData(ticker="NOHIST", current_price=150.0)

    _apply_history_common(data, pd.DataFrame())

    assert data.current_price == 150.0
    assert data.return_1m is None


def test_fetch_asset_refetches_a_history_that_lags_the_quote(mocker, stock_info):
    stock_info["regularMarketTime"] = _quote_epoch("2026-10-06 20:00")
    stale, fresh = _session_history("2026-10-05"), _session_history("2026-10-06")
    mock = MagicMock()
    mock.info = stock_info
    mock.history.side_effect = [stale, fresh]
    mock.dividends = pd.Series(dtype=float)
    mock.earnings_dates = pd.DataFrame()
    mocker.patch("investdaytip.data_source.yf.Ticker", return_value=mock)

    result = fetch_asset("STALE")

    assert mock.history.call_count == 2  # one recovery fetch
    assert not result.errors
    assert result.current_price == float(fresh["Close"].iloc[-1])
    assert result.return_1m == pytest.approx(
        float(fresh["Close"].iloc[-1] / fresh["Close"].iloc[-22] - 1)
    )


def test_fetch_asset_does_not_refetch_when_quote_and_history_agree(mocker, stock_info):
    stock_info["regularMarketTime"] = _quote_epoch("2026-10-06 20:00")
    mock = _mock_ticker(stock_info, history=_session_history("2026-10-06"))
    mocker.patch("investdaytip.data_source.yf.Ticker", return_value=mock)

    fetch_asset("SYNCED")

    mock.history.assert_called_once()


def test_fetch_asset_stays_self_consistent_when_the_refetch_is_still_stale(mocker, stock_info):
    # Yahoo can serve the same stale chart twice: the row then reports the
    # session it actually has — price and metrics together, never mixed.
    stock_info["regularMarketTime"] = _quote_epoch("2026-10-06 20:00")
    stale = _session_history("2026-10-05")
    mock = MagicMock()
    mock.info = stock_info
    mock.history.side_effect = [stale, stale]
    mock.dividends = pd.Series(dtype=float)
    mock.earnings_dates = pd.DataFrame()
    mocker.patch("investdaytip.data_source.yf.Ticker", return_value=mock)

    result = fetch_asset("STILLSTALE")

    assert mock.history.call_count == 2
    assert result.current_price == float(stale["Close"].iloc[-1])
    assert result.return_1m == pytest.approx(
        float(stale["Close"].iloc[-1] / stale["Close"].iloc[-22] - 1)
    )
