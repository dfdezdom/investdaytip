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
from contextlib import contextmanager, redirect_stderr
from dataclasses import dataclass, field
from typing import Optional, Union

import pandas as pd
import yfinance as yf


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

    errors: list[str] = field(default_factory=list)


@dataclass
class EtfData:
    ticker: str
    asset_type: str = "ETF"
    name: Optional[str] = None
    category: Optional[str] = None
    fund_family: Optional[str] = None
    currency: Optional[str] = None
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
        if f != f:
            return None
        return f
    except (TypeError, ValueError):
        return None


def _trend_metrics(
    history: pd.DataFrame,
) -> tuple[Optional[float], Optional[float], Optional[float], Optional[float], Optional[float]]:
    """Return (price_vs_sma200, return_1m, return_12m, sma200_slope, annualized_vol)."""
    if history is None or history.empty or "Close" not in history:
        return None, None, None, None, None

    close = history["Close"].dropna()
    if len(close) < 200:
        return None, None, None, None, None

    sma200 = close.rolling(window=200).mean()
    price = float(close.iloc[-1])
    sma_now = float(sma200.iloc[-1])
    price_vs = (price / sma_now) - 1.0 if sma_now > 0 else None

    return_1m = None
    if len(close) >= 22:
        past_1m = float(close.iloc[-22])
        if past_1m > 0:
            return_1m = (price / past_1m) - 1.0

    return_12m = None
    vol = None
    if len(close) >= 252:
        past = float(close.iloc[-252])
        if past > 0:
            return_12m = (price / past) - 1.0
        daily_ret = close.iloc[-252:].pct_change().dropna()
        if len(daily_ret) > 30:
            vol = float(daily_ret.std() * math.sqrt(252))

    slope = None
    sma_clean = sma200.dropna()
    if len(sma_clean) >= 126:
        recent = sma_clean.iloc[-126:]
        start, end = float(recent.iloc[0]), float(recent.iloc[-1])
        if start > 0:
            slope = (end / start) - 1.0

    return price_vs, return_1m, return_12m, slope, vol


def _fetch_stock(ticker: str, t: yf.Ticker, info: dict) -> StockData:
    data = StockData(ticker=ticker)
    data.name = info.get("shortName") or info.get("longName")
    data.sector = info.get("sector")
    data.currency = info.get("currency")
    data.trailing_pe = _safe_get(info, "trailingPE")
    data.forward_pe = _safe_get(info, "forwardPE")
    data.price_to_book = _safe_get(info, "priceToBook")
    data.peg_ratio = _safe_get(info, "pegRatio") or _safe_get(info, "trailingPegRatio")
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
    data.current_price = _safe_get(info, "currentPrice") or _safe_get(info, "regularMarketPrice")

    try:
        hist = t.history(period="2y", interval="1d", auto_adjust=True)
        pvs, r1m, r12, slope, _vol = _trend_metrics(hist)
        data.price_vs_sma200 = pvs
        data.return_1m = r1m
        data.return_12m = r12
        data.sma200_slope = slope
        if data.current_price is None and hist is not None and not hist.empty:
            data.current_price = float(hist["Close"].iloc[-1])
    except Exception as exc:
        data.errors.append(f"history fetch failed: {exc}")

    return data


def _fetch_etf(ticker: str, t: yf.Ticker, info: dict) -> EtfData:
    data = EtfData(ticker=ticker)
    data.name = info.get("longName") or info.get("shortName")
    data.category = info.get("category")
    data.fund_family = info.get("fundFamily")
    data.currency = info.get("currency")
    data.total_assets = _safe_get(info, "totalAssets")
    data.expense_ratio = (
        _safe_get(info, "annualReportExpenseRatio")
        or _safe_get(info, "netExpenseRatio")
    )
    data.three_year_return = _safe_get(info, "threeYearAverageReturn")
    data.five_year_return = _safe_get(info, "fiveYearAverageReturn")
    data.beta_3y = _safe_get(info, "beta3Year") or _safe_get(info, "beta")
    data.yield_ = _safe_get(info, "yield") or _safe_get(info, "trailingAnnualDividendYield")
    data.current_price = _safe_get(info, "regularMarketPrice") or _safe_get(info, "previousClose")
    data.nav = _safe_get(info, "navPrice")

    # Try to backfill expense ratio from fundsData if missing
    if data.expense_ratio is None:
        try:
            funds = getattr(t, "funds_data", None)
            if funds is not None:
                desc = funds.fund_overview or {}
                er = desc.get("annualReportExpenseRatio") or desc.get("expenseRatio")
                if er is not None:
                    data.expense_ratio = float(er)
        except Exception:
            pass

    try:
        hist = t.history(period="2y", interval="1d", auto_adjust=True)
        pvs, r1m, r12, slope, vol = _trend_metrics(hist)
        data.price_vs_sma200 = pvs
        data.return_1m = r1m
        data.return_12m = r12
        data.sma200_slope = slope
        data.volatility_1y = vol
        if r12 is not None and vol is not None and vol > 0:
            data.sharpe_proxy = (r12 - RISK_FREE_RATE) / vol
        if data.current_price is None and hist is not None and not hist.empty:
            data.current_price = float(hist["Close"].iloc[-1])
    except Exception as exc:
        data.errors.append(f"history fetch failed: {exc}")

    return data


def fetch_asset(ticker: str) -> AssetData:
    """Fetch data for a ticker, auto-dispatching stock vs ETF."""
    try:
        with _suppress_stderr():
            t = yf.Ticker(ticker)
            info = t.info or {}
    except Exception as exc:
        d = StockData(ticker=ticker)
        d.errors.append(f"info fetch failed: {exc}")
        return d

    quote_type = (info.get("quoteType") or "").upper()
    with _suppress_stderr():
        if quote_type == "ETF":
            return _fetch_etf(ticker, t, info)
        return _fetch_stock(ticker, t, info)


# Backwards-compatible alias
def fetch_stock(ticker: str) -> AssetData:
    return fetch_asset(ticker)

