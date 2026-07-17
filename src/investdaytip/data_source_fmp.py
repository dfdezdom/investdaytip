"""FMP (Financial Modeling Prep) data source.

Provides ``fetch_asset(ticker, min_market_cap)`` as a drop-in replacement
for ``investdaytip.data_source.fetch_asset`` when the user specifies
``--data-source fmp``.

Only stocks are supported (ETFs return an error).  Uses the FMP stable API
with the following endpoints per ticker:
  - ``profile``              — name, sector, exchange, currency, market cap, price
  - ``ratios-ttm``           — PE, PB, PEG, profit margin, D/E,
                               current ratio, div yield, payout ratio, FCF/share
  - ``key-metrics-ttm``      — ROE, ROA
  - ``financial-growth``     — earnings growth, revenue growth
  - ``earnings-surprises``   — actual vs estimated EPS (EPS Revisions factor)
  - ``historical-price-eod`` — OHLCV for 2 years (trend + technicals)
"""

from __future__ import annotations

import http.client
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
    cache_fmp_info_get,
    cache_fmp_info_set,
    cache_history_get,
    cache_history_set,
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
    _get("profile", {"symbol": "SPY"})  # fast, well-known ticker; raises on error


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
                raise FmpError(f"endpoint not found: {path} (HTTP 404)") from exc
            if exc.code == 429:
                raise FmpRateLimitError(
                    f"FMP rate limit (HTTP 429): {exc}"
                ) from exc
            if attempt < len(FMP_RETRY_DELAYS):
                time.sleep(FMP_RETRY_DELAYS[attempt])
                continue
            raise FmpError(f"FMP request failed (HTTP {exc.code}): {exc}") from exc
        except (URLError, http.client.HTTPException, OSError) as exc:
            # URLError covers DNS/refusal; http.client.HTTPException covers
            # IncompleteRead/BadStatusLine; OSError covers reset/timeout.
            if attempt < len(FMP_RETRY_DELAYS):
                time.sleep(FMP_RETRY_DELAYS[attempt])
                continue
            raise FmpError(f"FMP request failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            # Empty body or an HTML error page instead of JSON.
            if attempt < len(FMP_RETRY_DELAYS):
                time.sleep(FMP_RETRY_DELAYS[attempt])
                continue
            raise FmpError(f"FMP returned invalid JSON for {path}: {exc}") from exc

        if isinstance(data, dict) and "Error Message" in data:
            msg: str = data["Error Message"]
            logger.warning("FMP API error for %s: %s", path, msg)
            if "limit" in msg.lower():
                raise FmpRateLimitError(f"FMP rate limit: {msg}")
            raise FmpError(f"FMP error: {msg}")
        if not isinstance(data, list):
            raise FmpError(f"unexpected FMP response type: {type(data).__name__}")
        return data

    raise FmpError("FMP request failed after all retries")


def _history_to_df(data: list[dict]) -> pd.DataFrame:
    """Convert FMP historical-price-eod response to a DataFrame.

    Uses ``adjClose`` when present and rescales OHLC by the same factor, so
    trend/momentum metrics are computed on split- and dividend-adjusted
    prices (matching yfinance's ``auto_adjust=True`` behaviour).
    """
    records: list[dict] = []
    for entry in data:
        try:
            raw_close = float(entry["close"])
            adj_close = float(entry.get("adjClose") or raw_close)
            factor = adj_close / raw_close if raw_close > 0 else 1.0
            records.append({
                "Date": entry["date"],
                "Open": float(entry["open"]) * factor,
                "High": float(entry["high"]) * factor,
                "Low": float(entry["low"]) * factor,
                "Close": adj_close,
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
    cached = cache_fmp_info_get(ticker)
    if cached is not None and "profile" in cached:
        return cached["profile"]
    try:
        data = _get("profile", {"symbol": ticker})
        profile = data[0] if data else {}
        # Store under the FMP-specific info key for reuse by
        # _fetch_and_cache_fundamentals
        cache_fmp_info_set(ticker, {"profile": profile})
        return profile
    except FmpRateLimitError:
        raise
    except FmpError:
        return None


def _fetch_and_cache_fundamentals(ticker: str) -> dict | None:
    """Fetch ratios-ttm and merge into the info cache.

    The profile must already be cached (via ``_fetch_and_cache_profile``)
    before calling this.  Returns the full info dict on success, ``None``
    on failure.
    """
    cached = cache_fmp_info_get(ticker)
    if cached is not None and "ratios_ttm" in cached:
        return cached

    try:
        raw_ratios = _get("ratios-ttm", {"symbol": ticker})
        ratios = raw_ratios[0] if raw_ratios else {}
        if not ratios:
            logger.warning("FMP: ratios-ttm empty for %s", ticker)
    except FmpRateLimitError:
        raise
    except FmpError:
        return None

    profile = (cached or {}).get("profile") or {}
    info = {"profile": profile, "ratios_ttm": ratios}
    cache_fmp_info_set(ticker, info)
    return info


def _fetch_and_cache_key_metrics(ticker: str) -> dict:
    """Fetch key-metrics-ttm and return the first record (or empty dict)."""
    try:
        data = _get("key-metrics-ttm", {"symbol": ticker})
        if not data:
            logger.warning("FMP: key-metrics-ttm empty for %s", ticker)
            return {}
        return data[0]
    except FmpRateLimitError:
        raise
    except FmpError:
        return {}


def _fetch_and_cache_financial_growth(ticker: str) -> dict:
    """Fetch financial-growth and return the most recent record (or empty dict)."""
    try:
        data = _get("financial-growth", {"symbol": ticker})
        if not data:
            logger.warning("FMP: financial-growth empty for %s", ticker)
            return {}
        # Most recent period first
        data.sort(key=lambda x: x.get("date", ""), reverse=True)
        return data[0]
    except FmpRateLimitError:
        raise
    except FmpError:
        return {}


def _fetch_and_cache_eps_surprise(ticker: str) -> Optional[float]:
    """Average EPS surprise (%) over the last four reported quarters.

    Uses FMP's ``earnings-surprises`` endpoint (actual vs estimated EPS) with
    the same semantics as yfinance's ``Surprise(%)``:
    ``(actual - estimated) / abs(estimated) * 100``.  Feeds the quant model's
    EPS Revisions factor.  Cached inside the FMP info entry (a stored ``None``
    marks "fetched but unavailable").
    """
    cached = cache_fmp_info_get(ticker)
    if cached is not None and "eps_surprise" in cached:
        return cached["eps_surprise"]

    try:
        data = _get("earnings-surprises", {"symbol": ticker})
    except FmpRateLimitError:
        raise
    except FmpError:
        return None

    entries = sorted(data, key=lambda x: x.get("date", ""), reverse=True)
    surprises: list[float] = []
    for entry in entries[:4]:
        actual = _safe_float(entry, "actualEarningResult")
        estimated = _safe_float(entry, "estimatedEarning")
        if actual is None or estimated is None or estimated == 0:
            continue
        surprises.append((actual - estimated) / abs(estimated) * 100.0)
    surprise = sum(surprises) / len(surprises) if surprises else None

    info = dict(cached or {})
    info["eps_surprise"] = surprise
    cache_fmp_info_set(ticker, info)
    return surprise


def _shares_outstanding(profile: dict, price: float | None) -> Optional[float]:
    """Return shares outstanding from profile or infer from market cap / price."""
    shares = _safe_float(profile, "sharesOutstanding")
    if shares is not None and shares > 0:
        return shares
    cap = _safe_float(profile, "marketCap") or _safe_float(profile, "mktCap")
    if cap is not None and price is not None and price > 0:
        return cap / price
    return None


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
        data = _get(
            "historical-price-eod/full",
            {"symbol": ticker, "from": two_years_ago, "to": today},
        )
        df = _history_to_df(data)
        if df.empty:
            logger.warning("FMP: historical-price-eod empty for %s", ticker)
        history_json = df.to_json()
        if history_json is not None:
            cache_history_set(ticker, history_json)
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
    cap_val = profile.get("marketCap") or profile.get("mktCap")
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
        logger.warning("FMP: %s", "; ".join(d.errors))
        return d

    # ETF check
    if profile.get("isEtf", False):
        d = StockData(ticker=ticker)
        d.errors.append("ETFs not supported via FMP data source")
        logger.warning("FMP: %s", "; ".join(d.errors))
        return d

    # Market cap validation + filter
    is_valid, cap = _mkt_cap(profile)
    if not is_valid:
        d = StockData(ticker=ticker)
        d.errors.append("no valid market cap from FMP")
        logger.warning("FMP: %s", "; ".join(d.errors))
        return d

    if min_market_cap > 0 and cap < min_market_cap:
        d = StockData(ticker=ticker, market_cap=cap)
        d.errors.append("market cap below threshold")
        logger.warning("FMP: %s", "; ".join(d.errors))
        return d

    # ── 2. Fundamentals (ratios + key metrics + growth) ───────────────────
    info = _fetch_and_cache_fundamentals(ticker)
    ratios = (info or {}).get("ratios_ttm") or {}
    key_metrics = _fetch_and_cache_key_metrics(ticker)
    growth = _fetch_and_cache_financial_growth(ticker)

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

    # Valuation (FMP stable uses *TTM field names)
    data.trailing_pe = _safe_float(ratios, "priceToEarningsRatioTTM")
    data.price_to_book = _safe_float(ratios, "priceToBookRatioTTM")
    data.peg_ratio = _safe_float(ratios, "priceToEarningsGrowthRatioTTM")

    # Quality
    data.return_on_equity = _safe_float(key_metrics, "returnOnEquityTTM")
    data.return_on_assets = _safe_float(key_metrics, "returnOnAssetsTTM")
    data.profit_margin = _safe_float(ratios, "netProfitMarginTTM")
    data.earnings_growth = _safe_float(growth, "epsgrowth")
    data.revenue_growth = _safe_float(growth, "revenueGrowth")

    # Health — FMP reports D/E as a pure ratio (1.5 == 1.5x) while yfinance
    # uses a percentage (152.0 == 1.52x); convert to the yfinance scale so the
    # scorers (which divide by 100) see consistent units across data sources.
    de = _safe_float(ratios, "debtToEquityRatioTTM")
    data.debt_to_equity = de * 100.0 if de is not None else None
    data.current_ratio = _safe_float(ratios, "currentRatioTTM")

    # FCF: compute from FCF/share * shares outstanding
    fcf_ps = _safe_float(ratios, "freeCashFlowPerShareTTM")
    shares = _shares_outstanding(profile, price)
    if fcf_ps is not None and shares is not None and shares > 0:
        data.free_cashflow = fcf_ps * shares

    # Income
    data.dividend_yield = _safe_float(ratios, "dividendYieldTTM")
    data.payout_ratio = _safe_float(ratios, "dividendPayoutRatioTTM")

    # EPS surprise (quant model's EPS Revisions factor)
    data.eps_surprise = _fetch_and_cache_eps_surprise(ticker)

    # Trend + technicals from price history
    _apply_history_common(data, history)

    return data
