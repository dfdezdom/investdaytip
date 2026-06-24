"""FMP (Financial Modeling Prep) data source.

Provides ``fetch_asset(ticker, min_market_cap)`` as a drop-in replacement
for ``investdaytip.data_source.fetch_asset`` when the user specifies
``--data-source fmp``.

Only stocks are supported (ETFs return an error).  Uses the FMP stable API
with the following endpoints per ticker:
  - ``profile``              — name, sector, exchange, currency, market cap, price
  - ``ratios-ttm``           — PE, PB, PEG, ROE, ROA, profit margin, D/E,
                               current ratio, div yield, payout ratio, FCF/share
  - ``historical-price-eod`` — OHLCV for 2 years (trend + technicals)
  - ``earnings-surprises``   — EPS surprise for the ``quant`` scoring model
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from io import StringIO
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd

from investdaytip.cache import (
    cache_history_get,
    cache_history_set,
    cache_info_get,
    cache_info_set,
)
from investdaytip.data_source import (
    StockData,
    _apply_history_common,
)

logger = logging.getLogger(__name__)

FMP_BASE = "https://financialmodelingprep.com/stable"
FMP_REQUEST_TIMEOUT = 10  # seconds per HTTP request
FMP_TICKER_TIMEOUT = 90   # seconds max per ticker (all 4 endpoints)
FMP_RETRY_DELAYS = [2, 5]  # short retries before giving up
USER_AGENT = "InvestDayTip/0.8.0"


class FmpError(Exception):
    """Non-recoverable FMP API error (missing key, network, invalid ticker)."""


class FmpRateLimitError(FmpError):
    """FMP free-tier rate limit reached (250 requests/day).

    Caught by :func:`recommend` to offer a yfinance fallback.
    """


def check_rate_limit() -> None:
    """Make a single lightweight FMP request to probe for rate-limiting.

    Raises :exc:`FmpRateLimitError` if the daily quota is exhausted.
    Raises :exc:`FmpError` for other failures (network, missing key).
    Safe to call before starting the batch so the user gets prompt feedback.
    """
    _get("profile/SPY")  # fast, well-known ticker; raises on error


def _get(path: str, params: dict[str, str] | None = None) -> list[dict]:
    """Perform an FMP API GET and return the parsed JSON array."""
    api_key = os.environ.get("FMP_API_KEY")
    if not api_key:
        raise FmpError("FMP_API_KEY environment variable not set")

    url = f"{FMP_BASE}/{path}?apikey={api_key}"
    if params:
        url += "&" + "&".join(f"{k}={v}" for k, v in params.items())

    for attempt in range(len(FMP_RETRY_DELAYS) + 1):
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(req, timeout=FMP_REQUEST_TIMEOUT) as resp:
                data = json.loads(resp.read().decode())
        except HTTPError as exc:
            if exc.code == 404:
                raise FmpError(f"ticker not found: {path}") from exc
            if exc.code == 429:
                raise FmpRateLimitError(
                    f"FMP rate limit (HTTP 429): {exc}"
                ) from exc
            if attempt < len(FMP_RETRY_DELAYS):
                time.sleep(FMP_RETRY_DELAYS[attempt])
                continue
            raise FmpError(f"FMP request failed (HTTP {exc.code}): {exc}") from exc
        except URLError as exc:
            if attempt < len(FMP_RETRY_DELAYS):
                time.sleep(FMP_RETRY_DELAYS[attempt])
                continue
            raise FmpError(f"FMP request failed: {exc}") from exc

        if isinstance(data, dict) and "Error Message" in data:
            msg: str = data["Error Message"]
            if "limit" in msg.lower():
                raise FmpRateLimitError(f"FMP rate limit: {msg}")
            raise FmpError(f"FMP error: {msg}")
        if not isinstance(data, list):
            raise FmpError(f"unexpected FMP response type: {type(data).__name__}")
        return data

    raise FmpError("FMP request failed after all retries")


def _fetch_eps_surprise(data: list[dict]) -> Optional[float]:
    """Compute average EPS surprise (%) over the last 4 quarters."""
    quarters: list[tuple[str, float]] = []
    for entry in data:
        try:
            actual = float(entry["actualEarningResult"])
            estimate = float(entry["estimatedEarning"])
            if estimate != 0 and math.isfinite(actual) and math.isfinite(estimate):
                pct = ((actual - estimate) / abs(estimate)) * 100.0
                quarters.append((entry["date"], pct))
        except (KeyError, TypeError, ValueError):
            continue
    if not quarters:
        return None
    quarters.sort(key=lambda x: x[0], reverse=True)
    recent = [pct for _, pct in quarters[:4]]
    return sum(recent) / len(recent) if recent else None


def _history_to_df(data: list[dict]) -> pd.DataFrame:
    """Convert FMP historical-price-eod response to a DataFrame."""
    records: list[dict] = []
    for entry in data:
        try:
            records.append({
                "Date": entry["date"],
                "Open": float(entry["open"]),
                "High": float(entry["high"]),
                "Low": float(entry["low"]),
                "Close": float(entry["close"]),
                "Volume": int(entry.get("volume", 0)),
            })
        except (KeyError, TypeError, ValueError):
            continue
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").set_index("Date")
    return df


# ── Cache-backed fetchers ──────────────────────────────────────────────────


def _fetch_and_cache_profile(ticker: str) -> dict | None:
    """Return cached profile dict or fetch fresh."""
    cached = cache_info_get(ticker)
    if cached is not None and "profile" in cached:
        return cached["profile"]
    try:
        data = _get(f"profile/{ticker}")
        profile = data[0] if data else {}
        # Store under info key for reuse by _fetch_and_cache_fundamentals
        cache_info_set(ticker, {"profile": profile})
        return profile
    except FmpRateLimitError:
        raise
    except FmpError:
        return None


def _fetch_and_cache_fundamentals(ticker: str) -> dict | None:
    """Fetch ratios-ttm + earnings-surprises and merge into the info cache.

    The profile must already be cached (via ``_fetch_and_cache_profile``)
    before calling this.  Returns the full info dict on success, ``None``
    on failure.
    """
    cached = cache_info_get(ticker)
    if cached is not None and "ratios_ttm" in cached:
        return cached

    try:
        raw_ratios = _get(f"ratios-ttm/{ticker}")
        ratios = raw_ratios[0] if raw_ratios else {}
    except FmpRateLimitError:
        raise
    except FmpError:
        return None

    eps_surprise = None
    try:
        surprises = _get(f"earnings-surprises/{ticker}")
        eps_surprise = _fetch_eps_surprise(surprises)
    except FmpRateLimitError:
        raise
    except FmpError:
        pass

    profile = (cached or {}).get("profile") or {}
    info = {"profile": profile, "ratios_ttm": ratios, "eps_surprise": eps_surprise}
    cache_info_set(ticker, info)
    return info


def _fetch_and_cache_history(ticker: str) -> pd.DataFrame:
    """Return cached price history or fetch from FMP."""
    raw = cache_history_get(ticker)
    if raw is not None:
        try:
            return pd.read_json(StringIO(raw))
        except Exception:
            pass

    today = time.strftime("%Y-%m-%d")
    two_years_ago = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 63072000))
    try:
        data = _get(f"historical-price-eod/{ticker}", {"from": two_years_ago, "to": today})
        df = _history_to_df(data)
        cache_history_set(ticker, df.to_json())
        return df
    except FmpRateLimitError:
        raise
    except FmpError:
        return pd.DataFrame()


# ── Helpers ─────────────────────────────────────────────────────────────────


def _safe_float(d: dict, key: str) -> Optional[float]:
    val = d.get(key)
    if val is None:
        return None
    try:
        f = float(val)
        if not math.isfinite(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _mkt_cap(profile: dict) -> tuple[bool, float]:
    cap_val = profile.get("mktCap")
    if cap_val is None:
        return False, 0.0
    try:
        cap = float(cap_val)
        if not math.isfinite(cap) or cap <= 0:
            return False, 0.0
        return True, cap
    except (TypeError, ValueError):
        return False, 0.0


# ── Public entry point ─────────────────────────────────────────────────────


def fetch_asset(ticker: str, min_market_cap: float = 0.0) -> StockData:
    """Fetch data for *ticker* from FMP's stable API.

    Args:
        ticker: The ticker symbol (e.g. ``"AAPL"``).
        min_market_cap: If ``> 0``, skip the history fetch for tickers
            whose market cap is below this threshold.

    Returns:
        A ``StockData`` instance (never raises). Fields are ``None`` on
        error so the scorer degrades gracefully.
    """
    # ── 1. Profile (lightweight, used for filtering) ──────────────────────
    profile = _fetch_and_cache_profile(ticker)
    if profile is None:
        d = StockData(ticker=ticker)
        d.errors.append("failed to fetch profile from FMP")
        return d

    # ETF check
    if profile.get("isEtf", False):
        d = StockData(ticker=ticker)
        d.errors.append("ETFs not supported via FMP data source")
        return d

    # Market cap validation + filter
    is_valid, cap = _mkt_cap(profile)
    if not is_valid:
        d = StockData(ticker=ticker)
        d.errors.append("no valid market cap from FMP")
        return d

    if min_market_cap > 0 and cap < min_market_cap:
        d = StockData(ticker=ticker, market_cap=cap)
        d.errors.append("market cap below threshold")
        return d

    # ── 2. Fundamentals (ratios + EPS) ────────────────────────────────────
    info = _fetch_and_cache_fundamentals(ticker)
    ratios = (info or {}).get("ratios_ttm") or {}

    # ── 3. Price history ──────────────────────────────────────────────────
    history = _fetch_and_cache_history(ticker)

    # ── 4. Build StockData ────────────────────────────────────────────────
    price = _safe_float(profile, "price")
    data = StockData(ticker=ticker)
    data.name = profile.get("companyName") or profile.get("symbol")
    data.sector = profile.get("sector")
    data.currency = profile.get("currency")
    data.exchange = profile.get("exchange")
    data.current_price = price
    data.market_cap = cap

    # Valuation
    data.trailing_pe = _safe_float(ratios, "priceEarningsRatio")
    data.price_to_book = _safe_float(ratios, "priceToBookRatio")
    data.peg_ratio = _safe_float(ratios, "pegRatio")

    # Quality
    data.return_on_equity = _safe_float(ratios, "returnOnEquity")
    data.return_on_assets = _safe_float(ratios, "returnOnAssets")
    data.profit_margin = _safe_float(ratios, "netProfitMargin")
    data.earnings_growth = _safe_float(ratios, "earningsGrowth")
    data.revenue_growth = _safe_float(ratios, "revenueGrowth")

    # Health
    data.debt_to_equity = _safe_float(ratios, "debtToEquity")
    data.current_ratio = _safe_float(ratios, "currentRatio")

    # FCF: compute from FCF/share * shares outstanding
    fcf_ps = _safe_float(ratios, "freeCashFlowPerShare")
    shares = _safe_float(ratios, "totalSharesOutstanding")
    if fcf_ps is not None and shares is not None and shares > 0:
        data.free_cashflow = fcf_ps * shares

    # Income
    data.dividend_yield = _safe_float(ratios, "dividendYield")
    data.payout_ratio = _safe_float(ratios, "payoutRatio")

    # EPS surprise
    surprise_val = (info or {}).get("eps_surprise")
    if surprise_val is not None and math.isfinite(surprise_val):
        data.eps_surprise = surprise_val

    # Trend + technicals from price history
    _apply_history_common(data, history)

    return data
