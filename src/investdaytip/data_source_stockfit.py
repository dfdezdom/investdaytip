"""StockFit point-in-time (PIT) data source for backtesting.

Fetches annual financial statements where every fiscal period carries its
SEC filing acceptance date (``dateFiled``).  This enables look-ahead-free
historical snapshots: at any backtest date ``D`` only periods whose filing
was accepted on or before ``D`` are used — no fixed reporting-lag
assumption needed.

Endpoints per stock ticker (3 calls):
  - ``/api/financials/income-statement``  — revenue, net income, EPS (+ dateFiled)
  - ``/api/financials/balance-sheet``      — equity, debt, liquidity, shares (+ dateFiled)
  - ``/api/financials/cash-flow-statement`` — free cash flow (+ dateFiled)

Entity-stitching auto-fallback
------------------------------
When a ticker resolves to a new holding entity whose annual series is empty
or short (e.g. ``XOM`` after its 2026 holdco reorganisation), the client
searches the company name among *delisted* entities, probes each candidate
CIK, and keeps the CIK with the longest annual series — so corporate lineage
breaks do not silently drop a decade of fundamentals.

Only US-listed stocks are supported (StockFit is SEC-only).  ETFs and
non-US listings raise/return nothing and must stay on yfinance.

Fundamental insights (live path)
--------------------------------
:func:`fetch_fundamental_insights` powers the opt-in ``--fundamental-insights``
HTML section from three Free-tier chart endpoints (margin stack, earnings
quality, balance-sheet health) — never raises, soft-fails per ticker.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from investdaytip.cache import cache_stockfit_insights_get, cache_stockfit_insights_set

logger = logging.getLogger(__name__)

STOCKFIT_BASE = "https://api.stockfit.io/v1/api"
STOCKFIT_REQUEST_TIMEOUT = 25  # seconds per HTTP request
STOCKFIT_MIN_ANNUAL = 3        # below this, the entity-stitching fallback kicks in
_MAX_STITCH_PROBES = 5         # predecessor CIKs probed per fallback
STOCKFIT_RETRY_DELAYS = [2, 5]
USER_AGENT = "InvestDayTip"

# StockFit Professional tier allows 500 req/min; stay safely below it even
# with parallel workers (≈460 req/min across all threads).
_RATE_LIMIT_INTERVAL = 0.13


class StockfitError(Exception):
    """Non-recoverable StockFit API error (missing key, network, bad ticker)."""


class StockfitRateLimitError(StockfitError):
    """StockFit rate limit reached (HTTP 429)."""


class _RateLimiter:
    """Cross-thread minimum interval between request starts."""

    def __init__(self, min_interval: float) -> None:
        self._min_interval = min_interval
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._last + self._min_interval - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._last = now


_rate_limiter = _RateLimiter(_RATE_LIMIT_INTERVAL)


def check_api_key() -> None:
    """Raise :exc:`StockfitError` with setup instructions when the key is missing."""
    if not os.environ.get("STOCKFIT_API_KEY"):
        raise StockfitError(
            "STOCKFIT_API_KEY environment variable not set. "
            "Get a free key at https://developer.stockfit.io and export it, "
            "e.g. export STOCKFIT_API_KEY=<your-key>"
        )


def _get(path: str, params: dict[str, str] | None = None) -> Any:
    """Perform a StockFit API GET and return the parsed JSON.

    Raises :exc:`StockfitRateLimitError` on HTTP 429 (no retry — the caller
    decides whether to wait) and :exc:`StockfitError` for other failures.
    """
    api_key = os.environ.get("STOCKFIT_API_KEY")
    if not api_key:
        raise StockfitError("STOCKFIT_API_KEY environment variable not set")

    url = f"{STOCKFIT_BASE}/{path}"
    if params:
        url += "?" + "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params.items())

    for attempt in range(len(STOCKFIT_RETRY_DELAYS) + 1):
        _rate_limiter.wait()
        try:
            req = Request(url, headers={"Authorization": f"Bearer {api_key}",
                                        "User-Agent": USER_AGENT})
            with urlopen(req, timeout=STOCKFIT_REQUEST_TIMEOUT) as resp:
                data = json.loads(resp.read().decode())
        except HTTPError as exc:
            if exc.code == 404:
                return []  # unknown ticker — empty series, not an error
            if exc.code == 429:
                raise StockfitRateLimitError(f"StockFit rate limit (HTTP 429): {exc}") from exc
            if attempt < len(STOCKFIT_RETRY_DELAYS):
                time.sleep(STOCKFIT_RETRY_DELAYS[attempt])
                continue
            raise StockfitError(f"StockFit request failed (HTTP {exc.code}): {exc}") from exc
        except (URLError, OSError) as exc:
            if attempt < len(STOCKFIT_RETRY_DELAYS):
                time.sleep(STOCKFIT_RETRY_DELAYS[attempt])
                continue
            raise StockfitError(f"StockFit request failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            if attempt < len(STOCKFIT_RETRY_DELAYS):
                time.sleep(STOCKFIT_RETRY_DELAYS[attempt])
                continue
            raise StockfitError(f"StockFit returned invalid JSON for {path}: {exc}") from exc

        if isinstance(data, dict) and "error" in data:
            # 400s (bad param, unknown CIK) are soft errors → empty result
            logger.debug("StockFit API error for %s: %s", path, data.get("error"))
            return []
        return data
    raise StockfitError(f"StockFit retries exhausted for {path}")


def _safe_fact(facts: dict[str, Any], key: str) -> Optional[float]:
    """Extract a finite float from a StockFit facts dict, else ``None``."""
    val = facts.get(key)
    if isinstance(val, (int, float)) and math.isfinite(val):
        return float(val)
    return None


@dataclass
class PitPeriod:
    """One fiscal period as filed, with its SEC acceptance date."""

    period_end: datetime
    fiscal_year: int
    fiscal_period: str
    date_filed: datetime
    facts: dict[str, Any] = field(default_factory=dict)

    def fact(self, key: str) -> Optional[float]:
        return _safe_fact(self.facts, key)


@dataclass
class PitStatements:
    """Annual statement series for one ticker, newest period first."""

    ticker: str
    cik: Optional[int] = None
    stitched: bool = False  # True when the entity fallback found a predecessor
    income: list[PitPeriod] = field(default_factory=list)
    balance: list[PitPeriod] = field(default_factory=list)
    cash_flow: list[PitPeriod] = field(default_factory=list)


def _parse_periods(raw: Any) -> list[PitPeriod]:
    """Parse a StockFit statements array into :class:`PitPeriod` (newest first)."""
    periods: list[PitPeriod] = []
    if not isinstance(raw, list):
        return periods
    for row in raw:
        if not isinstance(row, dict) or "dateFiled" not in row:
            continue
        try:
            periods.append(
                PitPeriod(
                    period_end=datetime.strptime(str(row["period"])[:10], "%Y-%m-%d"),
                    fiscal_year=int(row.get("fiscalYear") or 0),
                    fiscal_period=str(row.get("fiscalPeriod") or ""),
                    date_filed=datetime.strptime(str(row["dateFiled"])[:10], "%Y-%m-%d"),
                    facts=row.get("facts") or {},
                )
            )
        except (KeyError, ValueError, TypeError) as exc:
            logger.debug("Skipping malformed StockFit period: %s", exc)
    periods.sort(key=lambda p: p.period_end, reverse=True)
    return periods


def _fetch_statements(selector: dict[str, Any]) -> tuple[list[PitPeriod], list[PitPeriod], list[PitPeriod]]:
    """Fetch the three annual statements for a selector (symbol or cik)."""
    inc = _parse_periods(_get("financials/income-statement", {**selector, "period": "annual", "limit": "12"}))
    bal = _parse_periods(_get("financials/balance-sheet", {**selector, "period": "annual", "limit": "12"}))
    cf = _parse_periods(_get("financials/cash-flow-statement", {**selector, "period": "annual", "limit": "12"}))
    return inc, bal, cf


def _lookup_profile(ticker: str) -> dict[str, Any]:
    """Return the StockFit profile for *ticker* (empty dict on failure)."""
    res = _get("lookup/batch", {"symbols": ticker})
    if isinstance(res, dict):
        prof = res.get(ticker)
        if isinstance(prof, dict):
            return prof
    return {}


def _search_queries(name: str) -> list[str]:
    """Candidate search strings for locating predecessor entities.

    A renamed holding company (``"ExxonMobil Holdings Corp"`` vs. the old
    ``"EXXON MOBIL CORP"``) typically only shares its first word with its
    predecessor — and that word may itself be a camel-case concatenation, so
    it is split as well.  Returns up to three queries, most specific first.
    """
    queries: list[str] = [name]
    words = name.split()
    if words:
        first = words[0]
        if len(first) >= 4:
            queries.append(first)
        parts = re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z]+|[a-z]+", first)
        if parts and len(parts[0]) >= 4:
            queries.append(parts[0])
    out: list[str] = []
    for query in queries:
        if query not in out:
            out.append(query)
    return out[:3]


def _candidate_ciks(profile: dict[str, Any], current_cik: Optional[int]) -> list[int]:
    """Find predecessor CIKs by searching the company name among delisted entities.

    Merges (deduplicated) results from every :func:`_search_queries` variant so
    a failing exact-name match still falls through to shorter queries.
    """
    name = profile.get("name") or ""
    if not name:
        return []
    seen: set[int] = set()
    ciks: list[int] = []
    for query in _search_queries(name):
        results = _get(
            "lookup/search",
            {"searchString": query[:50], "includeDelisted": "true", "limit": "25"},
        )
        if not isinstance(results, list):
            continue
        for cand in results:
            if not isinstance(cand, dict):
                continue
            cik = cand.get("cik")
            if (
                isinstance(cik, int)
                and cik != current_cik
                and cand.get("type") == "stock"
                and cik not in seen
            ):
                seen.add(cik)
                ciks.append(cik)
        if len(ciks) >= 8:
            break
    return ciks[:8]


def fetch_pit_statements(ticker: str) -> PitStatements:
    """Fetch annual statements with filing dates for *ticker*.

    Applies the entity-stitching fallback when the directly-resolved entity
    has an empty/short annual series (holdco reorgs, e.g. XOM).

    Per-ticker soft failures (unknown ticker, no data) return a
    :class:`PitStatements` with empty lists — the caller falls back to the
    yfinance path for that ticker.
    """
    selector: dict[str, Any] = {"symbol": ticker}
    inc, bal, cf = _fetch_statements(selector)

    profile: dict[str, Any] = {}
    if len(inc) < STOCKFIT_MIN_ANNUAL:
        profile = _lookup_profile(ticker)
        current_cik = profile.get("cik") if isinstance(profile.get("cik"), int) else None
        # Probe candidate predecessors and keep the longest annual series
        # (never the current short one — all probes use the same selector).
        original_count = len(inc)
        best: Optional[tuple[list[PitPeriod], list[PitPeriod], list[PitPeriod], int]] = None
        for cik in _candidate_ciks(profile, current_cik)[:_MAX_STITCH_PROBES]:
            cand_inc, cand_bal, cand_cf = _fetch_statements({"cik": cik})
            if len(cand_inc) > original_count and (
                best is None or len(cand_inc) > len(best[0])
            ):
                best = (cand_inc, cand_bal, cand_cf, cik)
        if best is not None:
            inc, bal, cf = best[0], best[1], best[2]
            selector = {"cik": best[3]}
            logger.info(
                "StockFit entity fallback for %s: using CIK %s (%d periods, was %d)",
                ticker, best[3], len(inc), original_count,
            )

    resolved_cik = selector.get("cik") if "cik" in selector else (
        profile.get("cik") if isinstance(profile.get("cik"), int) else None
    )
    return PitStatements(
        ticker=ticker,
        cik=resolved_cik,
        stitched="cik" in selector,
        income=inc,
        balance=bal,
        cash_flow=cf,
    )


def pit_fact_asof(
    periods: list[PitPeriod], key: str, as_of: datetime
) -> tuple[Optional[float], Optional[float]]:
    """Value of *key* as known at *as_of*, plus the prior-year value.

    Returns ``(current, previous)`` where ``current`` comes from the latest
    period whose filing was accepted on or before ``as_of`` and ``previous``
    from the period before it in the series.  Either may be ``None``.
    """
    eligible = [p for p in periods if p.date_filed <= as_of]
    if not eligible:
        return None, None
    current = eligible[0].fact(key)
    previous = eligible[1].fact(key) if len(eligible) > 1 else None
    return current, previous


# ── Fundamental insights (live path, Free-tier chart endpoints) ─────────────

INSIGHT_YEARS = 5  # fiscal years requested per chart

_INSIGHT_CHARTS = (
    "financials/chart/revenue-profitability",
    "earnings/chart/quality",
    "financials/chart/balance-sheet-health",
)


@dataclass
class InsightPeriod:
    """Chart-derived fundamentals for one fiscal year."""

    period_end: str  # YYYY-MM-DD
    revenue: Optional[float] = None
    gross_margin: Optional[float] = None
    operating_margin: Optional[float] = None
    net_margin: Optional[float] = None
    fcf_to_ni: Optional[float] = None
    ocf_to_ni: Optional[float] = None
    debt_to_equity: Optional[float] = None
    current_ratio: Optional[float] = None


@dataclass
class FundamentalInsights:
    """Margin / earnings-quality / balance trends for one ticker.

    ``periods`` is sorted newest first (same convention as :class:`PitPeriod`).
    """

    ticker: str
    periods: list[InsightPeriod] = field(default_factory=list)


def _chart_values(raw: Any) -> tuple[list[str], dict[str, list[Any]]]:
    """Extract ``periods`` and ``{series_name: data}`` from a chart payload."""
    if not isinstance(raw, dict):
        return [], {}
    periods = [str(p) for p in raw.get("periods") or []]
    values: dict[str, list[Any]] = {}
    for block in ("series", "rates"):
        entries = raw.get(block)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if (
                isinstance(entry, dict)
                and isinstance(entry.get("name"), str)
                and isinstance(entry.get("data"), list)
            ):
                values[entry["name"]] = entry["data"]
    return periods, values


def _chart_point(values: dict[str, list[Any]], name: str, index: int) -> Optional[float]:
    """Finite value of series *name* at *index*, else ``None``."""
    data = values.get(name)
    if data is None or index >= len(data):
        return None
    return _safe_fact({"value": data[index]}, "value")


def _parse_insights(
    ticker: str,
    revenue_raw: Any,
    quality_raw: Any,
    balance_raw: Any,
) -> Optional[FundamentalInsights]:
    """Merge the three chart payloads into :class:`FundamentalInsights`.

    Returns ``None`` when no period carries data (unknown ticker, ETF,
    non-US listing).  Metrics missing from an individual chart stay ``None``.
    """
    rows: dict[str, InsightPeriod] = {}

    def _row(period_end: str) -> InsightPeriod:
        row = rows.get(period_end)
        if row is None:
            row = InsightPeriod(period_end=period_end)
            rows[period_end] = row
        return row

    periods, values = _chart_values(revenue_raw)
    for i, period_end in enumerate(periods):
        row = _row(period_end)
        row.revenue = _chart_point(values, "Revenue", i)
        row.gross_margin = _chart_point(values, "Gross Margin", i)
        row.operating_margin = _chart_point(values, "Operating Margin", i)
        row.net_margin = _chart_point(values, "Net Margin", i)

    periods, values = _chart_values(quality_raw)
    for i, period_end in enumerate(periods):
        row = _row(period_end)
        row.fcf_to_ni = _chart_point(values, "FCF / Net Income", i)
        row.ocf_to_ni = _chart_point(values, "OCF / Net Income", i)

    periods, values = _chart_values(balance_raw)
    for i, period_end in enumerate(periods):
        row = _row(period_end)
        row.debt_to_equity = _chart_point(values, "Debt to Equity", i)
        row.current_ratio = _chart_point(values, "Current Ratio", i)

    if not rows:
        return None
    ordered = sorted(rows.values(), key=lambda r: r.period_end, reverse=True)
    return FundamentalInsights(ticker=ticker, periods=ordered)


def _insights_from_cache(ticker: str, raw: str) -> Optional[FundamentalInsights]:
    """Rebuild :class:`FundamentalInsights` from cached JSON (``None`` on error)."""
    try:
        payload = json.loads(raw)
        periods = [InsightPeriod(**p) for p in payload.get("periods") or []]
    except (TypeError, ValueError, AttributeError):
        return None
    if not periods:
        return None
    return FundamentalInsights(ticker=ticker, periods=periods)


def fetch_fundamental_insights(ticker: str) -> Optional[FundamentalInsights]:
    """Fetch margin / earnings-quality / balance trends for *ticker*.

    Uses three Free-tier StockFit chart endpoints (annual, ``INSIGHT_YEARS``
    periods).  Never raises: a per-endpoint failure degrades to partial data
    and a fully failed fetch returns ``None`` so the caller can skip the
    ticker.  Complete results are cached for one day.
    """
    cached = cache_stockfit_insights_get(ticker)
    if cached is not None:
        parsed = _insights_from_cache(ticker, cached)
        if parsed is not None:
            return parsed

    results: list[Any] = []
    for path in _INSIGHT_CHARTS:
        try:
            results.append(
                _get(path, {"symbol": ticker, "period": "annual", "limit": str(INSIGHT_YEARS)})
            )
        except StockfitError as exc:
            logger.debug("StockFit insights fetch failed for %s (%s): %s", ticker, path, exc)
            results.append(None)

    insights = _parse_insights(ticker, *results)
    if insights is None:
        return None
    if all(r is not None for r in results):
        try:
            payload = {"periods": [asdict(p) for p in insights.periods]}
            cache_stockfit_insights_set(ticker, json.dumps(payload))
        except (TypeError, ValueError):
            logger.debug("Could not cache StockFit insights for %s", ticker)
    return insights
