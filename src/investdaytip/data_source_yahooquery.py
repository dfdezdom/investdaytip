"""Data fetching from Yahoo Finance via yahooquery.

Wraps yahooquery batch calls and flattens the nested ``all_modules`` response
into the same flat ``info`` dict that ``data_source.py`` expects, so the
existing ``_fetch_stock()`` / ``_fetch_etf()`` helpers can be reused without
duplication.

yahooquery uses Yahoo's internal API endpoints (not HTML scraping) so it is
more resilient to upstream changes and supports efficient batch fetching.
"""

from __future__ import annotations

import logging
import math
import warnings
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
from yahooquery import Ticker

from investdaytip.data_source import (
    AssetData,
    StockData,
    _fetch_etf,
    _fetch_stock,
)

logger = logging.getLogger(__name__)

# Suppress pandas FutureWarning from yahooquery's internal pd.concat calls.
# This is an upstream issue (yahooquery uses an old pandas API) and is not
# actionable for users.
warnings.filterwarnings(
    "ignore",
    message="The behavior of pd.concat",
    category=FutureWarning,
)

# Modules we need for the flat info dict.
# Requesting only these keeps the payload small compared to ``all_modules``.
_REQUIRED_MODULES = [
    "assetProfile",
    "defaultKeyStatistics",
    "financialData",
    "quoteType",
    "summaryDetail",
    "summaryProfile",
    "fundProfile",
    "fundPerformance",
    "earnings",
    "earningsTrend",
]


# ---------------------------------------------------------------------------
# Field mapping — yahooquery nested dict → flat yfinance-style info dict
# ---------------------------------------------------------------------------

# Each entry maps a flat yfinance key to a (module, key) tuple in yahooquery.
# Example: yfinance "trailingPE" → yahooquery summaryDetail["trailingPE"]
_MODULE_KEY_MAP: dict[str, tuple[str, str]] = {
    # Valuation
    "trailingPE": ("summaryDetail", "trailingPE"),
    "forwardPE": ("summaryDetail", "forwardPE"),
    "priceToBook": ("defaultKeyStatistics", "priceToBook"),
    "pegRatio": ("defaultKeyStatistics", "pegRatio"),
    "trailingPegRatio": ("defaultKeyStatistics", "trailingPegRatio"),
    # Quality
    "returnOnEquity": ("financialData", "returnOnEquity"),
    "returnOnAssets": ("financialData", "returnOnAssets"),
    "profitMargins": ("defaultKeyStatistics", "profitMargins"),
    "earningsGrowth": ("financialData", "earningsGrowth"),
    "revenueGrowth": ("financialData", "revenueGrowth"),
    # Health
    "debtToEquity": ("financialData", "debtToEquity"),
    "currentRatio": ("financialData", "currentRatio"),
    "freeCashflow": ("financialData", "freeCashflow"),
    # Income
    "dividendYield": ("summaryDetail", "dividendYield"),
    "payoutRatio": ("summaryDetail", "payoutRatio"),
    # Market context
    "marketCap": ("summaryDetail", "marketCap"),
    "currentPrice": ("financialData", "currentPrice"),
    "regularMarketPrice": ("summaryDetail", "regularMarketPrice"),
    "previousClose": ("summaryDetail", "previousClose"),
    # Names
    "shortName": ("quoteType", "shortName"),
    "longName": ("quoteType", "longName"),
    "sector": ("summaryProfile", "sector"),
    "currency": ("summaryDetail", "currency"),
    "exchange": ("quoteType", "exchange"),
    # Asset type
    "quoteType": ("quoteType", "quoteType"),
    # ETF fields
    "totalAssets": ("summaryDetail", "totalAssets"),
    "annualReportExpenseRatio": ("fundProfile", "annualReportExpenseRatio"),
    "netExpenseRatio": ("defaultKeyStatistics", "netExpenseRatio"),
    "threeYearAverageReturn": ("defaultKeyStatistics", "threeYearAverageReturn"),
    "fiveYearAverageReturn": ("defaultKeyStatistics", "fiveYearAverageReturn"),
    "beta3Year": ("defaultKeyStatistics", "beta3Year"),
    "beta": ("summaryDetail", "beta"),
    "yield": ("summaryDetail", "yield"),
    "trailingAnnualDividendYield": ("summaryDetail", "trailingAnnualDividendYield"),
    "navPrice": ("summaryDetail", "navPrice"),
    "category": ("defaultKeyStatistics", "category"),
    "fundFamily": ("defaultKeyStatistics", "fundFamily"),
}


def _get_nested(data: dict, module: str, key: str) -> Optional[float]:
    """Safely extract a scalar value from a yahooquery module dict.

    Returns ``None`` when the module or key is missing, or the value is not a
    finite number.
    """
    mod = data.get(module)
    if not isinstance(mod, dict):
        return None
    val = mod.get(key)
    if val is None:
        return None
    try:
        f = float(val)
        if math.isfinite(f):
            return f
    except (TypeError, ValueError):
        pass
    return None


def _get_str(data: dict, module: str, key: str) -> Optional[str]:
    """Safely extract a string value from a yahooquery module dict."""
    mod = data.get(module)
    if not isinstance(mod, dict):
        return None
    val = mod.get(key)
    if val is None:
        return None
    return str(val) if val else None


def _yq_modules_to_info(modules: dict) -> dict:
    """Flatten a yahooquery ``all_modules`` response into a yfinance-style ``info`` dict.

    This is the core bridge between the two libraries.  Every field used by
    ``_fetch_stock()`` / ``_fetch_etf()`` in ``data_source.py`` is mapped here.
    """
    info: dict[str, Optional[float | str]] = {}
    for flat_key, (module, key) in _MODULE_KEY_MAP.items():
        # Prefer float extraction for numeric fields, string for text fields.
        if flat_key in (
            "shortName",
            "longName",
            "sector",
            "currency",
            "exchange",
            "quoteType",
            "category",
            "fundFamily",
        ):
            info[flat_key] = _get_str(modules, module, key)
        else:
            info[flat_key] = _get_nested(modules, module, key)

    # quoteType from yahooquery is "EQUITY" / "ETF" — normalise to "EQUITY" / "ETF"
    qt = info.get("quoteType")
    if qt:
        info["quoteType"] = str(qt).upper()

    # yahooquery nests the ETF expense ratio inside fundProfile.feesExpensesInvestment,
    # not directly under fundProfile. It is also reported as a real decimal
    # (0.0003 = 0.03%) while yfinance reports it as a percentage (0.03 = 3%).
    fund_profile = modules.get("fundProfile") if isinstance(modules, dict) else None
    if fund_profile and info.get("annualReportExpenseRatio") is None:
        fees = fund_profile.get("feesExpensesInvestment") if isinstance(fund_profile, dict) else None
        if isinstance(fees, dict):
            er = fees.get("annualReportExpenseRatio")
            if isinstance(er, (int, float)) and math.isfinite(float(er)):
                info["annualReportExpenseRatio"] = float(er) * 100.0

    return info


# ---------------------------------------------------------------------------
# EPS surprise from yahooquery earnings data
# ---------------------------------------------------------------------------


def _yq_earnings_to_eps_surprise(earnings_data: dict | None) -> Optional[float]:
    """Compute average EPS surprise from yahooquery ``earnings`` module.

    yahooquery ``earnings`` contains ``earningsChart.quarterly`` with
    ``actual``, ``estimate`` and ``surprisePct`` for recent quarters.
    We average the ``surprisePct`` values of the last 4 quarters.
    """
    if not isinstance(earnings_data, dict):
        return None
    chart = earnings_data.get("earningsChart")
    if not isinstance(chart, dict):
        return None
    quarterly = chart.get("quarterly")
    if not isinstance(quarterly, list) or not quarterly:
        return None

    surprises: list[float] = []
    for q in quarterly:
        if not isinstance(q, dict):
            continue
        sp = q.get("surprisePct")
        if sp is not None:
            try:
                f = float(sp)
                if math.isfinite(f):
                    surprises.append(f)
            except (TypeError, ValueError):
                continue

    if not surprises:
        return None
    # Average all available (yahooquery already returns the most recent quarters).
    return float(sum(surprises) / len(surprises))


# ---------------------------------------------------------------------------
# History DataFrame normalisation
# ---------------------------------------------------------------------------


def _yq_history_to_dataframe(history: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Convert a yahooquery multi-index history to a single-index DataFrame.

    yahooquery returns ``symbol / date`` multi-index.  We drop the symbol level
    and rename columns to match yfinance conventions (``Close`` → ``Close``,
    ``adjclose`` is used as ``Close`` when auto_adjust semantics are needed).
    """
    if history.empty:
        return pd.DataFrame()

    # If multi-index, slice by ticker
    if isinstance(history.index, pd.MultiIndex) and "symbol" in history.index.names:
        try:
            df = history.loc[ticker].copy()
        except KeyError:
            return pd.DataFrame()
    else:
        df = history.copy()

    # Rename columns to yfinance convention
    # yahooquery provides both "close" and "adjclose"; we prefer "adjclose"
    # (which corresponds to auto_adjust=True in yfinance) and only fall back
    # to "close" when "adjclose" is missing.
    if "adjclose" in df.columns:
        df = df.rename(columns={"adjclose": "Close"})
    elif "close" in df.columns:
        df = df.rename(columns={"close": "Close"})
    for src, dst in (
        ("high", "High"),
        ("low", "Low"),
        ("open", "Open"),
        ("volume", "Volume"),
    ):
        if src in df.columns:
            df = df.rename(columns={src: dst})

    return df


# ---------------------------------------------------------------------------
# Dividend history
# ---------------------------------------------------------------------------


def _yq_dividend_history(ticker: str, start: str | None = None) -> pd.Series | None:
    """Fetch dividend history via yahooquery.

    ``start`` is an ISO date string (e.g. ``"2024-01-01"``).  When ``None``,
    defaults to ~2 years back to cover TTTM computation.
    """
    if start is None:
        start = (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d")
    try:
        t = Ticker(ticker)
        df = t.dividend_history(start=start)
        if df is None or not isinstance(df, pd.DataFrame) or df.empty:
            return None
        # yahooquery returns a DataFrame with a single column "dividends"
        s: pd.Series
        if "dividends" in df.columns:
            col = df["dividends"]
            if isinstance(col, pd.Series):
                s = col.copy()
            else:
                s = pd.Series(dtype=float)
        else:
            s = pd.Series(dtype=float)
        # Reset index to plain date (yahooquery may return MultiIndex with symbol)
        if isinstance(s.index, pd.MultiIndex) and "symbol" in s.index.names:
            s = s.reset_index(level="symbol", drop=True)
        # Ensure index is DatetimeIndex (yahooquery may use datetime.date)
        if not isinstance(s.index, pd.DatetimeIndex):
            s.index = pd.to_datetime(s.index)
        return s
    except Exception as exc:
        logger.debug("yahooquery dividend fetch failed for %s: %s", ticker, exc)
        return None


# ---------------------------------------------------------------------------
# Batch fetching — the main optimisation
# ---------------------------------------------------------------------------


def _fetch_batch_chunk(tickers: list[str]) -> dict[str, AssetData]:
    """Fetch fundamentals + history for a *small* chunk of tickers.

    This is the inner worker used by ``fetch_batch_yq``.  It performs a
    single yahooquery batch call for at most ``_CHUNK_SIZE`` tickers.

    Uses the SQLite cache (``cache_info_get`` / ``cache_history_set``) so
    repeated runs skip redundant network calls.
    """
    from io import StringIO

    from investdaytip.cache import (
        cache_history_get,
        cache_history_set,
        cache_info_get,
        cache_info_set,
    )

    results: dict[str, AssetData] = {}
    tickers_to_fetch: list[str] = []

    # ── Check cache first ───────────────────────────────────────────────
    for tk in tickers:
        cached_info = cache_info_get(tk)
        cached_hist = cache_history_get(tk)
        if cached_info is not None and cached_hist is not None:
            try:
                history = pd.read_json(StringIO(cached_hist))
            except Exception:
                history = pd.DataFrame()
            info = cached_info
            quote_type = (info.get("quoteType") or "").upper()
            if quote_type == "ETF":
                data = _fetch_etf(tk, info, history)
            else:
                data = _fetch_stock(tk, info, history)
            results[tk] = data
        else:
            tickers_to_fetch.append(tk)

    if not tickers_to_fetch:
        # Everything was cached — return immediately.
        return results

    # ── Batch 1: all_modules (fundamentals) ───────────────────────────────
    try:
        t = Ticker(tickers_to_fetch)
        all_modules = t.all_modules
    except Exception as exc:
        logger.warning("yahooquery chunk all_modules failed: %s", exc)
        for tk in tickers_to_fetch:
            d = StockData(ticker=tk)
            d.errors.append(f"yahooquery chunk all_modules failed: {exc}")
            results[tk] = d
        return results

    # ── Batch 2: history (price data) ───────────────────────────────────
    try:
        hist_batch = t.history(period="2y", interval="1d")
    except Exception as exc:
        logger.warning("yahooquery chunk history failed: %s", exc)
        hist_batch = pd.DataFrame()

    # ── Per-ticker processing + cache write ────────────────────────────
    for tk in tickers_to_fetch:
        raw = all_modules.get(tk)
        if isinstance(raw, str):
            d = StockData(ticker=tk)
            d.errors.append(f"yahooquery: {raw}")
            results[tk] = d
            continue

        if not isinstance(raw, dict):
            d = StockData(ticker=tk)
            d.errors.append("yahooquery: unexpected response format")
            results[tk] = d
            continue

        info = _yq_modules_to_info(raw)
        quote_type = (info.get("quoteType") or "").upper()

        # History
        history = pd.DataFrame()
        if not hist_batch.empty:
            history = _yq_history_to_dataframe(hist_batch, tk)

        # EPS surprise from yahooquery earnings data (store in info so it
        # survives the cache round-trip).
        if quote_type != "ETF":
            earnings_data = raw.get("earnings")
            eps_surprise = _yq_earnings_to_eps_surprise(earnings_data)
            if eps_surprise is not None:
                info["epsSurprise"] = eps_surprise

        # Cache both info and history now that we have them
        cache_info_set(tk, info)
        if not history.empty:
            hist_json = history.to_json()
            if hist_json is not None:
                cache_history_set(tk, hist_json)

        # Build the asset
        if quote_type == "ETF":
            data = _fetch_etf(tk, info, history)
        else:
            data = _fetch_stock(tk, info, history)

        results[tk] = data

    return results


# Yahoo's internal API can start to return ``curl: (23)`` errors or hang
# when the response payload exceeds ~50 tickers.  We keep chunks small so
# each call is fast (~5-10 s) and resilient.
_CHUNK_SIZE = 10

# How many chunks to fetch in parallel.  Each chunk is ~10 tickers;
# with 5 workers a universe of 370 tickers finishes in ~7 chunks
# serially per worker instead of 37 chunks serially.
_MAX_CHUNK_WORKERS = 5


def fetch_batch_yq(
    tickers: list[str],
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> dict[str, AssetData]:
    """Fetch fundamentals + history for many tickers in chunked API calls.

    Splits ``tickers`` into chunks of at most ``_CHUNK_SIZE`` and calls
    yahooquery for each chunk.  Chunks are processed in parallel with a
    small thread pool so large universes finish in a reasonable time.

    If ``progress_cb`` is provided, it is called after each chunk finishes
    with ``(done, total, ticker)`` where *ticker* is the first ticker of
    the chunk that just completed.

    This avoids the ``curl: (23) Failure writing output to destination``
    error that occurs with very large batches.
    """
    if not tickers:
        return {}

    results: dict[str, AssetData] = {}
    total = len(tickers)
    done = 0

    chunks: list[list[str]] = []
    for start in range(0, len(tickers), _CHUNK_SIZE):
        chunks.append(tickers[start : start + _CHUNK_SIZE])

    if len(chunks) == 1:
        # Fast path: a single chunk does not need a thread pool.
        return _fetch_batch_chunk(chunks[0])

    import concurrent.futures
    _CHUNK_TIMEOUT = 20.0  # seconds per chunk
    future_to_chunk = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=_MAX_CHUNK_WORKERS) as pool:
        futures = []
        for chunk in chunks:
            fut = pool.submit(_fetch_batch_chunk, chunk)
            futures.append(fut)
            future_to_chunk[fut] = chunk

        for future in concurrent.futures.as_completed(futures):
            chunk = future_to_chunk[future]
            try:
                chunk_results = future.result(timeout=_CHUNK_TIMEOUT)
                results.update(chunk_results)
            except concurrent.futures.TimeoutError:
                logger.warning("yahooquery chunk timed out after %.0fs", _CHUNK_TIMEOUT)
                for tk in chunk:
                    results[tk] = StockData(
                        ticker=tk,
                        errors=[f"yahooquery chunk timed out after {_CHUNK_TIMEOUT:.0f}s"],
                    )
            except Exception as exc:
                logger.warning("yahooquery chunk future failed: %s", exc)
                for tk in chunk:
                    results[tk] = StockData(
                        ticker=tk,
                        errors=[f"yahooquery chunk failed: {exc}"],
                    )

            # Update progress bar one ticker at a time so it feels smooth.
            # Only count successfully fetched tickers; failed ones will be
            # counted by the fallback loop in recommender.py.
            for tk in chunk:
                if tk in results and not results[tk].errors:
                    done += 1
                    if progress_cb:
                        progress_cb(done, total, tk)

    return results


# ---------------------------------------------------------------------------
# Single-ticker fetch (for fallback / parity with data_source.py)
# ---------------------------------------------------------------------------


def fetch_asset_yq(ticker: str, min_market_cap: float = 0.0) -> AssetData:
    """Fetch a single ticker via yahooquery, with the same signature as ``fetch_asset``.

    This is a thin wrapper around ``fetch_batch_yq`` for API parity.  The
    ``min_market_cap`` argument is accepted for compatibility but the actual
    filtering is performed by the caller (``recommend()``).
    """
    batch = fetch_batch_yq([ticker])
    return batch.get(ticker, StockData(ticker=ticker, errors=["yahooquery: no result"]))


# ---------------------------------------------------------------------------
# Index fetch (for advisor macro indicators)
# ---------------------------------------------------------------------------


def fetch_index_yq(ticker: str) -> Optional[float]:
    """Fetch latest close of any index via yahooquery.

    Returns ``None`` on failure so the caller can fallback to yfinance.
    """
    try:
        t = Ticker(ticker)
        hist = t.history(period="5d", interval="1d")
        if hist is None or hist.empty:
            return None
        df = _yq_history_to_dataframe(hist, ticker)
        if df.empty or "Close" not in df:
            return None
        val = float(df["Close"].iloc[-1])
        if math.isfinite(val):
            return val
    except Exception as exc:
        logger.debug("yahooquery index fetch failed for %s: %s", ticker, exc)
    return None


def check_yahooquery_available() -> bool:
    """Probe whether yahooquery can reach Yahoo's API right now.

    Returns ``True`` if a lightweight request succeeds, ``False`` otherwise.
    The caller can use this to skip the batch path and fall back to yfinance
    immediately when Yahoo's API is unreachable.
    """
    try:
        t = Ticker("SPY")
        hist = t.history(period="1d", interval="1d")
        return hist is not None and not hist.empty
    except Exception as exc:
        logger.debug("yahooquery availability check failed: %s", exc)
    return False
