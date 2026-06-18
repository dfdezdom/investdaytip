"""Tests for the historical backtesting engine.

Pure function tests and an integration test with mocked yfinance.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from investdaytip.backtest import (
    BacktestResult,
    _balance_sheet_value,
    _build_historical_stock_data,
    _col_before,
    _compute_historical_eps_surprise,
    _compute_metrics,
    _forward_return,
    _generate_snapshot_dates,
    _interpret_backtest,
    _latest_available_quarter,
    _latest_value_before,
    _max_drawdown,
    _prev_quarter_end,
    _quarter_end,
    _sharpe_ratio,
    _value_n_years_before,
    run_backtest,
)
from investdaytip.data_source import StockData

# ---------------------------------------------------------------------------
#  Helpers — build a financial DataFrame with DatetimeIndex columns and
#  metric-name rows, matching yfinance's layout
# ---------------------------------------------------------------------------


def _fin_df(rows: dict[str, list[float]], dates: list[str]) -> pd.DataFrame:
    """Build a DataFrame with *rows* as index labels and *dates* as columns."""
    dti = pd.DatetimeIndex(dates)
    return pd.DataFrame(
        np.array(list(rows.values())),
        index=list(rows.keys()),
        columns=dti,
    )


# =========================================================================
# Date helpers
# =========================================================================


class TestQuarterEnd:
    def test_march(self):
        assert _quarter_end(datetime(2024, 3, 15)) == datetime(2024, 3, 31)

    def test_june(self):
        assert _quarter_end(datetime(2024, 5, 1)) == datetime(2024, 6, 30)

    def test_september(self):
        assert _quarter_end(datetime(2024, 8, 20)) == datetime(2024, 9, 30)

    def test_december(self):
        assert _quarter_end(datetime(2024, 11, 5)) == datetime(2024, 12, 31)

    def test_year_boundary(self):
        assert _quarter_end(datetime(2025, 1, 10)) == datetime(2025, 3, 31)


class TestPrevQuarterEnd:
    def test_goes_back_one_quarter(self):
        assert _prev_quarter_end(datetime(2024, 6, 30)) == datetime(2024, 3, 31)

    def test_from_mid_quarter(self):
        assert _prev_quarter_end(datetime(2024, 5, 15)) == datetime(2024, 3, 31)

    def test_year_crossing(self):
        assert _prev_quarter_end(datetime(2024, 1, 15)) == datetime(2023, 12, 31)


class TestLatestAvailableQuarter:
    def test_within_lag_uses_previous(self):
        # Snapshot May 1, lag 60 → cutoff Mar 2 → latest quarter-end ≤ Mar 2 → Dec 31
        q = _latest_available_quarter(datetime(2024, 5, 1), lag_days=60)
        assert q == datetime(2023, 12, 31)

    def test_beyond_lag_uses_current(self):
        # Snapshot Jun 1, lag 60 → cutoff Apr 2 → latest quarter-end ≤ Apr 2 → Mar 31
        q = _latest_available_quarter(datetime(2024, 6, 1), lag_days=60)
        assert q == datetime(2024, 3, 31)

    def test_long_lag(self):
        # Jun 15 - 90d = Mar 17 → latest quarter-end ≤ Mar 17 = Dec 31
        q = _latest_available_quarter(datetime(2024, 6, 15), lag_days=90)
        assert q == datetime(2023, 12, 31)

    def test_short_lag(self):
        q = _latest_available_quarter(datetime(2024, 4, 1), lag_days=1)
        assert q == datetime(2024, 3, 31)

    def test_very_early_date_returns_none(self):
        q = _latest_available_quarter(datetime(2000, 1, 1), lag_days=60)
        assert q is None


class TestGenerateSnapshotDates:
    def test_generates_quarterly_dates(self):
        end = datetime(2024, 6, 1)
        start = datetime(2023, 6, 1)
        dates = _generate_snapshot_dates(end, start, interval_months=3)
        assert len(dates) >= 4
        assert dates[0] >= start
        assert dates[-1] <= end

    def test_intervals_respected(self):
        end = datetime(2024, 12, 1)
        start = datetime(2024, 1, 1)
        dates = _generate_snapshot_dates(end, start, interval_months=3)
        assert 3 <= len(dates) <= 5
        for i in range(1, len(dates)):
            diff = (dates[i].month - dates[i - 1].month) % 12
            assert diff == 3 or diff == 0


# =========================================================================
# Financial data extraction
# =========================================================================


@pytest.fixture
def sample_income_stmt() -> pd.DataFrame:
    """Uses yfinance yearly row-name conventions (spaces)."""
    return _fin_df(
        {
            "Net Income": [100, 90, 95, 85, 80, 75, 78, 70],
            "Total Revenue": [1000, 950, 980, 920, 880, 850, 860, 820],
            "Basic EPS": [1.0, 0.9, 0.95, 0.85, 0.8, 0.75, 0.78, 0.7],
        },
        dates=[
            "2024-12-31", "2024-09-30", "2024-06-30", "2024-03-31",
            "2023-12-31", "2023-09-30", "2023-06-30", "2023-03-31",
        ],
    )


@pytest.fixture
def sample_balance_sheet() -> pd.DataFrame:
    """Uses yfinance yearly row-name conventions (spaces)."""
    return _fin_df(
        {
            "Stockholders Equity": [1000, 980, 950, 920],
            "Total Debt": [300, 290, 280, 270],
            "Current Assets": [500, 480, 470, 450],
            "Current Liabilities": [200, 190, 185, 180],
            "Ordinary Shares Number": [100, 100, 100, 100],
        },
        dates=["2024-12-31", "2024-09-30", "2024-06-30", "2024-03-31"],
    )


class TestColBefore:
    def test_finds_exact(self):
        df = _fin_df({"a": [1, 2, 3]}, dates=["2024-12-31", "2024-09-30", "2024-06-30"])
        assert _col_before(df, datetime(2024, 10, 1)) == pd.Timestamp("2024-09-30")

    def test_returns_none_when_before_all(self):
        df = _fin_df({"a": [1]}, dates=["2024-06-30"])
        assert _col_before(df, datetime(2024, 1, 1)) is None

    def test_returns_none_when_empty(self):
        assert _col_before(pd.DataFrame(), datetime(2024, 6, 1)) is None


class TestLatestValueBefore:
    """``_latest_value_before`` returns the value from the most recent fiscal
    year (column) on or before the given date — **annual** data, not TTM."""

    @pytest.fixture
    def fy_income(self):
        return _fin_df({
            "Net Income": [80, 70],
            "Total Revenue": [850, 800],
            "Basic EPS": [0.8, 0.7],
        }, dates=["2023-12-31", "2022-12-31"])

    def test_returns_latest_fy_value(self, fy_income):
        val = _latest_value_before(fy_income, datetime(2024, 3, 31), "NetIncome")
        assert val == 80

    def test_returns_older_fy_when_before(self, fy_income):
        val = _latest_value_before(fy_income, datetime(2023, 6, 1), "NetIncome")
        assert val == 70

    def test_returns_none_for_missing_key(self, fy_income):
        assert _latest_value_before(fy_income, datetime(2024, 3, 31), "NonExistent") is None

    def test_returns_none_when_no_data(self):
        df = pd.DataFrame()
        assert _latest_value_before(df, datetime(2024, 1, 1), "NetIncome") is None


class TestValueNYearsBefore:
    @pytest.fixture
    def fy_income(self):
        return _fin_df({
            "Net Income": [100, 80, 70, 60],
        }, dates=["2024-12-31", "2023-12-31", "2022-12-31", "2021-12-31"])

    def test_one_year_before(self, fy_income):
        val = _value_n_years_before(fy_income, datetime(2025, 3, 31), "NetIncome", n=1)
        assert val == 80

    def test_two_years_before(self, fy_income):
        val = _value_n_years_before(fy_income, datetime(2025, 3, 31), "NetIncome", n=2)
        assert val == 70

    def test_beyond_data_returns_none(self, fy_income):
        assert _value_n_years_before(fy_income, datetime(2025, 3, 31), "NetIncome", n=10) is None

    def test_returns_none_for_missing_key(self, fy_income):
        assert _value_n_years_before(fy_income, datetime(2025, 3, 31), "NonExistent") is None


class TestBalanceSheetValue:
    def test_exact_date(self, sample_balance_sheet):
        val = _balance_sheet_value(sample_balance_sheet, datetime(2024, 12, 31), "StockholdersEquity")
        assert val == 1000

    def test_before_date(self, sample_balance_sheet):
        val = _balance_sheet_value(sample_balance_sheet, datetime(2024, 11, 15), "StockholdersEquity")
        assert val == 980

    def test_missing_key(self, sample_balance_sheet):
        assert _balance_sheet_value(sample_balance_sheet, datetime(2024, 12, 31), "NonExistent") is None


# =========================================================================
# Forward return
# =========================================================================


class TestForwardReturn:
    @pytest.fixture
    def price_series(self):
        dates = pd.date_range("2024-01-01", "2025-06-30", freq="D")
        np.random.seed(42)
        prices = 100 * np.exp(np.cumsum(np.random.normal(0.0005, 0.01, len(dates))))
        return pd.DataFrame({"Close": prices}, index=dates)

    def test_forward_6m(self, price_series):
        assert isinstance(_forward_return(price_series, datetime(2024, 1, 15), 6), float)

    def test_forward_12m(self, price_series):
        assert isinstance(_forward_return(price_series, datetime(2024, 1, 15), 12), float)

    def test_empty_history(self):
        assert _forward_return(pd.DataFrame(), datetime(2024, 1, 1), 6) is None

    def test_no_close_column(self):
        assert _forward_return(pd.DataFrame({"Open": [1, 2, 3]}), datetime(2024, 1, 1), 6) is None


# =========================================================================
# Metrics computation
# =========================================================================


class TestSharpeRatio:
    def test_positive_returns(self):
        assert _sharpe_ratio([0.05, 0.04, 0.06, 0.03], periods_per_year=2) > 0

    def test_negative_returns(self):
        assert _sharpe_ratio([-0.05, -0.04, -0.06], periods_per_year=2) < 0

    def test_few_returns(self):
        assert _sharpe_ratio([0.05], periods_per_year=2) == 0.0


class TestMaxDrawdown:
    def test_no_decline(self):
        assert _max_drawdown([1.0, 1.05, 1.10, 1.15]) == 0.0

    def test_single_decline(self):
        dd = _max_drawdown([1.0, 1.10, 0.90, 1.05])
        assert abs(dd - 0.1818) < 0.01

    def test_few_values(self):
        assert _max_drawdown([1.0]) == 0.0


class TestComputeMetrics:
    @staticmethod
    def _snap(r6, r12, b6, b12):
        return type("_", (), {
            "avg_return_6m": r6, "avg_return_12m": r12,
            "benchmark_return_6m": b6, "benchmark_return_12m": b12,
        })()

    def test_all_beat_benchmark(self):
        m = _compute_metrics([
            self._snap(0.10, 0.20, 0.05, 0.10),
            self._snap(0.08, 0.15, 0.04, 0.08),
            self._snap(0.12, 0.25, 0.06, 0.12),
        ])
        assert m["win_rate_6m"] == 1.0
        assert m["win_rate_12m"] == 1.0
        assert m["cumulative_return"] > m["benchmark_cumulative_return"]

    def test_none_beats_benchmark(self):
        m = _compute_metrics([
            self._snap(0.02, 0.05, 0.10, 0.20),
            self._snap(0.01, 0.03, 0.08, 0.15),
        ])
        assert m["win_rate_6m"] == 0.0
        assert m["win_rate_12m"] == 0.0

    def test_empty_snapshots(self):
        m = _compute_metrics([])
        assert m["sharpe"] == 0.0
        assert m["cumulative_return"] == 0.0


# =========================================================================
# _build_historical_stock_data
# =========================================================================


class TestBuildHistoricalStockData:
    """Fundamental metrics use **annual** fiscal-year data — the value from the
    most recent fiscal year end on or before the quarter date.

    With quarter_date=2024-03-31 and FY ending 2023-12-31, the latest annual
    data available is for FY 2023 (column 2023-12-31).
    """
    _QD = ["2024-12-31", "2023-12-31", "2022-12-31", "2021-12-31", "2020-12-31"]

    def test_populates_basic_fields(self):
        dates = pd.date_range("2023-01-01", "2024-12-31", freq="D")
        history = pd.DataFrame(
            {"Close": np.linspace(100, 150, len(dates))}, index=dates
        )

        inc = _fin_df({
            "Net Income": [100, 80, 70, 60, 50],
            "Total Revenue": [1000, 850, 800, 750, 700],
            "Basic EPS": [1.0, 0.8, 0.7, 0.6, 0.5],
        }, self._QD)
        bs = _fin_df({
            "Stockholders Equity": [1000, 900, 800, 700, 600],
            "Total Debt": [200, 170, 150, 130, 110],
            "Current Assets": [500, 440, 400, 360, 320],
            "Current Liabilities": [200, 180, 160, 140, 120],
            "Ordinary Shares Number": [100, 100, 100, 100, 100],
        }, self._QD)
        cf = _fin_df({"Free Cash Flow": [50, 40, 35, 30, 25]}, self._QD)

        info = {"shortName": "Test Inc", "sector": "Technology", "currency": "USD"}

        sd = _build_historical_stock_data(
            ticker="TEST",
            info=info,
            price_history=history,
            snapshot_date=datetime(2024, 6, 15),
            balance_sheet=bs,
            income_stmt=inc,
            cash_flow=cf,
            dividends=pd.Series(dtype=float),
            quarter_date=datetime(2024, 3, 31),
        )

        assert isinstance(sd, StockData)
        assert sd.ticker == "TEST"
        assert sd.sector == "Technology"
        assert sd.currency == "USD"
        # Price at snapshot (Jun 15, 2024)
        assert sd.current_price is not None and sd.current_price > 120
        # ROE: NI(2023) = 80 / Equity(2023) = 900 ≈ 0.089
        assert sd.return_on_equity is not None and abs(sd.return_on_equity - 0.089) < 0.01
        # P/E: price ~131 / EPS(2023) = 0.8 → ~164
        assert sd.trailing_pe is not None and 150 <= sd.trailing_pe <= 180
        # P/B: price ~131 / BVPS (900/100 = 9) ≈ 14.6
        assert sd.price_to_book is not None and 12 <= sd.price_to_book <= 16
        # D/E (percentage): 170/900 * 100 ≈ 18.9
        assert sd.debt_to_equity is not None and 16 <= sd.debt_to_equity <= 22
        # Earnings growth: (80-70)/70 ≈ 14.3%
        assert sd.earnings_growth is not None and abs(sd.earnings_growth - 0.143) < 0.02
        # Revenue growth: (850-800)/800 ≈ 6.25%
        assert sd.revenue_growth is not None and abs(sd.revenue_growth - 0.0625) < 0.01

    def test_historical_eps_surprise_ignores_future_reports(self):
        dates = pd.date_range("2023-01-01", "2024-12-31", freq="D")
        history = pd.DataFrame(
            {"Close": np.linspace(100, 150, len(dates))}, index=dates
        )
        info = {"shortName": "Test Inc", "sector": "Technology", "currency": "USD"}

        snapshot = datetime(2024, 6, 15)
        past_report = snapshot - pd.Timedelta(days=30)
        future_report = snapshot + pd.Timedelta(days=30)
        earnings_dates = pd.DataFrame(
            {
                "EPS Estimate": [1.0, 1.0],
                "Reported EPS": [1.1, None],
                "Surprise(%)": [10.0, None],
            },
            index=pd.DatetimeIndex([past_report, future_report]),
        )

        sd = _build_historical_stock_data(
            ticker="TEST",
            info=info,
            price_history=history,
            snapshot_date=snapshot,
            balance_sheet=_fin_df({"Stockholders Equity": [1000]}, ["2023-12-31"]),
            income_stmt=_fin_df(
                {"Net Income": [100], "Total Revenue": [1000], "Basic EPS": [1.0]},
                ["2023-12-31"],
            ),
            cash_flow=_fin_df({"Free Cash Flow": [50]}, ["2023-12-31"]),
            dividends=pd.Series(dtype=float),
            quarter_date=datetime(2023, 12, 31),
            earnings_dates=earnings_dates,
            reporting_lag_days=0,
        )

        assert sd.eps_surprise == pytest.approx(10.0)


class TestComputeHistoricalEpsSurprise:
    def test_only_uses_reports_known_at_snapshot(self):
        today = pd.Timestamp.now().normalize()
        snapshot = (today - pd.Timedelta(days=60)).to_pydatetime()
        dates = pd.DatetimeIndex([
            today - pd.Timedelta(days=120),
            today - pd.Timedelta(days=30),  # after snapshot
        ])
        df = pd.DataFrame(
            {
                "EPS Estimate": [1.0, 1.0],
                "Reported EPS": [1.1, 1.2],
                "Surprise(%)": [10.0, 20.0],
            },
            index=dates,
        )
        result = _compute_historical_eps_surprise(df, snapshot, reporting_lag_days=0)
        assert result == pytest.approx(10.0)


# =========================================================================
# Integration test with mocked yfinance
# =========================================================================


class TestRunBacktest:
    """Full ``run_backtest()`` flow with synthetically mocked data.

    Uses **annual** fiscal-year columns (8 years) matching the yearly
    financial statements now used by ``_fetch_ticker_data``.
    """

    _QD = ["2024-12-31", "2023-12-31", "2022-12-31", "2021-12-31",
           "2020-12-31", "2019-12-31", "2018-12-31", "2017-12-31"]

    @staticmethod
    def _make_history(start: str, end: str, base_price: float = 100.0) -> pd.DataFrame:
        dates = pd.date_range(start, end, freq="D")
        trend = 1 + 0.0002 * np.arange(len(dates)) + 0.005 * np.sin(np.arange(len(dates)) / 63)
        return pd.DataFrame({"Close": base_price * trend}, index=dates)

    def _make_financials(self, ni=100, rev=1000, eq=1000, debt=200, ca=500,
                         cl=200, shares=100, eps=1.0, fcf=50) -> tuple:
        n = len(self._QD)
        return (
            _fin_df({
                "Stockholders Equity": [eq] * n, "Total Debt": [debt] * n,
                "Current Assets": [ca] * n, "Current Liabilities": [cl] * n,
                "Ordinary Shares Number": [shares] * n,
            }, self._QD),
            _fin_df({
                "Net Income": [ni] * n, "Total Revenue": [rev] * n, "Basic EPS": [eps] * n,
            }, self._QD),
            _fin_df({"Free Cash Flow": [fcf] * n}, self._QD),
        )

    def _mock_ticker(self, mocker, ticker: str, history: pd.DataFrame,
                     bs: pd.DataFrame, inc: pd.DataFrame, cf: pd.DataFrame):
        tick = mocker.Mock()
        tick.info = {"shortName": ticker, "sector": "Technology",
                     "currency": "USD", "exchange": "NMS"}
        tick.history.return_value = history
        # Backtest now reads yearly data via properties, not quarterly methods.
        tick.balance_sheet = bs
        tick.income_stmt = inc
        tick.cashflow = cf
        tick.dividends = pd.Series(dtype=float)
        return tick

    def test_returns_backtest_result(self, mocker):
        end = "2025-06-01"
        start = "2020-01-01"
        spy_hist = self._make_history(start, end, 100.0)
        aapl_hist = self._make_history(start, end, 100.0)
        msft_hist = self._make_history(start, end, 100.0)
        bs, inc, cf = self._make_financials()

        mocker.patch(
            "investdaytip.backtest.yf.Ticker",
            side_effect={
                "SPY": self._mock_ticker(mocker, "SPY", spy_hist, bs, inc, cf),
                "AAPL": self._mock_ticker(mocker, "AAPL", aapl_hist, bs, inc, cf),
                "MSFT": self._mock_ticker(mocker, "MSFT", msft_hist, bs, inc, cf),
            }.get,
        )

        result = run_backtest(
            tickers=["AAPL", "MSFT"], top_n=2, period="5y",
            interval_months=6, reporting_lag_days=60,
            min_market_cap=0,
        )

        assert isinstance(result, BacktestResult)
        assert result.total_snapshots >= 3
        assert len(result.snapshots) > 0
        assert result.snapshots[0].picks
        assert result.snapshots[0].picks[0].asset_type == "STOCK"

    def test_empty_universe(self, mocker):
        mocker.patch("investdaytip.backtest.yf.Ticker")
        mocker.patch("investdaytip.backtest._build_universe", return_value=[])
        result = run_backtest(tickers=None, top_n=5)
        assert result.total_snapshots == 0
        assert "Empty" in " ".join(result.errors)

    def test_benchmark_missing_returns_empty(self, mocker):
        mocker.patch(
            "investdaytip.backtest.yf.Ticker",
            side_effect=lambda t: mocker.Mock(
                info={},
                history=mocker.Mock(return_value=pd.DataFrame()),
            ),
        )
        result = run_backtest(tickers=["AAPL"], top_n=5)
        assert result.total_snapshots == 0


# =========================================================================
# _interpret_backtest
# =========================================================================


def _make_result(alpha: float, sharpe: float, bench_sharpe: float, wr12: float) -> BacktestResult:
    return BacktestResult(
        snapshots=[],
        total_snapshots=5,
        cumulative_return=0.5 + alpha,
        benchmark_cumulative_return=0.5,
        alpha=alpha,
        sharpe=sharpe,
        benchmark_sharpe=bench_sharpe,
        win_rate_6m=0.5,
        win_rate_12m=wr12,
        max_drawdown=0.15,
        benchmark_ticker="TEST",
    )


class TestInterpretBacktest:
    def test_positive_alpha(self):
        r = _make_result(alpha=0.025, sharpe=1.2, bench_sharpe=1.0, wr12=0.65)
        text = _interpret_backtest(r)
        assert "positive alpha" in text
        assert "outperforming" in text
        assert "better risk-adjusted" in text
        assert "Consistent" in text

    def test_negative_alpha(self):
        r = _make_result(alpha=-0.03, sharpe=0.5, bench_sharpe=1.0, wr12=0.35)
        text = _interpret_backtest(r)
        assert "failed to outperform" in text
        assert "higher volatility" in text
        assert "Weak" in text

    def test_neutral_alpha(self):
        r = _make_result(alpha=0.005, sharpe=0.9, bench_sharpe=0.9, wr12=0.5)
        text = _interpret_backtest(r)
        assert "no significant advantage" in text
        assert "Near-random" in text
