"""Historical backtesting engine — validates stock scoring using quarterly financial data.

For each quarterly snapshot, fundamental metrics (ROE, P/E, D/E, etc.) are
recomputed from yfinance quarterly financial statements available *at that
point in time*, respecting a configurable reporting lag.  Trend metrics are
computed from the price history slice preceding the snapshot.

Only stocks are supported (no ETFs).
"""

from __future__ import annotations

import calendar
import logging
import math
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from io import StringIO
from typing import Any, Optional

import numpy as np
import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFRateLimitError

from investdaytip.data_source import (
    StockData,
    _compute_eps_surprise,
    _suppress_stderr,
    _technical_indicators,
    _trend_metrics,
)
from investdaytip.recommender import _build_universe
from investdaytip.scoring import ScoredAsset, resolve_include_technical, score_stock

logger = logging.getLogger(__name__)

RISK_FREE_RATE: float = 0.045

_DEFAULT_BENCHMARKS: dict[str, str] = {
    "us": "SPY",
    "eu": "VGK",
    "asia": "AAXJ",
    "superinvestor": "SPY",
    "all": "SPY",
}

# Known benchmark tickers and their asset type for display purposes.
_BENCHMARK_ASSET_TYPES: dict[str, str] = {
    "SPY": "ETF",   # S&P 500
    "VGK": "ETF",   # FTSE Developed Europe All Cap
    "AAXJ": "ETF",  # All Country Asia ex Japan
    "EU50": "Índice",
    "JPY": "Índice",
}


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class BacktestSnapshot:
    date: datetime
    picks: list[ScoredAsset]
    avg_return_6m: Optional[float] = None
    avg_return_12m: Optional[float] = None
    benchmark_return_6m: Optional[float] = None
    benchmark_return_12m: Optional[float] = None


@dataclass
class BacktestResult:
    snapshots: list[BacktestSnapshot]
    total_snapshots: int = 0
    cumulative_return: float = 0.0
    benchmark_cumulative_return: float = 0.0
    sharpe: float = 0.0
    benchmark_sharpe: float = 0.0
    win_rate_6m: float = 0.0
    win_rate_12m: float = 0.0
    max_drawdown: float = 0.0
    alpha: float = 0.0
    errors: list[str] = field(default_factory=list)
    benchmark_ticker: str = ""


# ── Date helpers ──────────────────────────────────────────────────────────────


def _quarter_end(date: datetime) -> datetime:
    """Return the quarter-end date that contains *date*."""
    return (pd.Timestamp(date) + pd.offsets.QuarterEnd(0)).to_pydatetime()


def _prev_quarter_end(date: datetime) -> datetime:
    """Return the previous quarter-end before *date*."""
    return (pd.Timestamp(date) - pd.offsets.QuarterEnd(1)).to_pydatetime()


def _latest_available_quarter(
    snapshot_date: datetime, lag_days: int = 60
) -> Optional[datetime]:
    """Find the latest quarter end whose data would be known by *snapshot_date*.

    Financial results are assumed available *lag_days* after the quarter end.
    Returns ``None`` when no quarter meets the criterion.
    """
    cutoff = snapshot_date - timedelta(days=lag_days)
    if cutoff < datetime(2000, 1, 1):
        return None
    qe = _quarter_end(cutoff)
    if qe > cutoff:
        qe = _prev_quarter_end(cutoff)
    return qe


def _generate_snapshot_dates(
    end: datetime,
    start: datetime | None = None,
    interval_months: int = 3,
) -> list[datetime]:
    """Generate quarterly snapshot dates going backward from *end*."""
    if start is None:
        start = end - timedelta(days=365 * 5)
    dates: list[datetime] = []
    d = end
    while d >= start:
        dates.append(d)
        # Use divmod to handle any interval_months value correctly
        year_offset, month_offset = divmod(d.month - interval_months - 1, 12)
        m = month_offset + 1
        y = d.year + year_offset
        # Preserve end-of-month semantics: if original day exceeds target month's
        # days, use the last day of the target month
        max_day = calendar.monthrange(y, m)[1]
        day = min(d.day, max_day)
        d = d.replace(year=y, month=m, day=day)
    return list(reversed(dates))


# ── Financial-data extraction (uses annual / fiscal-year data) ────────────────

# Mapping from our internal metric names to yfinance yearly statement row labels.
_FY_ROW_NAMES: dict[str, str] = {
    "NetIncome": "Net Income",
    "TotalRevenue": "Total Revenue",
    "BasicEPS": "Basic EPS",
    "StockholdersEquity": "Stockholders Equity",
    "TotalAssets": "Total Assets",
    "TotalDebt": "Total Debt",
    "CurrentAssets": "Current Assets",
    "CurrentLiabilities": "Current Liabilities",
    "OrdinarySharesNumber": "Ordinary Shares Number",
    "FreeCashFlow": "Free Cash Flow",
}


def _fy(key: str) -> str:
    """Map internal metric name to yfinance yearly-statement row label."""
    return _FY_ROW_NAMES.get(key, key)


def _col_before(df: pd.DataFrame, date: datetime) -> Optional[pd.Timestamp]:
    """Return the column in *df* closest to but not after *date*."""
    if df.empty:
        return None
    cols = sorted(c for c in df.columns if not pd.isna(c))
    candidates = [c for c in cols if c <= pd.Timestamp(date)]
    return candidates[-1] if candidates else None


def _latest_value_before(
    df: pd.DataFrame, date: datetime, key: str
) -> Optional[float]:
    """Return the value of row *key* at the most recent fiscal year ≤ *date*."""
    col = _col_before(df, date)
    if col is None:
        return None
    row = _fy(key)
    if row not in df.index:
        return None
    val = df.loc[row, col]
    return float(val) if not pd.isna(val) else None


def _value_n_years_before(
    df: pd.DataFrame, date: datetime, key: str, n: int = 1
) -> Optional[float]:
    """Return the value of row *key* *n* fiscal years before *date*."""
    col = _col_before(df, date)
    if col is None:
        return None
    cols = sorted((c for c in df.columns if not pd.isna(c)), reverse=True)
    try:
        idx = cols.index(col)
    except ValueError:
        return None
    target_idx = idx + n
    if target_idx >= len(cols):
        return None
    row = _fy(key)
    if row not in df.index:
        return None
    val = df.loc[row, cols[target_idx]]
    return float(val) if not pd.isna(val) else None


def _balance_sheet_value(
    bs: pd.DataFrame, date: datetime, key: str
) -> Optional[float]:
    """Single balance-sheet value at (or just before) *date*."""
    return _latest_value_before(bs, date, key)


def _ttm_dividends(
    div_series: pd.Series, from_date: datetime, to_date: datetime
) -> float:
    """Sum dividends between *from_date* and *to_date*."""
    if div_series.empty:
        return 0.0
    idx = div_series.index
    if hasattr(idx, "tz") and idx.tz is not None:
        idx = idx.tz_localize(None)
        div_series = div_series.copy()
        div_series.index = idx
    mask = (idx >= pd.Timestamp(from_date)) & (
        idx < pd.Timestamp(to_date)
    )
    return float(div_series[mask].sum())


# ── Forward return ──────────────────────────────────────────────────────────


def _forward_return(
    history: pd.DataFrame, from_date: datetime, months: int
) -> Optional[float]:
    """Return from *from_date* to *from_date + months*.

    Uses the ``Close`` column of *history*.
    """
    if history is None or history.empty or "Close" not in history:
        return None
    close = history["Close"].dropna()
    if len(close) < 2:
        return None
    try:
        target = from_date + timedelta(days=int(30.5 * months))
        price_start = close.asof(pd.Timestamp(from_date))
        price_end = close.asof(pd.Timestamp(target))
    except Exception:
        return None
    if pd.isna(price_start) or pd.isna(price_end) or price_start <= 0:
        return None
    return float(price_end / price_start) - 1.0


# ── Aggregated metrics ──────────────────────────────────────────────────────


def _compute_metrics(
    snapshots: list[BacktestSnapshot], interval_months: int = 3
) -> dict[str, float]:
    """Compute aggregate metrics from a list of snapshots."""
    if not snapshots:
        return {
            "cumulative_return": 0.0,
            "benchmark_cumulative_return": 0.0,
            "sharpe": 0.0,
            "benchmark_sharpe": 0.0,
            "win_rate_6m": 0.0,
            "win_rate_12m": 0.0,
            "max_drawdown": 0.0,
            "alpha": 0.0,
        }

    # Chain-link returns
    cum = 1.0
    bench_cum = 1.0
    portfolio_returns_6m: list[float] = []
    benchmark_returns_6m: list[float] = []
    portfolio_returns_12m: list[float] = []
    benchmark_returns_12m: list[float] = []
    win_count_6m = 0
    win_count_12m = 0
    total_6m = 0
    total_12m = 0

    cum_values = [1.0]
    bench_values = [1.0]

    for s in snapshots:
        r6 = s.avg_return_6m
        r12 = s.avg_return_12m
        b6 = s.benchmark_return_6m
        b12 = s.benchmark_return_12m

        if r6 is not None and b6 is not None:
            portfolio_returns_6m.append(r6)
            benchmark_returns_6m.append(b6)
            cum *= 1.0 + r6
            bench_cum *= 1.0 + b6
            cum_values.append(cum)
            bench_values.append(bench_cum)
            total_6m += 1
            if r6 > b6:
                win_count_6m += 1

        if r12 is not None and b12 is not None:
            portfolio_returns_12m.append(r12)
            benchmark_returns_12m.append(b12)
            total_12m += 1
            if r12 > b12:
                win_count_12m += 1

    # Sharpe (annualized assuming 6m returns → 2 periods/year)
    sharpe = _sharpe_ratio(portfolio_returns_6m, periods_per_year=2)
    bench_sharpe = _sharpe_ratio(benchmark_returns_6m, periods_per_year=2)

    # Max drawdown
    dd = _max_drawdown(cum_values)

    # Alpha (annualized excess return)
    years = max(len(snapshots) * interval_months / 12.0, 1.0)
    alpha = ((cum / bench_cum) ** (1.0 / years) - 1.0) if bench_cum > 0 else 0.0

    return {
        "cumulative_return": cum - 1.0,
        "benchmark_cumulative_return": bench_cum - 1.0,
        "sharpe": sharpe,
        "benchmark_sharpe": bench_sharpe,
        "win_rate_6m": win_count_6m / max(total_6m, 1),
        "win_rate_12m": win_count_12m / max(total_12m, 1),
        "max_drawdown": dd,
        "alpha": alpha,
    }


def _sharpe_ratio(
    returns: list[float], periods_per_year: float = 2
) -> float:
    if len(returns) < 2:
        return 0.0
    arr = np.array(returns)
    excess = arr - RISK_FREE_RATE / periods_per_year
    mean_excess = float(np.mean(excess))
    std_excess = float(np.std(excess, ddof=1))
    if std_excess == 0:
        return 0.0
    return (mean_excess / std_excess) * math.sqrt(periods_per_year)


def _max_drawdown(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    arr = np.array(values)
    peak = np.maximum.accumulate(arr)
    dd = float(np.min((arr - peak) / peak))
    return abs(dd)


# ── Per-ticker historical data construction ─────────────────────────────────


def _compute_historical_eps_surprise(
    earnings_dates: pd.DataFrame | None,
    snapshot_date: datetime,
    reporting_lag_days: int,
    lookback_quarters: int = 4,
) -> Optional[float]:
    """Average EPS surprise for quarters reported before *snapshot_date*.

    Only earnings reports that would have been public by
    ``snapshot_date - reporting_lag_days`` are considered, preventing
    look-ahead bias in the backtest.
    """
    if earnings_dates is None or earnings_dates.empty:
        return None
    cutoff = snapshot_date - timedelta(days=reporting_lag_days)
    # Normalize index to naive dates for consistent comparisons.
    idx = pd.DatetimeIndex(pd.to_datetime(earnings_dates.index)).tz_localize(None)
    filtered = earnings_dates.loc[idx <= pd.Timestamp(cutoff)].copy()
    if filtered.empty:
        return None
    filtered.index = idx[idx <= pd.Timestamp(cutoff)]
    return _compute_eps_surprise(filtered, lookback_quarters)


def _build_historical_stock_data(
    ticker: str,
    info: dict,
    price_history: pd.DataFrame,
    snapshot_date: datetime,
    balance_sheet: pd.DataFrame,
    income_stmt: pd.DataFrame,
    cash_flow: pd.DataFrame,
    dividends: pd.Series,
    quarter_date: datetime,
    earnings_dates: pd.DataFrame | None = None,
    reporting_lag_days: int = 60,
) -> StockData:
    """Build a ``StockData`` instance using only data available at *snapshot_date*.

    Fundamental metrics are sourced from **annual** (fiscal-year) financial
    statements.  For each metric the value at the most recent fiscal year
    ending on or before *quarter_date* is used.
    """
    # Price at snapshot
    close = price_history["Close"].dropna()
    price = float(close.asof(pd.Timestamp(snapshot_date))) if not close.empty else None

    if pd.isna(price):
        price = None

    # ── Trend metrics from history slice ──
    hist_slice = price_history.loc[:pd.Timestamp(snapshot_date)].copy()
    trend = _trend_metrics(hist_slice)
    price_vs_sma200, return_1m, return_12m, sma200_slope, _vol, daily_change = (
        trend if trend else (None, None, None, None, None, None)
    )
    rsi_14, macd_histogram = _technical_indicators(
        hist_slice["Close"].dropna()
    ) if "Close" in hist_slice else (None, None)

    # ── Fundamental metrics from annual fiscal-year data ──
    ni = _latest_value_before(income_stmt, quarter_date, "NetIncome")
    rev = _latest_value_before(income_stmt, quarter_date, "TotalRevenue")
    eps = _latest_value_before(income_stmt, quarter_date, "BasicEPS")

    # YoY growth (compare with previous fiscal year)
    ni_prev = _value_n_years_before(income_stmt, quarter_date, "NetIncome", n=1)
    rev_prev = _value_n_years_before(income_stmt, quarter_date, "TotalRevenue", n=1)

    earnings_growth = _pct_change(ni, ni_prev)
    revenue_growth = _pct_change(rev, rev_prev)

    equity = _balance_sheet_value(balance_sheet, quarter_date, "StockholdersEquity")
    total_assets = _balance_sheet_value(balance_sheet, quarter_date, "TotalAssets")
    total_debt = _balance_sheet_value(balance_sheet, quarter_date, "TotalDebt")
    curr_assets = _balance_sheet_value(balance_sheet, quarter_date, "CurrentAssets")
    curr_liab = _balance_sheet_value(balance_sheet, quarter_date, "CurrentLiabilities")
    shares = _balance_sheet_value(balance_sheet, quarter_date, "OrdinarySharesNumber")

    fcf = _latest_value_before(cash_flow, quarter_date, "FreeCashFlow")

    # Debt/Equity: yfinance reports as percentage, divide by 100
    debt_to_equity = (
        (total_debt / equity) * 100.0 if (total_debt and equity and equity != 0) else None
    )

    current_ratio = (
        curr_assets / curr_liab if (curr_assets and curr_liab and curr_liab != 0) else None
    )

    # Trailing P/E
    trailing_pe = (price / eps) if (price and eps and eps != 0) else None

    # Price/Book
    bvps = equity / shares if (equity and shares and shares != 0) else None
    price_to_book = (price / bvps) if (price and bvps and bvps != 0) else None

    # ROE / ROA
    roe = (ni / equity) if (ni and equity and equity != 0) else None
    roa = (ni / total_assets) if (ni and total_assets and total_assets != 0) else None

    # Profit margin
    profit_margin = (ni / rev) if (ni and rev and rev != 0) else None

    # Market cap
    market_cap = (price * shares) if (price and shares) else None

    # Dividend yield (TTM)
    ttm_div = _ttm_dividends(
        dividends,
        quarter_date - timedelta(days=365),
        quarter_date,
    )
    dividend_yield = (ttm_div / price) if (price and price > 0) else None

    # Payout ratio
    payout_ratio = (
        (ttm_div / eps) if (ttm_div and eps and eps != 0) else None
    )

    eps_surprise = _compute_historical_eps_surprise(
        earnings_dates, snapshot_date, reporting_lag_days
    )

    return StockData(
        ticker=ticker,
        name=info.get("shortName") or info.get("longName"),
        sector=info.get("sector"),
        currency=info.get("currency"),
        exchange=info.get("exchange"),
        trailing_pe=trailing_pe,
        forward_pe=None,
        price_to_book=price_to_book,
        peg_ratio=None,
        return_on_equity=roe,
        return_on_assets=roa,
        profit_margin=profit_margin,
        earnings_growth=earnings_growth,
        revenue_growth=revenue_growth,
        debt_to_equity=debt_to_equity,
        current_ratio=current_ratio,
        free_cashflow=fcf,
        dividend_yield=dividend_yield,
        payout_ratio=payout_ratio,
        eps_surprise=eps_surprise,
        market_cap=market_cap,
        current_price=price,
        price_vs_sma200=price_vs_sma200,
        return_1m=return_1m,
        return_12m=return_12m,
        sma200_slope=sma200_slope,
        daily_change=daily_change,
        rsi_14=rsi_14,
        macd_histogram=macd_histogram,
    )



def _pct_change(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    if current is None or previous is None or previous == 0:
        return None
    return (current - previous) / abs(previous)


# ── Data fetching ────────────────────────────────────────────────────────────


_TickerData = dict[str, Any]


def _try_fetch_from_cache(ticker: str) -> _TickerData | None:
    """Return cached backtest data for *ticker*, or ``None`` if any component is missing/expired."""
    from investdaytip.cache import (
        cache_dividends_get,
        cache_earnings_dates_get,
        cache_financial_get,
        cache_history_get,
        cache_info_get,
        enabled,
    )
    if not enabled:
        return None

    info_raw = cache_info_get(ticker)
    hist_raw = cache_history_get(ticker)
    bs_raw = cache_financial_get(ticker, "balance_sheet")
    inc_raw = cache_financial_get(ticker, "income_stmt")
    cf_raw = cache_financial_get(ticker, "cash_flow")
    divs_raw = cache_dividends_get(ticker)
    ed_raw = cache_earnings_dates_get(ticker)

    if any(r is None for r in (info_raw, hist_raw, bs_raw, inc_raw, cf_raw, divs_raw, ed_raw)):
        return None

    try:
        info = info_raw
        hist = pd.read_json(StringIO(hist_raw))
        bs = pd.read_json(StringIO(bs_raw))
        inc = pd.read_json(StringIO(inc_raw))
        cf = pd.read_json(StringIO(cf_raw))
        divs = pd.read_json(StringIO(divs_raw), typ="series")
        ed = pd.read_json(StringIO(ed_raw))
    except Exception:
        return None

    return _TickerData(
        ticker=ticker,
        info=info,
        history=hist,
        balance_sheet=bs,
        income_stmt=inc,
        cash_flow=cf,
        dividends=divs,
        earnings_dates=ed,
    )


def _store_fetch_in_cache(ticker: str, data: _TickerData) -> None:
    """Persist each component of *data* in the SQLite cache."""
    from investdaytip.cache import (
        cache_dividends_set,
        cache_earnings_dates_set,
        cache_financial_set,
        cache_history_set,
        cache_info_set,
        enabled,
    )
    if not enabled:
        return

    info = data.get("info")
    if info:
        cache_info_set(ticker, info)

    hist = data.get("history")
    if hist is not None and not hist.empty:
        cache_history_set(ticker, hist.to_json(date_format="iso"))

    for kind in ("balance_sheet", "income_stmt", "cash_flow"):
        df = data.get(kind)
        if df is not None and not df.empty:
            cache_financial_set(ticker, kind, df.to_json(date_format="iso"))

    divs = data.get("dividends")
    if divs is not None and not divs.empty:
        cache_dividends_set(ticker, divs.to_json(date_format="iso"))

    ed = data.get("earnings_dates")
    if ed is not None and not ed.empty:
        if ed.index.duplicated().any():
            ed = ed[~ed.index.duplicated(keep="last")]
        cache_earnings_dates_set(ticker, ed.to_json(date_format="iso"))


def _fetch_ticker_data(
    ticker: str, period: str = "5y", _retries: int = 0
) -> _TickerData:
    """Fetch all data needed for backtesting a single ticker.

    Financial statements are fetched as **annual** (fiscal-year) data to
    cover a longer history (5+ fiscal years) than quarterly reports (which
    yfinance only returns 5 quarters for).

    Results are cached per-component (info, history, financials, dividends,
    earnings_dates) with component-appropriate TTLs so repeated runs skip
    yfinance calls.
    """
    # ── Try cache first ──
    cached = _try_fetch_from_cache(ticker)
    if cached is not None:
        return cached

    result: _TickerData = {"ticker": ticker}
    try:
        t = yf.Ticker(ticker)
        with _suppress_stderr():
            result["info"] = t.info if t.info else {}
            hist = t.history(period=period, auto_adjust=True)
            # Yfinance returns timezone-aware (UTC) DatetimeIndex; strip tz so
            # all internal datetime comparisons are naive (consistent across functions).
            if hist is not None and not hist.empty and hist.index.tz is not None:
                hist.index = hist.index.tz_localize(None)
            result["history"] = hist
            result["balance_sheet"] = t.balance_sheet
            result["income_stmt"] = t.income_stmt
            result["cash_flow"] = t.cashflow
            result["dividends"] = t.dividends
            try:
                result["earnings_dates"] = t.earnings_dates
            except Exception as ed_exc:
                logger.warning("Failed to fetch earnings_dates for %s: %s", ticker, ed_exc)
                result["earnings_dates"] = pd.DataFrame()
            # Strip timezone from financial statement columns so that
            # _col_before comparisons with tz-naive Timestamps work.
            for key in ("balance_sheet", "income_stmt", "cash_flow"):
                df = result.get(key)
                if df is not None and not df.empty and hasattr(df.columns, "tz") and df.columns.tz is not None:
                    df = df.copy()
                    df.columns = df.columns.tz_localize(None)
                    result[key] = df
            # Dividends index can also be tz-aware
            divs = result.get("dividends")
            if divs is not None and not divs.empty and hasattr(divs.index, "tz") and divs.index.tz is not None:
                divs = divs.copy()
                divs.index = divs.index.tz_localize(None)
                result["dividends"] = divs
    except YFRateLimitError:
        if _retries >= 3:
            logger.warning("Rate limit persisted for %s after 3 retries", ticker)
            result["error"] = "Rate limit exceeded"
        else:
            delays = [10, 30, 60]
            time.sleep(delays[min(_retries, len(delays) - 1)])
            return _fetch_ticker_data(ticker, period, _retries + 1)
    except Exception as exc:
        logger.warning("Failed to fetch data for %s: %s", ticker, exc)
        result["error"] = str(exc)

    # ── Store in cache ──
    _store_fetch_in_cache(ticker, result)
    return result


def _fetch_all_data(
    tickers: list[str],
    period: str = "5y",
    max_workers: int = 10,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> dict[str, _TickerData]:
    """Fetch data for all tickers in parallel."""
    all_data: dict[str, _TickerData] = {}
    total = len(tickers)
    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_fetch_ticker_data, t, period): t for t in tickers}
        for future in as_completed(futures):
            t = futures[future]
            try:
                all_data[t] = future.result()
            except Exception as exc:
                logger.warning("Error fetching %s: %s", t, exc)
                all_data[t] = {"ticker": t, "error": str(exc)}
            completed += 1
            if on_progress:
                on_progress(t, completed, total)
    return all_data


# ── Public API ────────────────────────────────────────────────────────────────


def run_backtest(
    tickers: list[str] | None = None,
    *,
    top_n: int = 10,
    period: str = "5y",
    interval_months: int = 3,
    benchmark: str | None = None,
    region: str | list[str] = "us",
    currency: str | list[str] = "USD",
    asset_class: str = "stocks",
    min_market_cap: float = 2_000_000_000,
    reporting_lag_days: int = 60,
    max_workers: int = 10,
    on_progress: Callable[[str, int, int], None] | None = None,
    include_technical: bool | None = None,
    scoring_model: str = "quant",
) -> BacktestResult:
    """Run a historical backtest of the stock scoring model.

    Parameters
    ----------
    tickers:
        Explicit ticker list.  If ``None``, uses the curated universe
        filtered by *asset_class*, *region*, and *currency*.
    top_n:
        Number of top-scoring stocks to pick per snapshot.
    period:
        Lookback period for yfinance (e.g. ``"5y"``, ``"10y"``, ``"max"``).
    interval_months:
        Months between consecutive snapshots.
    benchmark:
        Benchmark ticker.  If ``None``, derived from *region*.
    region:
        Region filter passed to ``_build_universe()``.
    currency:
        Currency filter.
    asset_class:
        Asset-class filter (only ``"stocks"`` is supported).
    min_market_cap:
        Minimum market-cap filter.
    reporting_lag_days:
        Days after quarter-end before financial data is considered available.
    max_workers:
        Parallel fetch workers.
    include_technical:
        Whether to blend RSI/MACD into the score. ``None`` defaults to
        ``True`` for ``"quant"`` and ``False`` for ``"classic"``.
    scoring_model:
        ``"quant"`` (default) or ``"classic"``.

    Returns
    -------
    BacktestResult with per-snapshot picks and aggregate metrics.
    """
    include_technical = resolve_include_technical(include_technical, scoring_model)

    # Backtest requires consistent, up-to-date data across all tickers.
    # Stale cached history can shift the latest-common date and produce
    # different snapshot counts. We therefore disable the cache for the
    # duration of the run and restore it afterwards.
    from investdaytip.cache import enabled as cache_enabled
    from investdaytip.cache import set_enabled as cache_set_enabled
    original_cache_state = cache_enabled
    cache_set_enabled(False)

    errors: list[str] = []

    try:
        # Resolve universe
        universe = (
            list(tickers)
            if tickers
            else _build_universe(None, asset_class, region, currency)
        )
        if not universe:
            return BacktestResult(snapshots=[], errors=["Empty universe"])

        # Resolve benchmark
        if benchmark is None:
            reg = region if isinstance(region, str) else (region[0] if region else "us")
            benchmark = _DEFAULT_BENCHMARKS.get(reg, "SPY")

        # Fetch all data
        all_tickers = list(set(universe + [benchmark]))
        data = _fetch_all_data(all_tickers, period, max_workers, on_progress=on_progress)

        if benchmark not in data or "history" not in data.get(benchmark, {}):
            return BacktestResult(
                snapshots=[], errors=[f"Could not fetch benchmark {benchmark}"]
            )

        benchmark_history = data[benchmark]["history"]

        # Determine date range
        latest_common = _latest_common_end(data)
        if latest_common is None:
            return BacktestResult(snapshots=[], errors=["No historical data available"])

        end_date = latest_common - timedelta(days=reporting_lag_days)
        # Need at least 12 months of history before first snapshot (for trend)
        # and at least 12 months after last snapshot (for forward return)
        start_date = end_date - timedelta(days=int(365.25 * 4.5))
        snap_dates = _generate_snapshot_dates(
            end_date, start_date, interval_months
        )

        # Filter snapshots that are too close to the end (need forward-return room)
        snap_dates = [
            d
            for d in snap_dates
            if d + timedelta(days=int(30.5 * 12)) <= latest_common
        ]

        if not snap_dates:
            return BacktestResult(
                snapshots=[], errors=["Not enough history for any snapshot"]
            )

        snapshots: list[BacktestSnapshot] = []

        for sd in snap_dates:
            quarter_date = _latest_available_quarter(sd, reporting_lag_days)
            if quarter_date is None:
                continue

            scored: list[ScoredAsset] = []
            for t in universe:
                td = data.get(t)
                if td is None or "error" in td:
                    continue
                hist = td.get("history")
                if hist is None or hist.empty:
                    continue
                info = td.get("info", {})
                bs = td.get("balance_sheet", pd.DataFrame())
                inc = td.get("income_stmt", pd.DataFrame())
                cf = td.get("cash_flow", pd.DataFrame())
                divs = td.get("dividends", pd.Series(dtype=float))
                ed = td.get("earnings_dates")

                sd_obj = _build_historical_stock_data(
                    ticker=t,
                    info=info,
                    price_history=hist,
                    snapshot_date=sd,
                    balance_sheet=bs,
                    income_stmt=inc,
                    cash_flow=cf,
                    dividends=divs,
                    quarter_date=quarter_date,
                    earnings_dates=ed,
                    reporting_lag_days=reporting_lag_days,
                )

                # Apply min market cap filter
                if min_market_cap > 0 and (
                    sd_obj.market_cap is None or sd_obj.market_cap < min_market_cap
                ):
                    continue

                scored.append(score_stock(sd_obj, model=scoring_model, include_technical=include_technical))

            scored.sort(key=lambda s: s.total, reverse=True)
            picks = scored[:top_n]

            if not picks:
                continue

            # Forward returns
            r6_list = [
                _forward_return(data[p.data.ticker].get("history"), sd, 6)
                for p in picks
                if p.data.ticker in data
            ]
            r12_list = [
                _forward_return(data[p.data.ticker].get("history"), sd, 12)
                for p in picks
                if p.data.ticker in data
            ]

            r6_valid = [r for r in r6_list if r is not None]
            r12_valid = [r for r in r12_list if r is not None]

            b6 = _forward_return(benchmark_history, sd, 6)
            b12 = _forward_return(benchmark_history, sd, 12)

            snapshots.append(
                BacktestSnapshot(
                    date=sd,
                    picks=picks,
                    avg_return_6m=float(np.mean(r6_valid)) if r6_valid else None,
                    avg_return_12m=float(np.mean(r12_valid)) if r12_valid else None,
                    benchmark_return_6m=b6,
                    benchmark_return_12m=b12,
                )
            )

        metrics = _compute_metrics(snapshots, interval_months)

        return BacktestResult(
            snapshots=snapshots,
            total_snapshots=len(snapshots),
            errors=errors,
            benchmark_ticker=str(benchmark),
            **metrics,
        )
    finally:
        cache_set_enabled(original_cache_state)


def _latest_common_end(data: dict[str, _TickerData]) -> Optional[datetime]:
    """Find the latest date that all tickers have history up to."""
    ends: list[datetime] = []
    for td in data.values():
        hist = td.get("history")
        if hist is None or hist.empty:
            continue
        last = hist.index[-1]
        if isinstance(last, pd.Timestamp):
            ends.append(last.to_pydatetime().replace(tzinfo=None))
    if not ends:
        return None
    # Use the earliest last-date (conservative)
    return min(ends)


def _interpret_backtest(result: BacktestResult) -> str:
    """Generate a human-readable interpretation of backtest results."""
    parts: list[str] = []
    bench = _benchmark_label(result.benchmark_ticker) if result.benchmark_ticker else "benchmark"

    # Alpha
    a = result.alpha
    if a > 0.01:
        parts.append(
            f"The model generated positive alpha ({a*100:.1f}%), outperforming {bench}."
        )
    elif a < -0.01:
        parts.append(
            f"The model failed to outperform {bench} (alpha of {a*100:.1f}%)."
        )
    else:
        parts.append(
            f"The model performed in line with {bench} (alpha of {a*100:.1f}%), "
            "with no significant advantage."
        )

    # Sharpe comparison
    if result.sharpe >= result.benchmark_sharpe:
        parts.append(
            f"With a better risk-adjusted return than {bench} "
            f"(Sharpe {result.sharpe:.2f} vs {result.benchmark_sharpe:.2f})."
        )
    else:
        parts.append(
            f"Though with higher volatility than {bench} "
            f"(Sharpe {result.sharpe:.2f} vs {result.benchmark_sharpe:.2f})."
        )

    # Win rate 12M
    wr = result.win_rate_12m
    if wr > 0.55:
        parts.append(
            f"Consistent 12-month results (beat the benchmark in {wr*100:.0f}% of periods)."
        )
    elif wr < 0.45:
        parts.append(
            f"Weak 12-month consistency "
            f"(beat the benchmark in only {wr*100:.0f}% of periods)."
        )
    else:
        parts.append(
            f"Near-random 12-month results ({wr*100:.0f}%)."
        )

    return " ".join(parts)


def _benchmark_label(ticker: str) -> str:
    """Return a human-friendly label with asset type, e.g. ``"SPY (ETF)"``."""
    ttype = _BENCHMARK_ASSET_TYPES.get(ticker)
    if ttype:
        return f"{ticker} ({ttype})"
    return ticker
