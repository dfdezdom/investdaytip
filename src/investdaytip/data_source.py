"""Data fetching from Yahoo Finance via yfinance.

Wraps yfinance calls and extracts the fundamentals + price history needed
by the scoring engine. Supports both stocks and ETFs (auto-detected via
``quoteType``). All network/parsing errors are caught and surfaced as
``None`` fields so the scorer can degrade gracefully.
"""

from __future__ import annotations

import logging
import math
import os
import time
from contextlib import contextmanager, redirect_stderr
from dataclasses import dataclass, field
from datetime import datetime
from io import StringIO
from typing import Optional, Union

import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFRateLimitError

from investdaytip.financial_health import annual_facts, improvement_flags

# Silence yfinance's verbose logging (delisted symbols, HTTP errors)
for _name in ("yfinance", "yfinance.ticker", "yfinance.utils", "yfinance.data", "peewee"):
    logging.getLogger(_name).setLevel(logging.CRITICAL)


@contextmanager
def _suppress_stderr():
    """Suppress prints yfinance writes directly to stderr (e.g. delisted warnings)."""
    with open(os.devnull, "w") as devnull, redirect_stderr(devnull):
        yield


@dataclass
class StockData:
    ticker: str
    asset_type: str = "STOCK"
    name: Optional[str] = None
    sector: Optional[str] = None
    currency: Optional[str] = None
    exchange: Optional[str] = None
    # Valuation
    trailing_pe: Optional[float] = None
    forward_pe: Optional[float] = None
    price_to_book: Optional[float] = None
    peg_ratio: Optional[float] = None
    # Quality
    return_on_equity: Optional[float] = None
    return_on_assets: Optional[float] = None
    profit_margin: Optional[float] = None
    earnings_growth: Optional[float] = None
    revenue_growth: Optional[float] = None
    # YoY fundamental improvements (statement-derived; None = unknown)
    margin_improving: Optional[bool] = None
    roa_improving: Optional[bool] = None
    # Health
    debt_to_equity: Optional[float] = None
    current_ratio: Optional[float] = None
    free_cashflow: Optional[float] = None
    # Income
    dividend_yield: Optional[float] = None
    payout_ratio: Optional[float] = None
    # Earnings surprises (proxy for EPS revisions)
    eps_surprise: Optional[float] = None
    # EPS growth acceleration (2nd derivative) — derived diagnostic, tested as
    # an EPS-revisions fallback and REJECTED (factor-IC −0.059, 2026-09-27)
    eps_acceleration: Optional[float] = None
    # Market context
    market_cap: Optional[float] = None
    current_price: Optional[float] = None
    # Trend (computed from price history)
    price_vs_sma200: Optional[float] = None
    return_1m: Optional[float] = None
    return_12m: Optional[float] = None
    sma200_slope: Optional[float] = None
    daily_change: Optional[float] = None
    # Technical indicators
    rsi_14: Optional[float] = None
    macd_histogram: Optional[float] = None

    errors: list[str] = field(default_factory=list)


@dataclass
class EtfData:
    ticker: str
    asset_type: str = "ETF"
    name: Optional[str] = None
    category: Optional[str] = None
    fund_family: Optional[str] = None
    currency: Optional[str] = None
    exchange: Optional[str] = None
    total_assets: Optional[float] = None  # AUM in USD
    expense_ratio: Optional[float] = None
    three_year_return: Optional[float] = None
    five_year_return: Optional[float] = None
    beta_3y: Optional[float] = None
    yield_: Optional[float] = None
    current_price: Optional[float] = None
    nav: Optional[float] = None
    # Trend / risk (computed from price history)
    return_1m: Optional[float] = None
    return_3m: Optional[float] = None
    return_6m: Optional[float] = None
    return_12m: Optional[float] = None
    price_vs_sma200: Optional[float] = None
    sma200_slope: Optional[float] = None
    volatility_1y: Optional[float] = None  # annualized
    sharpe_proxy: Optional[float] = None  # (return_12m - rf) / volatility_1y
    daily_change: Optional[float] = None
    # Technical indicators
    rsi_14: Optional[float] = None
    macd_histogram: Optional[float] = None

    errors: list[str] = field(default_factory=list)

    @property
    def sector(self) -> Optional[str]:
        """For uniform rendering — ETF category acts as 'sector'."""
        return self.category

    @property
    def market_cap(self) -> Optional[float]:
        """For uniform filtering — AUM acts as 'market_cap'."""
        return self.total_assets


AssetData = Union[StockData, EtfData]

# US 1-year T-bill approximation for Sharpe proxy
RISK_FREE_RATE = 0.045


def _safe_get(info: dict, key: str) -> Optional[float]:
    val = info.get(key)
    if val is None:
        return None
    try:
        f = float(val)
        if not math.isfinite(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _first(*values: Optional[float]) -> Optional[float]:
    """Return the first value that is not ``None``.

    Unlike an ``or`` chain, a legitimate ``0.0`` is preserved instead of
    being treated as missing.
    """
    for v in values:
        if v is not None:
            return v
    return None


def _sanitize_yield(value: Optional[float]) -> Optional[float]:
    """Normalize yfinance yield fields to a decimal (e.g. 0.054 = 5.4%).

    yfinance is inconsistent across tickers and regions: some report yield as
    a decimal (0.054) and others as an already-multiplied percentage (5.4).
    Treat any value greater than 1.0 as a percentage and divide by 100.
    """
    if value is None or not math.isfinite(value):
        return None
    if value > 1.0:
        return value / 100.0
    return value


def _ttm_dividend_yield(
    dividends: pd.Series | None,
    price: Optional[float],
) -> Optional[float]:
    """Return trailing-twelve-month dividend yield computed from raw dividends.

    yfinance's ``dividendYield`` field is unreliable for many tickers (it can
    be off by 100x or report stale/synthetic values). Summing the dividends
    distributed over the last 365 days and dividing by the current price is
    more accurate. Returns ``None`` when no dividends or no price are available.
    """
    if dividends is None or dividends.empty or price is None or price <= 0:
        return None
    idx = dividends.index
    if hasattr(idx, "tz") and idx.tz is not None:
        idx = idx.tz_localize(None)
        dividends = dividends.copy()
        dividends.index = idx
    cutoff = pd.Timestamp.now().normalize() - pd.Timedelta(365, unit='D')
    ttm = float(dividends[dividends.index >= cutoff].sum())
    return (ttm / price) if ttm > 0 else None


def _compute_eps_surprise(
    earnings_dates: pd.DataFrame | None,
    lookback_quarters: int = 4,
) -> Optional[float]:
    """Return the average EPS surprise (%) over the last *lookback_quarters*.

    yfinance ``earnings_dates`` contains ``EPS Estimate``, ``Reported EPS`` and
    ``Surprise(%)`` for past and future earnings dates.  We keep only rows with
    a reported EPS and a finite surprise percentage, drop duplicate report
    dates (e.g. pre/post-market entries), and average the most recent quarters.
    Returns ``None`` when no usable data is available.
    """
    if earnings_dates is None or earnings_dates.empty:
        return None
    if "Surprise(%)" not in earnings_dates.columns or "Reported EPS" not in earnings_dates.columns:
        return None

    surprises = pd.Series(pd.to_numeric(earnings_dates["Surprise(%)"], errors="coerce"))
    reported = pd.Series(pd.to_numeric(earnings_dates["Reported EPS"], errors="coerce"))
    # Build boolean mask element-wise to avoid ndarray type issues.
    valid = pd.Series(
        [
            pd.notna(surprises.iat[i]) and pd.notna(reported.iat[i]) and math.isfinite(float(surprises.iat[i]))
            for i in range(len(surprises))
        ],
        index=surprises.index,
        dtype=bool,
    )
    if not valid.any():
        return None

    # Normalize the index to naive dates so timezone differences do not create
    # duplicate report days.
    idx = pd.DatetimeIndex(pd.to_datetime(earnings_dates.index)).tz_localize(None).normalize()

    df = pd.DataFrame({
        "surprise": surprises[valid],
        "report_date": idx[valid],
    })
    # Drop duplicate report dates, keeping the latest entry for each day.
    df = df.sort_index().drop_duplicates(subset=["report_date"], keep="last")

    recent = df.sort_index(ascending=False).head(lookback_quarters)
    if recent.empty:
        return None
    return float(recent["surprise"].mean())


def _period_return(history: pd.DataFrame, periods_back: int) -> Optional[float]:
    """Return the simple return over *periods_back* trading days."""
    if history is None or history.empty or "Close" not in history:
        return None
    close = history["Close"].dropna()
    if len(close) < periods_back + 1:
        return None
    past = float(close.iloc[-periods_back - 1])
    price = float(close.iloc[-1])
    return (price / past) - 1.0 if past > 0 else None


def _trend_metrics(
    history: pd.DataFrame,
) -> tuple[Optional[float], Optional[float], Optional[float], Optional[float], Optional[float], Optional[float]]:
    """Return (price_vs_sma200, return_1m, return_12m, sma200_slope, annualized_vol, daily_change).

    Degrades gracefully for short histories: daily_change and 1m return only
    need 2 and 22 bars respectively, while SMA200-dependent metrics require
    200+ bars.
    """
    if history is None or history.empty or "Close" not in history:
        return None, None, None, None, None, None

    close = history["Close"].dropna()
    if len(close) < 2:
        return None, None, None, None, None, None

    price = float(close.iloc[-1])
    prev_close = float(close.iloc[-2])
    daily_change = (price / prev_close) - 1.0 if prev_close > 0 else None

    return_1m = None
    if len(close) >= 22:
        past_1m = float(close.iloc[-22])
        if past_1m > 0:
            return_1m = (price / past_1m) - 1.0

    # SMA200-dependent metrics
    price_vs = None
    return_12m = None
    vol = None
    slope = None
    if len(close) >= 200:
        sma200 = close.rolling(window=200).mean()
        sma_now = float(sma200.iloc[-1])
        price_vs = (price / sma_now) - 1.0 if sma_now > 0 else None

        if len(close) >= 252:
            past = float(close.iloc[-252])
            if past > 0:
                return_12m = (price / past) - 1.0
            daily_ret = close.iloc[-252:].pct_change().dropna()
            if len(daily_ret) > 30:
                vol = float(daily_ret.std() * math.sqrt(252))

        sma_clean = sma200.dropna()
        if len(sma_clean) >= 126:
            recent = sma_clean.iloc[-126:]
            start, end = float(recent.iloc[0]), float(recent.iloc[-1])
            if start > 0:
                slope = (end / start) - 1.0

    return price_vs, return_1m, return_12m, slope, vol, daily_change


def _technical_indicators(
    close: pd.Series,
) -> tuple[Optional[float], Optional[float]]:
    """Return (rsi_14, macd_histogram_pct).

    RSI uses a 14-day look-back.  MACD histogram is the difference between
    the MACD line (EMA12 - EMA26) and its 9-day EMA signal, expressed as
    a percentage of the latest close so the value is comparable across
    tickers with different price levels.

    Both require at least 35 data points; otherwise (None, None) is returned.
    """
    clean = close.dropna()
    if len(clean) < 35:
        return None, None

    # RSI(14)
    delta = clean.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=14).mean().iloc[-1]
    avg_loss = loss.rolling(window=14).mean().iloc[-1]
    if pd.isna(avg_gain) or pd.isna(avg_loss):
        rsi = None
    elif avg_loss == 0:
        rsi = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))

    # MACD histogram (normalized by price)
    ema_12 = clean.ewm(span=12, adjust=False).mean()
    ema_26 = clean.ewm(span=26, adjust=False).mean()
    macd_line = ema_12 - ema_26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    hist = macd_line.iloc[-1] - signal_line.iloc[-1]
    last_price = float(clean.iloc[-1])
    hist_pct = (hist / last_price) if last_price > 0 else None

    return rsi, hist_pct


def _apply_history_common(
    data: StockData | EtfData,
    history: pd.DataFrame,
    *,
    include_volatility: bool = False,
) -> None:
    """Apply trend metrics, technical indicators, and price fallback to data.

    Extracts the common history-processing logic shared between stock and
    ETF fetching to avoid duplication.
    """
    pvs, r1m, r12, slope, vol, daily = _trend_metrics(history)
    data.price_vs_sma200 = pvs
    data.return_1m = r1m
    data.return_12m = r12
    if isinstance(data, EtfData) and history is not None and not history.empty:
        data.return_3m = _period_return(history, 63)
        data.return_6m = _period_return(history, 126)
    data.sma200_slope = slope
    data.daily_change = daily
    if include_volatility and isinstance(data, EtfData):
        data.volatility_1y = vol
        if r12 is not None and vol is not None and vol > 0:
            data.sharpe_proxy = (r12 - RISK_FREE_RATE) / vol
    if history is not None and not history.empty and "Close" in history:
        close = history["Close"].dropna()
        rsi, macd = _technical_indicators(close)
        data.rsi_14 = rsi
        data.macd_histogram = macd
    if data.current_price is None and history is not None and not history.empty and "Close" in history:
        data.current_price = float(history["Close"].iloc[-1])


# ── Shared StockData derivation (backtest + StockFit live source) ─────────

@dataclass
class _Fundamentals:
    """Raw fundamental inputs shared by the classic and PIT snapshot builders."""

    ni: Optional[float] = None
    rev: Optional[float] = None
    eps: Optional[float] = None
    ni_prev: Optional[float] = None
    rev_prev: Optional[float] = None
    eps_prev: Optional[float] = None
    eps_prev2: Optional[float] = None
    gross_profit: Optional[float] = None
    gross_profit_prev: Optional[float] = None
    total_assets_prev: Optional[float] = None
    # As-filed fiscal-year figure displaced by the StockFit TTM overlay.
    # Levels (ROE, margins, P/E) read the TTM value in the plain field; the
    # YoY comparisons read these so both sides come from non-overlapping
    # fiscal years.  None whenever there was no overlay (classic + backtest).
    ni_asfiled: Optional[float] = None
    rev_asfiled: Optional[float] = None
    eps_asfiled: Optional[float] = None
    gross_profit_asfiled: Optional[float] = None
    total_assets_asfiled: Optional[float] = None
    equity: Optional[float] = None
    total_assets: Optional[float] = None
    total_debt: Optional[float] = None
    curr_assets: Optional[float] = None
    curr_liab: Optional[float] = None
    shares: Optional[float] = None
    fcf: Optional[float] = None


def _pct_change(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    if current is None or previous is None or previous == 0:
        return None
    return (current - previous) / abs(previous)


def _eps_acceleration(
    eps: Optional[float], eps_prev: Optional[float], eps_prev2: Optional[float]
) -> Optional[float]:
    """Second derivative of as-filed EPS growth — EPS-revisions fallback.

    ``growth(eps vs eps_prev) − growth(eps_prev vs eps_prev2)``: accelerating
    earnings are the as-filed shadow of upward estimate revisions.  ``None``
    unless all three fiscal years are available.
    """
    growth_now = _pct_change(eps, eps_prev)
    growth_prev = _pct_change(eps_prev, eps_prev2)
    if growth_now is None or growth_prev is None:
        return None
    return growth_now - growth_prev


def _derive_stock_data(
    ticker: str,
    info: dict,
    price: Optional[float],
    trend_vals: tuple,
    rsi_14: Optional[float],
    macd_histogram: Optional[float],
    fund: _Fundamentals,
    ttm_div: Optional[float],
    eps_surprise: Optional[float],
) -> StockData:
    """Derive ``StockData`` metrics from raw fundamentals.

    Single derivation path shared by the classic (fixed reporting-lag) and
    StockFit point-in-time snapshot builders — a data-source comparison
    only changes the inputs, never the math.
    """
    price_vs_sma200, return_1m, return_12m, sma200_slope, _vol, daily_change = trend_vals

    ni, rev, eps = fund.ni, fund.rev, fund.eps
    equity = fund.equity
    total_assets = fund.total_assets
    total_debt = fund.total_debt
    curr_assets = fund.curr_assets
    curr_liab = fund.curr_liab
    shares = fund.shares
    fcf = fund.fcf

    # YoY comparisons read the *as-filed* fiscal years, never the TTM levels
    # above: the StockFit overlay swaps trailing figures into ``ni``/``rev``/
    # ``eps`` for the levels, and comparing a TTM window against the latest
    # filed year mixes spans — collapsing to exactly 0% whenever no quarter
    # has been filed since the fiscal year closed (MSFT FY2026 filed
    # 29-jul-2026, TTM ≡ FY2026 → Growth 18 → disqualifying cap).
    # No overlay (classic + backtest PIT) → all ``*_asfiled`` are None and
    # ``_first`` resolves to the as-filed ``ni``/``rev``/... as before.
    ni_yoy = _first(fund.ni_asfiled, fund.ni)
    rev_yoy = _first(fund.rev_asfiled, fund.rev)
    eps_yoy = _first(fund.eps_asfiled, fund.eps)
    gross_profit_yoy = _first(fund.gross_profit_asfiled, fund.gross_profit)
    total_assets_yoy = _first(fund.total_assets_asfiled, fund.total_assets)

    earnings_growth = _pct_change(ni_yoy, fund.ni_prev)
    revenue_growth = _pct_change(rev_yoy, fund.rev_prev)

    # Debt/Equity: yfinance reports as percentage, divide by 100.
    # Negative equity makes the ratio meaningless (and sign flips can clamp
    # to a perfect score in the scorers), so it is treated as missing — the
    # same way yfinance reports None for unusable ratios in the live path.
    debt_to_equity = (
        (total_debt / equity) * 100.0
        if (total_debt is not None and equity is not None and equity > 0)
        else None
    )

    current_ratio = (
        curr_assets / curr_liab
        if (curr_assets is not None and curr_liab is not None and curr_liab > 0)
        else None
    )

    # Trailing P/E (meaningless for loss-making companies → None, like yfinance)
    trailing_pe = (
        (price / eps) if (price is not None and eps is not None and eps > 0) else None
    )

    # Price/Book (meaningless with negative equity → None)
    bvps = (
        equity / shares
        if (equity is not None and equity > 0 and shares is not None and shares > 0)
        else None
    )
    price_to_book = (
        (price / bvps) if (price is not None and bvps is not None and bvps > 0) else None
    )

    # ROE / ROA (ROE with negative equity sign-flips → None)
    roe = (ni / equity) if (ni is not None and equity is not None and equity > 0) else None
    roa = (
        (ni / total_assets)
        if (ni is not None and total_assets is not None and total_assets > 0)
        else None
    )

    # Profit margin
    profit_margin = (ni / rev) if (ni is not None and rev is not None and rev > 0) else None

    # YoY improvement flags (Piotroski-style Δ checks) — shared semantics
    # with the live path via financial_health.improvement_flags().
    margin_improving, roa_improving = improvement_flags(
        {"GrossProfit": gross_profit_yoy, "TotalRevenue": rev_yoy,
         "NetIncome": ni_yoy, "TotalAssets": total_assets_yoy},
        {"GrossProfit": fund.gross_profit_prev, "TotalRevenue": fund.rev_prev,
         "NetIncome": fund.ni_prev, "TotalAssets": fund.total_assets_prev},
    )

    # Market cap
    market_cap = (price * shares) if (price and shares) else None

    # Dividend yield (TTM) — the TTM window is chosen by the caller
    # (quarter_date for the classic path, snapshot_date for PIT).
    dividend_yield = (
        (ttm_div / price) if (ttm_div is not None and price and price > 0) else None
    )

    # Payout ratio
    payout_ratio = (
        (ttm_div / eps) if (ttm_div and eps and eps != 0) else None
    )

    # EPS-revisions fallback: as-filed EPS growth acceleration.
    accel = _eps_acceleration(eps_yoy, fund.eps_prev, fund.eps_prev2)

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
        margin_improving=margin_improving,
        roa_improving=roa_improving,
        debt_to_equity=debt_to_equity,
        current_ratio=current_ratio,
        free_cashflow=fcf,
        dividend_yield=dividend_yield,
        payout_ratio=payout_ratio,
        eps_surprise=eps_surprise,
        eps_acceleration=accel,
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


def _fetch_stock(
    ticker: str,
    info: dict,
    history: pd.DataFrame,
    dividends: pd.Series | None = None,
    earnings_dates: pd.DataFrame | None = None,
    income_stmt: pd.DataFrame | None = None,
    balance_sheet: pd.DataFrame | None = None,
) -> StockData:
    data = StockData(ticker=ticker)
    data.name = info.get("shortName") or info.get("longName") or None
    data.sector = info.get("sector")
    data.currency = info.get("currency")
    data.exchange = info.get("exchange")
    data.trailing_pe = _safe_get(info, "trailingPE")
    data.forward_pe = _safe_get(info, "forwardPE")
    data.price_to_book = _safe_get(info, "priceToBook")
    data.peg_ratio = _first(_safe_get(info, "pegRatio"), _safe_get(info, "trailingPegRatio"))
    data.return_on_equity = _safe_get(info, "returnOnEquity")
    data.return_on_assets = _safe_get(info, "returnOnAssets")
    data.profit_margin = _safe_get(info, "profitMargins")
    data.earnings_growth = _safe_get(info, "earningsGrowth")
    data.revenue_growth = _safe_get(info, "revenueGrowth")
    data.debt_to_equity = _safe_get(info, "debtToEquity")
    data.current_ratio = _safe_get(info, "currentRatio")
    data.free_cashflow = _safe_get(info, "freeCashflow")
    data.payout_ratio = _safe_get(info, "payoutRatio")
    data.market_cap = _safe_get(info, "marketCap")
    data.current_price = _first(_safe_get(info, "currentPrice"), _safe_get(info, "regularMarketPrice"))
    _apply_history_common(data, history)

    # Compute dividend yield from raw dividends (more reliable than yfinance's
    # dividendYield field, which can be off by 100x for some tickers). Fall back
    # to the info field only when raw dividends are unavailable.
    computed_yield = _ttm_dividend_yield(dividends, data.current_price)
    if computed_yield is not None:
        data.dividend_yield = computed_yield
    else:
        data.dividend_yield = _sanitize_yield(_safe_get(info, "dividendYield"))

    if earnings_dates is not None:
        data.eps_surprise = _compute_eps_surprise(earnings_dates)
    else:
        data.eps_surprise = _safe_get(info, "epsSurprise")

    # YoY improvement flags from annual statements (None = unknown when the
    # statements are unavailable — same semantics as the backtest builders).
    if income_stmt is not None and balance_sheet is not None:
        cur = annual_facts(income_stmt, balance_sheet, None, datetime.now())
        prev = annual_facts(
            income_stmt, balance_sheet, None, datetime.now(), years_back=1
        )
        data.margin_improving, data.roa_improving = improvement_flags(cur, prev)

    return data


def _fetch_etf(ticker: str, info: dict, history: pd.DataFrame) -> EtfData:
    data = EtfData(ticker=ticker)
    data.name = info.get("longName") or info.get("shortName") or None
    data.category = info.get("category")
    data.fund_family = info.get("fundFamily")
    data.currency = info.get("currency")
    data.exchange = info.get("exchange")
    data.total_assets = _safe_get(info, "totalAssets")
    data.expense_ratio = _first(
        _safe_get(info, "annualReportExpenseRatio"),
        _safe_get(info, "netExpenseRatio"),
    )
    data.three_year_return = _safe_get(info, "threeYearAverageReturn")
    data.five_year_return = _safe_get(info, "fiveYearAverageReturn")
    data.beta_3y = _first(_safe_get(info, "beta3Year"), _safe_get(info, "beta"))
    data.yield_ = _sanitize_yield(_first(_safe_get(info, "yield"), _safe_get(info, "trailingAnnualDividendYield")))
    data.current_price = _first(_safe_get(info, "regularMarketPrice"), _safe_get(info, "previousClose"))
    data.nav = _safe_get(info, "navPrice")
    _apply_history_common(data, history, include_volatility=True)
    return data


def _enrich_etf_info(t: yf.Ticker, info: dict) -> None:
    """Backfill expense ratio from ``t.funds_data`` if missing from ``info``.

    Mutates ``info`` in-place so the enriched dict gets cached.
    """
    if info.get("annualReportExpenseRatio") is not None or info.get("netExpenseRatio") is not None:
        return
    try:
        funds = getattr(t, "funds_data", None)
        if funds is not None:
            desc = funds.fund_overview or {}
            er = desc.get("annualReportExpenseRatio") or desc.get("expenseRatio")
            if er is not None:
                info["netExpenseRatio"] = float(er)
    except Exception:
        pass


def _cached_statement_frame(ticker: str, kind: str) -> Optional[pd.DataFrame]:
    """One annual statement frame (7-day cache), ``None`` when unavailable."""
    from investdaytip.cache import cache_financial_get, cache_financial_set

    raw = cache_financial_get(ticker, kind)
    if raw is not None:
        try:
            df = pd.read_json(StringIO(raw))
            if not df.empty:
                return df
        except Exception:
            pass
    try:
        with _suppress_stderr():
            df = getattr(yf.Ticker(ticker), kind)
        if df is not None and not df.empty:
            cache_financial_set(ticker, kind, df.to_json())
            return df
    except Exception:
        pass
    return None


def fetch_statement_frames(ticker: str) -> tuple[Optional[pd.DataFrame], Optional[pd.DataFrame]]:
    """Annual income statement + balance sheet (7-day cache) for the YoY flags."""
    return (
        _cached_statement_frame(ticker, "income_stmt"),
        _cached_statement_frame(ticker, "balance_sheet"),
    )


def fetch_cash_flow_frame(ticker: str) -> Optional[pd.DataFrame]:
    """Annual cash-flow statement (7-day cache) — needed for Piotroski's OCF check."""
    return _cached_statement_frame(ticker, "cash_flow")


def fetch_asset(ticker: str, min_market_cap: float = 0.0, with_improvements: bool = True) -> AssetData:
    """Fetch data for a ticker, auto-dispatching stock vs ETF.

    Uses a SQLite cache (``~/.investdaytip/cache.db``) to avoid redundant
    yfinance calls.  The ``info`` dict is cached for 1 day; price history
    is cached for 5 minutes.  Use ``--no-cache`` to bypass or
    ``--cache-clear`` to purge all entries.

    Retries up to 3 times with exponential backoff on rate-limit errors.
    When ``min_market_cap > 0``, skips the expensive ``t.history()`` call
    for tickers whose market cap / AUM is below the threshold.

    ``with_improvements`` (stocks only) fetches the annual income statement
    and balance sheet (7-day cache) to populate the YoY-improvement flags
    used by the quant model; pass ``False`` for the classic model to skip
    the two extra calls.
    """
    from investdaytip.cache import (
        cache_dividends_get,
        cache_dividends_set,
        cache_earnings_dates_get,
        cache_earnings_dates_set,
        cache_history_get,
        cache_history_set,
        cache_info_get,
        cache_info_set,
    )

    # ── Step 1: get / fetch info dict ────────────────────────────────────
    info = cache_info_get(ticker)
    t: yf.Ticker | None = None
    info_fetched_fresh = False
    if info is None:
        delays = [15, 45, 90, 180]
        for attempt in range(len(delays) + 1):
            try:
                with _suppress_stderr():
                    t = yf.Ticker(ticker)
                    info = t.info or {}
            except YFRateLimitError:
                if attempt < len(delays):
                    time.sleep(delays[attempt])
                    continue
                d = StockData(ticker=ticker)
                d.errors.append(f"rate limited after {len(delays)} retries")
                return d
            except Exception as exc:
                d = StockData(ticker=ticker)
                d.errors.append(f"info fetch failed: {exc}")
                return d
            break

        info = info or {}
        if (info.get("quoteType") or "").upper() == "ETF" and t is not None:
            _enrich_etf_info(t, info)
        info_fetched_fresh = True

    # ``info`` is guaranteed to be a dict here: it was either a cache hit or
    # assigned ``t.info or {}`` above (both error paths returned early).
    assert info is not None
    quote_type = (info.get("quoteType") or "").upper()

    # ── Early exit: market-cap threshold ─────────────────────────────────
    if min_market_cap > 0:
        raw = (
            info.get("totalAssets") if quote_type == "ETF"
            else info.get("marketCap")
        )
        try:
            if raw is not None and float(raw) < min_market_cap:
                # Below threshold — skip the expensive history/dividends fetch.
                data = (
                    _fetch_etf(ticker, info, pd.DataFrame())
                    if quote_type == "ETF"
                    else _fetch_stock(ticker, info, pd.DataFrame())
                )
                data.errors.append("market cap below threshold — history skipped")
                if info_fetched_fresh:
                    cache_info_set(ticker, info)
                return data
        except (TypeError, ValueError):
            pass

    # ── Step 2: get / fetch price history ────────────────────────────────
    history_str = cache_history_get(ticker)
    if history_str is not None:
        try:
            history = pd.read_json(StringIO(history_str))
        except Exception:
            logging.warning("Corrupt history cache for %s — refetching", ticker)
            history_str = None
    if history_str is None:
        try:
            with _suppress_stderr():
                if t is None:
                    t = yf.Ticker(ticker)
                history = t.history(period="2y", interval="1d", auto_adjust=True)
            cache_history_set(ticker, history.to_json())
        except Exception as exc:
            data = (
                _fetch_etf(ticker, info, pd.DataFrame())
                if quote_type == "ETF"
                else _fetch_stock(ticker, info, pd.DataFrame())
            )
            data.errors.append(f"history fetch failed: {exc}")
            # Cache even on failure so next run produces identical result
            if info_fetched_fresh:
                cache_info_set(ticker, info)
                cache_history_set(ticker, pd.DataFrame().to_json())
            return data

    # Cache info now that history also succeeded — atomic snapshot for
    # consistent results across consecutive runs.
    if info_fetched_fresh:
        cache_info_set(ticker, info)

    # ── Step 3: get / fetch dividends ────────────────────────────────────
    # Used to compute a reliable TTM dividend yield for stocks. ETFs keep
    # using the yield field from info.
    dividends: pd.Series | None = None
    dividends_fetched_fresh = False
    if quote_type != "ETF":
        divs_raw = cache_dividends_get(ticker)
        if divs_raw is not None:
            try:
                dividends = pd.read_json(StringIO(divs_raw), typ="series")
            except Exception:
                dividends = None
        else:
            try:
                with _suppress_stderr():
                    if t is None:
                        t = yf.Ticker(ticker)
                    dividends = t.dividends
                dividends_fetched_fresh = True
            except Exception:
                dividends = None

    # ── Step 4: get / fetch earnings_dates (stocks only) ─────────────────
    earnings_dates: pd.DataFrame | None = None
    earnings_dates_fetched_fresh = False
    if quote_type != "ETF":
        ed_raw = cache_earnings_dates_get(ticker)
        if ed_raw is not None:
            try:
                earnings_dates = pd.read_json(StringIO(ed_raw))
            except Exception:
                earnings_dates = None
        else:
            try:
                with _suppress_stderr():
                    if t is None:
                        t = yf.Ticker(ticker)
                    earnings_dates = t.earnings_dates
                earnings_dates_fetched_fresh = True
            except Exception:
                earnings_dates = None

    # ── Step 4b: annual statements (stocks only) ─────────────────────────
    # Feeds the YoY-improvement flags (Δgross margin, ΔROA); 7-day cache.
    income_stmt: pd.DataFrame | None = None
    balance_sheet: pd.DataFrame | None = None
    if quote_type != "ETF" and with_improvements:
        income_stmt, balance_sheet = fetch_statement_frames(ticker)

    # ── Step 5: construct result ─────────────────────────────────────────
    if quote_type == "ETF":
        return _fetch_etf(ticker, info, history)

    if dividends_fetched_fresh and dividends is not None:
        cache_dividends_set(ticker, dividends.to_json(date_format="iso"))
    if earnings_dates_fetched_fresh and earnings_dates is not None:
        if earnings_dates.index.duplicated().any():
            earnings_dates = earnings_dates[~earnings_dates.index.duplicated(keep="last")]
        cache_earnings_dates_set(ticker, earnings_dates.to_json(date_format="iso"))
    return _fetch_stock(
        ticker, info, history, dividends, earnings_dates, income_stmt, balance_sheet
    )


# Backwards-compatible alias
def fetch_stock(ticker: str, min_market_cap: float = 0.0, with_improvements: bool = True) -> AssetData:
    return fetch_asset(ticker, min_market_cap, with_improvements=with_improvements)

