"""Tests for data_source_yahooquery — mocked yahooquery, no live network."""

from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from investdaytip.data_source import EtfData, StockData
from investdaytip.data_source_yahooquery import (
    _MODULE_KEY_MAP,
    _get_nested,
    _get_str,
    _yq_dividend_history,
    _yq_earnings_to_eps_surprise,
    _yq_history_to_dataframe,
    _yq_modules_to_info,
    check_yahooquery_available,
    fetch_asset_yq,
    fetch_batch_yq,
    fetch_index_yq,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_yq_modules(
    quote_type: str = "EQUITY",
    trailing_pe: float | None = 25.0,
    forward_pe: float | None = 22.0,
    price_to_book: float | None = 5.0,
    peg_ratio: float | None = 1.5,
    return_on_equity: float | None = 0.20,
    return_on_assets: float | None = 0.15,
    profit_margins: float | None = 0.25,
    earnings_growth: float | None = 0.10,
    revenue_growth: float | None = 0.08,
    debt_to_equity: float | None = 79.5,
    current_ratio: float | None = 1.2,
    free_cashflow: float | None = 1e10,
    dividend_yield: float | None = 0.005,
    payout_ratio: float | None = 0.25,
    market_cap: float | None = 1e12,
    current_price: float | None = 150.0,
    regular_market_price: float | None = 150.0,
    short_name: str | None = "Apple Inc.",
    long_name: str | None = "Apple Inc.",
    sector: str | None = "Technology",
    currency: str | None = "USD",
    exchange: str | None = "NMS",
    # ETF overrides
    total_assets: float | None = None,
    expense_ratio: float | None = None,
    three_year_return: float | None = None,
    five_year_return: float | None = None,
    beta_3y: float | None = None,
    yield_: float | None = None,
    nav_price: float | None = None,
    category: str | None = None,
    fund_family: str | None = None,
    earnings: dict | None = None,
) -> dict:
    """Build a yahooquery ``all_modules`` response for a single ticker."""
    modules: dict = {}
    modules["quoteType"] = {
        "quoteType": quote_type,
        "shortName": short_name,
        "longName": long_name,
        "exchange": exchange,
    }
    modules["summaryDetail"] = {
        "trailingPE": trailing_pe,
        "forwardPE": forward_pe,
        "dividendYield": dividend_yield,
        "payoutRatio": payout_ratio,
        "marketCap": market_cap,
        "regularMarketPrice": regular_market_price,
        "previousClose": regular_market_price,
        "currency": currency,
        "yield": yield_,
        "totalAssets": total_assets,
        "trailingAnnualDividendYield": yield_,
        "navPrice": nav_price,
        "beta": beta_3y,
    }
    modules["defaultKeyStatistics"] = {
        "priceToBook": price_to_book,
        "pegRatio": peg_ratio,
        "profitMargins": profit_margins,
        "category": category,
        "fundFamily": fund_family,
        "beta3Year": beta_3y,
        "threeYearAverageReturn": three_year_return,
        "fiveYearAverageReturn": five_year_return,
        "netExpenseRatio": expense_ratio,
        "trailingPegRatio": peg_ratio,
    }
    modules["financialData"] = {
        "returnOnEquity": return_on_equity,
        "returnOnAssets": return_on_assets,
        "earningsGrowth": earnings_growth,
        "revenueGrowth": revenue_growth,
        "debtToEquity": debt_to_equity,
        "currentRatio": current_ratio,
        "freeCashflow": free_cashflow,
        "currentPrice": current_price,
    }
    modules["summaryProfile"] = {"sector": sector}
    modules["fundProfile"] = {
        "annualReportExpenseRatio": expense_ratio,
    }
    if earnings:
        modules["earnings"] = earnings
    return modules


def _build_yq_history(
    ticker: str,
    days: int = 300,
    start_price: float = 100.0,
    end_price: float = 130.0,
    single_index: bool = False,
) -> pd.DataFrame:
    """Build a yahooquery-style history DataFrame.

    By default returns a multi-index ``(symbol, date)`` DataFrame.
    Pass ``single_index=True`` to get a plain date-indexed frame (some
    yahooquery endpoints return this for a single ticker).
    """
    dates = pd.date_range(end=pd.Timestamp.now(), periods=days, freq="D")
    prices = np.linspace(start_price, end_price, days)
    if single_index:
        return pd.DataFrame(
            {
                "open": prices * 0.99,
                "high": prices * 1.01,
                "low": prices * 0.98,
                "close": prices,
                "adjclose": prices,
                "volume": [1_000_000] * days,
            },
            index=dates,
        )
    # MultiIndex: assign values as plain arrays so pandas does not align by index
    idx = pd.MultiIndex.from_product([[ticker], dates], names=["symbol", "date"])
    return pd.DataFrame(
        {
            "open": prices * 0.99,
            "high": prices * 1.01,
            "low": prices * 0.98,
            "close": prices,
            "adjclose": prices,
            "volume": [1_000_000] * days,
        },
        index=idx,
    )


def _build_yq_earning_history(ticker: str, surprises: list[float]) -> pd.DataFrame:
    """Build a yahooquery-style earning_history DataFrame."""
    today = pd.Timestamp.now().normalize()
    dates = pd.date_range(end=today, periods=len(surprises), freq="91D")
    rows = []
    for i, (d, sp) in enumerate(zip(dates, surprises, strict=True)):
        rows.append(
            {
                "maxAge": 1,
                "epsActual": 1.0 + i * 0.1,
                "epsEstimate": 1.0,
                "epsDifference": i * 0.1,
                "surprisePercent": sp,
                "quarter": d,
                "currency": "USD",
                "period": f"-{len(surprises)-i}q",
            }
        )
    df = pd.DataFrame(rows)
    df.index = pd.MultiIndex.from_product([[ticker], range(len(rows))], names=["symbol", "row"])
    return df


def _build_yq_dividend_history(ticker: str, dividends: list[float]) -> pd.DataFrame:
    """Build a yahooquery-style dividend_history DataFrame."""
    today = pd.Timestamp.now().normalize()
    dates = pd.date_range(end=today, periods=len(dividends), freq="91D")
    df = pd.DataFrame(
        {"dividends": dividends},
        index=pd.MultiIndex.from_product([[ticker], dates], names=["symbol", "date"]),
    )
    return df


# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------


class TestGetNested:
    def test_returns_float_when_present(self):
        assert _get_nested({"mod": {"key": 42.5}}, "mod", "key") == 42.5

    def test_returns_none_when_missing(self):
        assert _get_nested({}, "mod", "key") is None
        assert _get_nested({"mod": {}}, "mod", "key") is None

    def test_returns_none_for_non_finite(self):
        assert _get_nested({"mod": {"key": float("nan")}}, "mod", "key") is None
        assert _get_nested({"mod": {"key": float("inf")}}, "mod", "key") is None

    def test_returns_none_for_non_numeric(self):
        assert _get_nested({"mod": {"key": "hello"}}, "mod", "key") is None


class TestGetStr:
    def test_returns_string_when_present(self):
        assert _get_str({"mod": {"key": "hello"}}, "mod", "key") == "hello"

    def test_returns_none_when_missing(self):
        assert _get_str({}, "mod", "key") is None


class TestYqModulesToInfo:
    def test_stock_fields_mapped(self):
        modules = _build_yq_modules()
        info = _yq_modules_to_info(modules)
        assert info["trailingPE"] == 25.0
        assert info["forwardPE"] == 22.0
        assert info["priceToBook"] == 5.0
        assert info["pegRatio"] == 1.5
        assert info["returnOnEquity"] == 0.20
        assert info["returnOnAssets"] == 0.15
        assert info["profitMargins"] == 0.25
        assert info["earningsGrowth"] == 0.10
        assert info["revenueGrowth"] == 0.08
        assert info["debtToEquity"] == 79.5
        assert info["currentRatio"] == 1.2
        assert info["freeCashflow"] == 1e10
        assert info["dividendYield"] == 0.005
        assert info["payoutRatio"] == 0.25
        assert info["marketCap"] == 1e12
        assert info["currentPrice"] == 150.0
        assert info["regularMarketPrice"] == 150.0
        assert info["shortName"] == "Apple Inc."
        assert info["longName"] == "Apple Inc."
        assert info["sector"] == "Technology"
        assert info["currency"] == "USD"
        assert info["exchange"] == "NMS"
        assert info["quoteType"] == "EQUITY"

    def test_etf_fields_mapped(self):
        modules = _build_yq_modules(
            quote_type="ETF",
            total_assets=1e11,
            expense_ratio=0.0003,
            three_year_return=0.12,
            five_year_return=0.14,
            beta_3y=1.0,
            yield_=0.015,
            nav_price=100.0,
            category="Large Blend",
            fund_family="Vanguard",
        )
        info = _yq_modules_to_info(modules)
        assert info["quoteType"] == "ETF"
        assert info["totalAssets"] == 1e11
        assert info["netExpenseRatio"] == 0.0003
        assert info["threeYearAverageReturn"] == 0.12
        assert info["fiveYearAverageReturn"] == 0.14
        assert info["beta3Year"] == 1.0
        assert info["yield"] == 0.015
        assert info["navPrice"] == 100.0
        assert info["category"] == "Large Blend"
        assert info["fundFamily"] == "Vanguard"

    def test_etf_expense_ratio_from_fees_expenses_investment(self):
        """yahooquery nests the ETF expense ratio inside fundProfile.feesExpensesInvestment."""
        modules = _build_yq_modules(
            quote_type="ETF",
            total_assets=1e11,
            expense_ratio=None,  # force fallback path
            category="Large Blend",
            fund_family="Vanguard",
        )
        modules["fundProfile"] = {
            "feesExpensesInvestment": {"annualReportExpenseRatio": 0.0003}
        }
        info = _yq_modules_to_info(modules)
        # Should be multiplied by 100 to match yfinance's percentage format.
        assert info["annualReportExpenseRatio"] == pytest.approx(0.03)

    def test_debt_to_equity_preserved(self):
        """debtToEquity is passed through unchanged to match yfinance current behavior."""
        modules = _build_yq_modules(debt_to_equity=79.5)
        info = _yq_modules_to_info(modules)
        assert info["debtToEquity"] == 79.5

    def test_debt_to_equity_high_value_preserved(self):
        """Values already in percentage form must not be modified."""
        modules = _build_yq_modules(debt_to_equity=7950.0)
        info = _yq_modules_to_info(modules)
        assert info["debtToEquity"] == 7950.0


class TestYqEarningsToEpsSurprise:
    def test_averages_surprise_pct(self):
        earnings = {
            "earningsChart": {
                "quarterly": [
                    {"surprisePct": 5.0},
                    {"surprisePct": 10.0},
                    {"surprisePct": 15.0},
                ]
            }
        }
        assert _yq_earnings_to_eps_surprise(earnings) == pytest.approx(10.0)

    def test_returns_none_for_missing_data(self):
        assert _yq_earnings_to_eps_surprise({}) is None
        assert _yq_earnings_to_eps_surprise({"foo": "bar"}) is None


class TestYqHistoryToDataFrame:
    def test_converts_multi_index(self):
        raw = _build_yq_history("AAPL", days=10)
        df = _yq_history_to_dataframe(raw, "AAPL")
        assert "Close" in df.columns
        assert len(df) == 10
        assert df.index.name == "date"

    def test_empty_returns_empty(self):
        df = _yq_history_to_dataframe(pd.DataFrame(), "AAPL")
        assert df.empty

    def test_single_index_preserved(self):
        raw = _build_yq_history("AAPL", days=5, single_index=True)
        df = _yq_history_to_dataframe(raw, "AAPL")
        assert "Close" in df.columns


# ---------------------------------------------------------------------------
# Integration tests for fetch_batch_yq
# ---------------------------------------------------------------------------


def test_fetch_batch_yq_stock(mocker):
    modules = {
        "AAPL": _build_yq_modules(
            quote_type="EQUITY",
            trailing_pe=30.0,
            earnings={
                "earningsChart": {
                    "quarterly": [
                        {"surprisePct": 5.0},
                        {"surprisePct": 5.0},
                        {"surprisePct": 5.0},
                        {"surprisePct": 5.0},
                    ]
                }
            },
        )
    }
    history = _build_yq_history("AAPL", days=300)
    earning_hist = _build_yq_earning_history("AAPL", [5.0, 5.0, 5.0, 5.0])
    dividend_hist = _build_yq_dividend_history("AAPL", [0.25, 0.25, 0.25, 0.25])

    mock_ticker = MagicMock()
    mock_ticker.all_modules = modules
    mock_ticker.history.return_value = history
    mock_ticker.earning_history = earning_hist
    mock_ticker.dividend_history.return_value = dividend_hist

    mocker.patch("investdaytip.data_source_yahooquery.Ticker", return_value=mock_ticker)

    results = fetch_batch_yq(["AAPL"])
    assert "AAPL" in results
    data = results["AAPL"]
    assert isinstance(data, StockData)
    assert data.ticker == "AAPL"
    assert data.trailing_pe == 30.0
    assert data.current_price is not None
    assert data.return_12m is not None
    assert data.eps_surprise == pytest.approx(5.0)


def test_fetch_batch_yq_etf(mocker):
    modules = {
        "VOO": _build_yq_modules(
            quote_type="ETF",
            total_assets=1e11,
            expense_ratio=0.0003,
            category="Large Blend",
            fund_family="Vanguard",
        )
    }
    history = _build_yq_history("VOO", days=300)

    mock_ticker = MagicMock()
    mock_ticker.all_modules = modules
    mock_ticker.history.return_value = history
    mock_ticker.earning_history = None
    mock_ticker.dividend_history.return_value = None

    mocker.patch("investdaytip.data_source_yahooquery.Ticker", return_value=mock_ticker)

    results = fetch_batch_yq(["VOO"])
    assert "VOO" in results
    data = results["VOO"]
    assert isinstance(data, EtfData)
    assert data.ticker == "VOO"
    assert data.total_assets == 1e11
    assert data.expense_ratio == 0.0003
    assert data.category == "Large Blend"
    assert data.fund_family == "Vanguard"


def test_fetch_batch_yq_invalid_ticker(mocker):
    modules = {"INVALID": "Quote not found for symbol: INVALID"}
    mock_ticker = MagicMock()
    mock_ticker.all_modules = modules
    mock_ticker.history.return_value = pd.DataFrame()
    mock_ticker.earning_history = None
    mock_ticker.dividend_history.return_value = None

    mocker.patch("investdaytip.data_source_yahooquery.Ticker", return_value=mock_ticker)

    results = fetch_batch_yq(["INVALID"])
    assert "INVALID" in results
    data = results["INVALID"]
    assert isinstance(data, StockData)
    assert data.errors
    assert "yahooquery" in data.errors[0]


def test_fetch_batch_yq_empty_list():
    results = fetch_batch_yq([])
    assert results == {}


def test_fetch_batch_yq_batch_failure_falls_back_to_all(mocker):
    """If the entire batch fails, every ticker should be marked as failed."""
    mocker.patch(
        "investdaytip.data_source_yahooquery.Ticker",
        side_effect=RuntimeError("network error"),
    )
    results = fetch_batch_yq(["AAPL", "MSFT"])
    assert len(results) == 2
    for tk in ["AAPL", "MSFT"]:
        assert results[tk].errors
        # Exception may be captured as "chunk all_modules failed" or propagated
        err = results[tk].errors[0].lower()
        assert "chunk" in err or "network error" in err


# ---------------------------------------------------------------------------
# fetch_asset_yq
# ---------------------------------------------------------------------------


def test_fetch_asset_yq_parity_with_fetch_batch(mocker):
    modules = {"AAPL": _build_yq_modules(quote_type="EQUITY")}
    history = _build_yq_history("AAPL", days=300)
    mock_ticker = MagicMock()
    mock_ticker.all_modules = modules
    mock_ticker.history.return_value = history
    mock_ticker.earning_history = None
    mock_ticker.dividend_history.return_value = None

    mocker.patch("investdaytip.data_source_yahooquery.Ticker", return_value=mock_ticker)

    result = fetch_asset_yq("AAPL")
    assert isinstance(result, StockData)
    assert result.ticker == "AAPL"


# ---------------------------------------------------------------------------
# fetch_index_yq
# ---------------------------------------------------------------------------


def test_fetch_index_yq_returns_latest_close(mocker):
    history = _build_yq_history("^VIX", days=5, start_price=20.0, end_price=22.0, single_index=True)
    mock_ticker = MagicMock()
    mock_ticker.history.return_value = history
    mocker.patch("investdaytip.data_source_yahooquery.Ticker", return_value=mock_ticker)

    val = fetch_index_yq("^VIX")
    assert val is not None
    assert val == pytest.approx(22.0)


def test_fetch_index_yq_none_on_empty(mocker):
    mock_ticker = MagicMock()
    mock_ticker.history.return_value = pd.DataFrame()
    mocker.patch("investdaytip.data_source_yahooquery.Ticker", return_value=mock_ticker)

    val = fetch_index_yq("^VIX")
    assert val is None


def test_check_yahooquery_available_true(mocker):
    mock_ticker = MagicMock()
    mock_ticker.history.return_value = _build_yq_history("SPY", days=1)
    mocker.patch("investdaytip.data_source_yahooquery.Ticker", return_value=mock_ticker)
    assert check_yahooquery_available() is True


def test_check_yahooquery_available_false(mocker):
    mock_ticker = MagicMock()
    mock_ticker.history.return_value = pd.DataFrame()
    mocker.patch("investdaytip.data_source_yahooquery.Ticker", return_value=mock_ticker)
    assert check_yahooquery_available() is False


# ---------------------------------------------------------------------------
# _yq_dividend_history
# ---------------------------------------------------------------------------


def test_yq_dividend_history_returns_series(mocker):
    divs = _build_yq_dividend_history("AAPL", [0.25, 0.25, 0.25])
    mock_ticker = MagicMock()
    mock_ticker.dividend_history.return_value = divs
    mocker.patch("investdaytip.data_source_yahooquery.Ticker", return_value=mock_ticker)

    result = _yq_dividend_history("AAPL", start="2024-01-01")
    assert result is not None
    assert len(result) == 3


def test_yq_dividend_history_none_on_empty(mocker):
    mock_ticker = MagicMock()
    mock_ticker.dividend_history.return_value = pd.DataFrame()
    mocker.patch("investdaytip.data_source_yahooquery.Ticker", return_value=mock_ticker)

    result = _yq_dividend_history("AAPL")
    assert result is None


# ---------------------------------------------------------------------------
# Field mapping completeness
# ---------------------------------------------------------------------------


def test_all_module_key_map_modules_exist():
    """Verify that every module referenced in _MODULE_KEY_MAP exists in a
    generated yahooquery response."""
    modules = _build_yq_modules()
    for _flat_key, (module, key) in _MODULE_KEY_MAP.items():
        assert module in modules, f"Module {module} missing from test builder"
        assert key in modules[module], f"Key {key} missing from module {module}"


# ---------------------------------------------------------------------------
# Cache roundtrip
# ---------------------------------------------------------------------------


def test_fetch_batch_yq_cache_roundtrip_preserves_eps_surprise(mocker, enabled_temp_cache):
    """eps_surprise must survive a cache round-trip (regression test)."""
    modules = {
        "AAPL": _build_yq_modules(
            quote_type="EQUITY",
            trailing_pe=30.0,
            earnings={
                "earningsChart": {
                    "quarterly": [
                        {"surprisePct": 5.0},
                        {"surprisePct": 5.0},
                        {"surprisePct": 5.0},
                        {"surprisePct": 5.0},
                    ]
                }
            },
        )
    }
    history = _build_yq_history("AAPL", days=300)

    mock_ticker = MagicMock()
    mock_ticker.all_modules = modules
    mock_ticker.history.return_value = history
    mock_ticker.earning_history = None
    mock_ticker.dividend_history.return_value = None

    mocker.patch("investdaytip.data_source_yahooquery.Ticker", return_value=mock_ticker)

    # First call — fresh fetch
    results1 = fetch_batch_yq(["AAPL"])
    data1 = results1["AAPL"]
    assert isinstance(data1, StockData)
    assert data1.eps_surprise == pytest.approx(5.0)

    # Second call — should read from cache, not from the mock
    mock_ticker.all_modules = {}  # would break if we hit the network again
    results2 = fetch_batch_yq(["AAPL"])
    data2 = results2["AAPL"]
    assert isinstance(data2, StockData)
    assert data2.eps_surprise == pytest.approx(5.0)
    assert data2.trailing_pe == pytest.approx(30.0)
