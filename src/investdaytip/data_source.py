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
from io import StringIO
from typing import Optional, Union

import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFRateLimitError

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
    profit_margin: Optional[float] = None
    earnings_growth: Optional[float] = None
    revenue_growth: Optional[float] = None
    # Health
    debt_to_equity: Optional[float] = None
    current_ratio: Optional[float] = None
    free_cashflow: Optional[float] = None
    # Income
    dividend_yield: Optional[float] = None
    payout_ratio: Optional[float] = None
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
    data.sma200_slope = slope
    data.daily_change = daily
    if include_volatility and isinstance(data, EtfData):
        data.volatility_1y = vol
        if r12 is not None and vol is not None and vol > 0:
            data.sharpe_proxy = (r12 - RISK_FREE_RATE) / vol
    if history is not None and not history.empty:
        close = history["Close"].dropna()
        rsi, macd = _technical_indicators(close)
        data.rsi_14 = rsi
        data.macd_histogram = macd
    if data.current_price is None and history is not None and not history.empty:
        data.current_price = float(history["Close"].iloc[-1])


def _fetch_stock(ticker: str, info: dict, history: pd.DataFrame) -> StockData:
    data = StockData(ticker=ticker)
    data.name = info.get("shortName") or info.get("longName")
    data.sector = info.get("sector")
    data.currency = info.get("currency")
    data.exchange = info.get("exchange")
    data.trailing_pe = _safe_get(info, "trailingPE")
    data.forward_pe = _safe_get(info, "forwardPE")
    data.price_to_book = _safe_get(info, "priceToBook")
    data.peg_ratio = _first(_safe_get(info, "pegRatio"), _safe_get(info, "trailingPegRatio"))
    data.return_on_equity = _safe_get(info, "returnOnEquity")
    data.profit_margin = _safe_get(info, "profitMargins")
    data.earnings_growth = _safe_get(info, "earningsGrowth")
    data.revenue_growth = _safe_get(info, "revenueGrowth")
    data.debt_to_equity = _safe_get(info, "debtToEquity")
    data.current_ratio = _safe_get(info, "currentRatio")
    data.free_cashflow = _safe_get(info, "freeCashflow")
    data.dividend_yield = _safe_get(info, "dividendYield")
    data.payout_ratio = _safe_get(info, "payoutRatio")
    data.market_cap = _safe_get(info, "marketCap")
    data.current_price = _first(_safe_get(info, "currentPrice"), _safe_get(info, "regularMarketPrice"))
    _apply_history_common(data, history)
    return data


def _fetch_etf(ticker: str, info: dict, history: pd.DataFrame) -> EtfData:
    data = EtfData(ticker=ticker)
    data.name = info.get("longName") or info.get("shortName")
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
    data.yield_ = _first(_safe_get(info, "yield"), _safe_get(info, "trailingAnnualDividendYield"))
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


def fetch_asset(ticker: str, min_market_cap: float = 0.0) -> AssetData:
    """Fetch data for a ticker, auto-dispatching stock vs ETF.

    Uses a SQLite cache (``~/.investdaytip/cache.db``) to avoid redundant
    yfinance calls.  The ``info`` dict is cached for 1 day; price history
    is cached for 5 minutes.  Use ``--no-cache`` to bypass or
    ``--cache-clear`` to purge all entries.

    Retries up to 3 times with exponential backoff on rate-limit errors.
    When ``min_market_cap > 0``, skips the expensive ``t.history()`` call
    for tickers whose market cap / AUM is below the threshold.
    """
    from investdaytip.cache import (
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
        delays = [10, 30, 60]
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

    # ── Step 2: get / fetch price history ────────────────────────────────
    history_str = cache_history_get(ticker)
    if history_str is not None:
        history = pd.read_json(StringIO(history_str))
    else:
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

    # ── Step 3: construct result ─────────────────────────────────────────
    if quote_type == "ETF":
        return _fetch_etf(ticker, info, history)
    return _fetch_stock(ticker, info, history)


# Backwards-compatible alias
def fetch_stock(ticker: str, min_market_cap: float = 0.0) -> AssetData:
    return fetch_asset(ticker, min_market_cap)

