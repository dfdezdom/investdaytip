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
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pandas as pd

from investdaytip.cache import (
    cache_history_get,
    cache_history_set,
    cache_stockfit_info_get,
    cache_stockfit_info_set,
    cache_stockfit_insights_get,
    cache_stockfit_insights_set,
    cache_stockfit_plan_get,
    cache_stockfit_plan_set,
    cache_stockfit_research_get,
    cache_stockfit_research_set,
)
from investdaytip.data_source import (
    AssetData,
    StockData,
    _apply_history_common,
    _derive_stock_data,
    _first,
    _Fundamentals,
)

logger = logging.getLogger(__name__)

STOCKFIT_BASE = "https://api.stockfit.io/v1/api"
STOCKFIT_REQUEST_TIMEOUT = 25  # seconds per HTTP request
STOCKFIT_MIN_ANNUAL = 3        # below this, the entity-stitching fallback kicks in
_MAX_STITCH_PROBES = 5         # predecessor CIKs probed per fallback
STITCH_MAX_PERIOD_AGE_DAYS = 550  # ≈18 months — see _series_is_recent()
STOCKFIT_RETRY_DELAYS = [2, 5]
USER_AGENT = "InvestDayTip"

# StockFit Professional tier allows 500 req/min; stay safely below it even
# with parallel workers (≈460 req/min across all threads).
_RATE_LIMIT_INTERVAL = 0.13


class StockfitError(Exception):
    """Non-recoverable StockFit API error (missing key, network, bad ticker)."""


class StockfitRateLimitError(StockfitError):
    """StockFit rate limit reached (HTTP 429)."""


class StockfitPlanError(StockfitError):
    """Endpoint not available on the caller's plan (HTTP 403)."""


# ── Tier awareness ───────────────────────────────────────────────────────────
# InvestDayTip works on every StockFit tier — and without a key at all:
# features degrade gracefully and never fabricate.  StockFit exposes no plan
# endpoint, but gated endpoints answer HTTP 403 ("Feature not available on
# current plan"), so the plan is detected by probing the tier boundaries.

PLAN_ORDER: dict[str, int] = {
    "none": -1, "unknown": -1, "free": 0, "starter": 1, "stock": 2, "pro": 3,
}

#: capability -> minimum plan that unlocks it (``None`` = works without a key)
CAPABILITIES: dict[str, Optional[str]] = {
    "pit_statements": None,          # backtest --pit-source stockfit + snapshot
    "fundamental_insights": "free",  # --fundamental-insights charts (any key)
    "deep_dive_summary": "starter",  # company/research-summary
    "economic_model": "stock",       # company/economic-model
    "footnotes": "pro",              # footnotes/* — rich devil's advocate
    "live_source": "starter",        # --data-source stockfit (quota: ~6 calls/ticker)
}

#: probed highest tier first: (plan, gated endpoint)
_PLAN_PROBES: tuple[tuple[str, str], ...] = (
    ("pro", "footnotes/concentration"),
    ("stock", "company/economic-model"),
    ("starter", "financials/scores"),
)

_plan_cache: Optional[str] = None


def detect_plan(force: bool = False) -> str:
    """Detect the caller's StockFit plan: none/free/starter/stock/pro.

    ``"none"`` without a key, ``"unknown"`` when detection cannot run (network
    errors — never blocks a feature), otherwise the highest tier whose probe
    endpoint answers.  Cached in-process and in the SQLite cache (1 day).
    """
    global _plan_cache
    if not os.environ.get("STOCKFIT_API_KEY"):
        return "none"
    if _plan_cache is not None and not force:
        return _plan_cache
    cached = cache_stockfit_plan_get()
    if cached is not None and not force:
        _plan_cache = cached
        return cached

    plan = "free"
    for candidate, path in _PLAN_PROBES:
        try:
            _get(path, {"symbol": "AAPL"})
            plan = candidate
            break
        except StockfitPlanError:
            continue
        except StockfitError:
            plan = "unknown"
            break
    _plan_cache = plan
    if plan != "unknown":
        cache_stockfit_plan_set(plan)
    return plan


def plan_allows(plan: str, capability: str) -> bool:
    """True when *plan* unlocks *capability* (``"unknown"`` never blocks).

    ``None`` requirements are keyless-capable features (e.g. PIT backtests
    from the local snapshot) — they work on every plan, including no key.
    """
    required = CAPABILITIES.get(capability, "missing")
    if required == "missing":
        raise KeyError(f"Unknown StockFit capability: {capability!r}")
    if required is None:
        return True
    if plan == "unknown":
        return True  # detection failed — try and degrade at the fetch
    return PLAN_ORDER.get(plan, -1) >= PLAN_ORDER[required]


def stockfit_status() -> dict[str, Any]:
    """Key presence, detected plan and the capability matrix (CLI/tests)."""
    plan = detect_plan()
    return {
        "key_present": bool(os.environ.get("STOCKFIT_API_KEY")),
        "plan": plan,
        "capabilities": {cap: plan_allows(plan, cap) for cap in CAPABILITIES},
    }


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
            if exc.code == 403:
                # Plan gate ("Feature not available on current plan") — no retry.
                raise StockfitPlanError(
                    f"StockFit feature not available on current plan ({path})"
                ) from exc
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


def _norm_ticker(ticker: str) -> str:
    """Canonical ticker spelling (``meta`` → ``META``).

    StockFit's ``lookup/batch`` keys its response by the uppercase symbol and
    every cache key / snapshot filename is case-sensitive, so a lowercase
    query used to miss all of them and silently fall back to yfinance.
    """
    return ticker.strip().upper()


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


def _series_is_recent(periods: list[PitPeriod], *, now: Optional[datetime] = None) -> bool:
    """True when *periods*' latest fiscal period is recent enough for a **live** predecessor.

    The entity-stitching fallback exists for holdco reorgs (XOM → ExxonMobil),
    where the predecessor keeps filing — its last fiscal year is therefore
    never more than about a year and a half old.  It must never graft a
    **defunct, unrelated filer**: a name search for "Sandisk Corp" also returns
    the old SanDisk (CIK 1000180, acquired by WDC in 2016) whose 12-FY series
    ends at FY2015, longer than the current Sandisk Corp's (CIK 2023554,
    spun off 2025) — so SNDK ended up scoring 2015 fundamentals (growth read
    −61% with the TTM overlay, +2843% without it).

    550 days (≈18 months) is the widest legitimate gap: an FY ending in June
    whose 10-K lands in September is ~450 days old in the days just before the
    next one replaces it.  Anything older stopped filing → not a predecessor.
    """
    ends = [p.period_end for p in periods if p.period_end]
    if not ends:
        return False
    return ((now or datetime.now()) - max(ends)).days <= STITCH_MAX_PERIOD_AGE_DAYS


def _fetch_statements(selector: dict[str, Any]) -> tuple[list[PitPeriod], list[PitPeriod], list[PitPeriod]]:
    """Fetch the three annual statements for a selector (symbol or cik)."""
    inc = _parse_periods(_get("financials/income-statement", {**selector, "period": "annual", "limit": "12"}))
    bal = _parse_periods(_get("financials/balance-sheet", {**selector, "period": "annual", "limit": "12"}))
    cf = _parse_periods(_get("financials/cash-flow-statement", {**selector, "period": "annual", "limit": "12"}))
    return inc, bal, cf


def _lookup_profile(ticker: str) -> dict[str, Any]:
    """Return the StockFit profile for *ticker* (empty dict on failure).

    ``lookup/batch`` keys its response by the uppercase symbol regardless of
    the query spelling (``meta`` → ``{"META": ...}``), so the key match is
    case-insensitive.
    """
    try:
        res = _get("lookup/batch", {"symbols": ticker})
    except StockfitError as exc:
        logger.debug("lookup/batch failed for %s: %s", ticker, exc)
        return {}
    if isinstance(res, dict):
        wanted = _norm_ticker(ticker)
        for key, prof in res.items():
            if _norm_ticker(str(key)) == wanted and isinstance(prof, dict):
                return prof
    return {}


def _search_queries(name: str) -> list[str]:
    """Candidate search strings for locating predecessor entities.

    A renamed holding company (``"ExxonMobil Holdings Corp"`` vs. the old
    ``"EXXON MOBIL CORP"``) typically only shares its first word with its
    predecessor — and that word may itself be a camel-case concatenation, so
    it is split as well.  Returns up to three queries, most specific first.

    Queries are punctuation-stripped: ``lookup/search`` rejects names
    containing ``,`` ``/`` ``&`` ``(`` ``)`` with HTTP 400 (verified
    2026-09-26 — e.g. ``"BlackRock, Inc."`` 400s while ``"BlackRock Inc"``
    resolves).
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
        clean = re.sub(r"[/,&()]+", " ", query)
        clean = re.sub(r"\s+", " ", clean).strip().rstrip(".")
        if clean and clean not in out:
            out.append(clean)
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
        try:
            results = _get(
                "lookup/search",
                {"searchString": query[:50], "includeDelisted": "true", "limit": "25"},
            )
        except StockfitError as exc:
            # Best-effort: a rejected query must not abort stitching (and
            # never the ticker's by-symbol series).
            logger.debug("lookup/search failed for %r: %s", query, exc)
            continue
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


def _fetch_pit_statements_live(ticker: str) -> PitStatements:
    """Fetch annual statements with filing dates for *ticker* (network).

    Applies the entity-stitching fallback when the directly-resolved entity
    has an empty/short annual series (holdco reorgs, e.g. XOM).  A candidate
    predecessor must also pass :func:`_series_is_recent` — otherwise a name
    match to a long-dead filer (old SanDisk) outranks the live company.

    Raises :exc:`StockfitError` on endpoint failures; per-ticker "no data"
    results come back as empty lists.
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
            try:
                cand_inc, cand_bal, cand_cf = _fetch_statements({"cik": cik})
            except StockfitError as exc:
                logger.debug("Stitch probe CIK %s failed for %s: %s", cik, ticker, exc)
                continue
            if not _series_is_recent(cand_inc):
                logger.debug(
                    "Stitch candidate CIK %s skipped for %s: latest fiscal period %s is stale",
                    cik, ticker, max((p.period_end for p in cand_inc), default=None),
                )
                continue
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


# ── Local PIT snapshot (backtests without an API key) ───────────────────────

SNAPSHOT_DIR_ENV = "STOCKFIT_PIT_SNAPSHOT_DIR"


def snapshot_dir() -> Path:
    """Directory holding per-ticker PIT statement snapshots.

    Defaults to ``~/.investdaytip/pit``; override with the
    ``STOCKFIT_PIT_SNAPSHOT_DIR`` environment variable (tests point it at a
    throwaway directory so the real home is never touched).
    """
    override = os.environ.get(SNAPSHOT_DIR_ENV)
    if override:
        return Path(override)
    return Path.home() / ".investdaytip" / "pit"


def snapshot_available() -> bool:
    """True when at least one snapshot file exists locally."""
    try:
        directory = snapshot_dir()
        return directory.is_dir() and any(directory.glob("*.json"))
    except OSError:
        return False


def check_pit_access() -> None:
    """Raise :exc:`StockfitError` unless a key **or** a local snapshot exists."""
    if os.environ.get("STOCKFIT_API_KEY") or snapshot_available():
        return
    raise StockfitError(
        "STOCKFIT_API_KEY not set and no local PIT snapshot found at "
        f"{snapshot_dir()}. Either export STOCKFIT_API_KEY "
        "(free key at https://developer.stockfit.io) or build a snapshot "
        "while your key is valid: python scripts/pit_snapshot.py"
    )


def _snapshot_path(ticker: str) -> Path:
    return snapshot_dir() / f"{_norm_ticker(ticker)}.json"


def _fresh_pit_snapshot(ticker: str, max_age_days: float = 7.0) -> Optional[PitStatements]:
    """Local PIT snapshot when it is younger than *max_age_days*.

    Filings arrive quarterly, so a week-old snapshot is fresh enough for the
    live source — and reusing it means warm `--data-source stockfit` runs cost
    ~2 calls per ticker instead of 6.  `fetch_pit_statements()` refreshes the
    snapshot on every live fetch, so the cache heals itself.
    """
    try:
        path = _snapshot_path(ticker)
        if path.is_file() and (time.time() - path.stat().st_mtime) < max_age_days * 86400:
            return load_pit_snapshot(ticker)
    except OSError:
        pass
    return None


def _serialize_periods(periods: list[PitPeriod]) -> list[dict[str, Any]]:
    return [
        {
            "period_end": p.period_end.strftime("%Y-%m-%d"),
            "fiscal_year": p.fiscal_year,
            "fiscal_period": p.fiscal_period,
            "date_filed": p.date_filed.strftime("%Y-%m-%d"),
            "facts": p.facts,
        }
        for p in periods
    ]


def _deserialize_periods(raw: Any) -> list[PitPeriod]:
    """Rebuild a sorted (newest first) period list from snapshot JSON."""
    out: list[PitPeriod] = []
    if not isinstance(raw, list):
        return out
    for row in raw:
        if not isinstance(row, dict):
            continue
        try:
            out.append(
                PitPeriod(
                    period_end=datetime.strptime(str(row["period_end"])[:10], "%Y-%m-%d"),
                    fiscal_year=int(row.get("fiscal_year") or 0),
                    fiscal_period=str(row.get("fiscal_period") or ""),
                    date_filed=datetime.strptime(str(row["date_filed"])[:10], "%Y-%m-%d"),
                    facts=row.get("facts") or {},
                )
            )
        except (KeyError, ValueError, TypeError) as exc:
            logger.debug("Skipping malformed snapshot period: %s", exc)
    out.sort(key=lambda p: p.period_end, reverse=True)
    return out


def save_pit_snapshot(statements: PitStatements) -> bool:
    """Persist *statements* to the snapshot directory (atomic, best-effort).

    Empty series are never written, so a failed live fetch cannot wipe an
    existing snapshot.  Returns ``True`` when the file was written.
    """
    if not (statements.income or statements.balance or statements.cash_flow):
        return False
    payload = {
        "ticker": statements.ticker,
        "cik": statements.cik,
        "stitched": statements.stitched,
        "saved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "income": _serialize_periods(statements.income),
        "balance": _serialize_periods(statements.balance),
        "cash_flow": _serialize_periods(statements.cash_flow),
    }
    try:
        path = _snapshot_path(statements.ticker)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(path)
        return True
    except OSError:
        logger.debug("Could not write PIT snapshot for %s", statements.ticker, exc_info=True)
        return False


def load_pit_snapshot(ticker: str) -> Optional[PitStatements]:
    """Load *ticker*'s snapshot from disk (``None`` when missing/corrupt/empty)."""
    ticker = _norm_ticker(ticker)
    try:
        payload = json.loads(_snapshot_path(ticker).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    income = _deserialize_periods(payload.get("income"))
    if not income:
        return None
    stitched = bool(payload.get("stitched"))
    if stitched and not _series_is_recent(income):
        # Built from the wrong predecessor (a defunct name-match — see
        # _series_is_recent): discard it so the next run refetches the right
        # entity, or degrades to yfinance, instead of scoring a company that
        # stopped filing a decade ago forever.
        logger.warning(
            "Discarding stale stitched PIT snapshot for %s: latest fiscal period %s",
            ticker, income[0].period_end.date(),
        )
        return None
    cik = payload.get("cik")
    return PitStatements(
        ticker=ticker,
        cik=cik if isinstance(cik, int) else None,
        stitched=stitched,
        income=income,
        balance=_deserialize_periods(payload.get("balance")),
        cash_flow=_deserialize_periods(payload.get("cash_flow")),
    )


def fetch_pit_statements(ticker: str) -> PitStatements:
    """Fetch annual statements with filing dates for *ticker*.

    Resolution order, each step soft-failing to the next:

    1. live StockFit when ``STOCKFIT_API_KEY`` is set — a successful live
       fetch also refreshes the local snapshot;
    2. the local PIT snapshot (:func:`snapshot_dir`);
    3. an empty :class:`PitStatements` — the caller falls back to the classic
       fixed-lag path for that ticker.

    Never raises for missing access; use :func:`check_pit_access` for the
    fail-fast check at startup.
    """
    ticker = _norm_ticker(ticker)
    live: Optional[PitStatements] = None
    if os.environ.get("STOCKFIT_API_KEY"):
        try:
            live = _fetch_pit_statements_live(ticker)
        except StockfitError as exc:
            logger.debug(
                "StockFit live fetch failed for %s (%s); trying snapshot", ticker, exc
            )
    if live is not None and live.income:
        save_pit_snapshot(live)  # best-effort — never breaks the fetch
        return live
    snap = load_pit_snapshot(ticker)
    if snap is not None:
        logger.debug("StockFit PIT snapshot used for %s (%d periods)", ticker, len(snap.income))
        return snap
    return live if live is not None else PitStatements(ticker=ticker)


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


def pit_fact_n_back(
    periods: list[PitPeriod], key: str, as_of: datetime, years_back: int
) -> Optional[float]:
    """Value of *key* ``years_back`` fiscal years before the as-of period.

    Same look-ahead discipline as :func:`pit_fact_asof`: only periods filed
    on or before *as_of* are considered.  ``None`` when the series is shorter.
    """
    eligible = [p for p in periods if p.date_filed <= as_of]
    if len(eligible) <= years_back:
        return None
    return eligible[years_back].fact(key)


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
    ticker = _norm_ticker(ticker)
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


# ── Research summary (deep-dive) ─────────────────────────────────────────────


@dataclass
class ResearchSummary:
    """Aggregated per-ticker research data from StockFit (Starter tier).

    ``profile``: company details.  ``snapshot``: EPS, margins, returns,
    health scores, predicted dates.  ``key_metrics``: sector-aware metrics.
    Sections may be empty dicts when StockFit omits them.
    """

    ticker: str
    profile: dict[str, Any] = field(default_factory=dict)
    snapshot: dict[str, Any] = field(default_factory=dict)
    key_metrics: dict[str, Any] = field(default_factory=dict)


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def fetch_research_summary(ticker: str) -> Optional[ResearchSummary]:
    """Fetch StockFit's aggregated research summary for *ticker*.

    Single ``company/research-summary`` call (Starter tier).  Never raises:
    returns ``None`` on failure so the deep-dive report can degrade to its
    keyless local sections.  Successful fetches are cached for one day.
    """
    ticker = _norm_ticker(ticker)
    cached = cache_stockfit_research_get(ticker)
    if cached is not None:
        try:
            payload = json.loads(cached)
            return ResearchSummary(
                ticker=ticker,
                profile=_dict_or_empty(payload.get("profile")),
                snapshot=_dict_or_empty(payload.get("snapshot")),
                key_metrics=_dict_or_empty(payload.get("key_metrics")),
            )
        except (TypeError, ValueError):
            logger.debug("Corrupt research-summary cache for %s", ticker)

    try:
        raw = _get("company/research-summary", {"symbol": ticker})
    except StockfitError as exc:
        logger.debug("StockFit research-summary failed for %s: %s", ticker, exc)
        return None
    if not isinstance(raw, dict) or not raw:
        return None

    summary = ResearchSummary(
        ticker=ticker,
        profile=_dict_or_empty(raw.get("profile")),
        snapshot=_dict_or_empty(raw.get("snapshot")),
        key_metrics=_dict_or_empty(raw.get("keyMetrics")),
    )
    try:
        cache_stockfit_research_set(
            ticker,
            json.dumps(
                {
                    "profile": summary.profile,
                    "snapshot": summary.snapshot,
                    "key_metrics": summary.key_metrics,
                }
            ),
        )
    except (TypeError, ValueError):
        logger.debug("Could not cache StockFit research summary for %s", ticker)
    return summary


# ── Live data source (`--data-source stockfit`) ──────────────────────────────

#: StockFit reports GICS sector names; map to yfinance-style names so the
#: ``-s`` sector filter and the advisor sector tilt behave identically.
_GICS_TO_YFINANCE_SECTOR = {
    "Information Technology": "Technology",
    "Consumer Discretionary": "Consumer Cyclical",
    "Consumer Staples": "Consumer Defensive",
    "Health Care": "Healthcare",
    "Financials": "Financial Services",
    "Materials": "Basic Materials",
}


def _ttm_facts_from(raw: Any) -> dict[str, Any]:
    """Extract the ``facts`` block from a ``period=ttm`` statements response."""
    if isinstance(raw, dict) and isinstance(raw.get("data"), list):
        raw = raw["data"]
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    if isinstance(raw, dict):
        facts = raw.get("facts")
        if isinstance(facts, dict):
            return facts
    return {}


def _fetch_ttm_facts(ticker: str, pit: Optional[PitStatements] = None) -> dict[str, Any]:
    """Trailing-twelve-month facts for the live source (best-effort).

    ``financials/income-statement?period=ttm`` serves flows summed over the
    last four reported quarters together with the latest quarter's balances
    and share counts, in one merged ``facts`` block.  Queried by symbol first —
    the *current* entity has the freshest quarters — and retried on the PIT
    CIK (which may be a stitched predecessor) only when the symbol yields
    nothing.  Any failure returns ``{}`` so the caller keeps the as-filed
    figures; a rate limit propagates so the ticker falls back to yfinance.
    """
    selectors: list[dict[str, str]] = [{"symbol": ticker}]
    if pit is not None and pit.cik:
        selectors.append({"cik": str(pit.cik)})
    for selector in selectors:
        try:
            raw = _get("financials/income-statement", {**selector, "period": "ttm"})
        except StockfitRateLimitError:
            raise
        except StockfitError as exc:
            logger.debug("StockFit TTM fetch failed for %s (%s): %s", ticker, selector, exc)
            continue
        facts = _ttm_facts_from(raw)
        if facts:
            return facts
    return {}


def _apply_ttm_overlay(fund: _Fundamentals, facts: dict[str, Any]) -> None:
    """Switch *fund*'s current-period **levels** to TTM figures, in place.

    Flows come from the last four reported quarters and balances / share
    counts from the latest quarter — the same trailing semantics yfinance
    serves, so P/E, P/B, ROE and margins line up across data sources.  The
    displaced as-filed figure is parked in the matching ``*_asfiled`` field
    and the comparison chain (``*_prev``, ``eps_prev``, ``eps_prev2``) is
    deliberately **left alone**, so growth, the improvement flags and EPS
    acceleration keep comparing non-overlapping fiscal years (FY(n) vs
    FY(n−1) vs FY(n−2)) — the same basis the backtest builders use.

    Shifting the chain instead (the pre-2026-09 behaviour) compares a TTM
    window against the latest filed year, which mixes spans and collapses to
    exactly **0%** whenever no quarter has been filed since the fiscal year
    closed: MSFT's FY2026 closed 30-jun and was filed 29-jul, so TTM ≡
    FY2026 → ``earnings_growth = 0.0`` → Growth 18 → "growth flagged as
    disqualifying" → the score got capped although the company grew +31%.

    A fact missing from the TTM block keeps its as-filed value in the plain
    field, leaves ``*_asfiled`` as ``None`` and the derivation falls back to
    the as-filed figure (``_first`` never discards a real ``0.0``).
    """

    def _override(cur_attr: str, asfiled_attr: str, *fact_keys: str) -> None:
        val = _first(*(_safe_fact(facts, key) for key in fact_keys))
        if val is None:
            return
        setattr(fund, asfiled_attr, getattr(fund, cur_attr))
        setattr(fund, cur_attr, val)

    _override("ni", "ni_asfiled", "netIncome")
    _override("rev", "rev_asfiled", "revenue")
    _override("gross_profit", "gross_profit_asfiled", "grossProfit")
    _override("total_assets", "total_assets_asfiled", "assets")
    eps_ttm = _first(_safe_fact(facts, "epsDiluted"), _safe_fact(facts, "eps"))
    if eps_ttm is not None:
        fund.eps_asfiled = fund.eps
        fund.eps = eps_ttm
    fund.fcf = _first(_safe_fact(facts, "freeCashFlow"), fund.fcf)
    fund.equity = _first(_safe_fact(facts, "stockholdersEquity"), fund.equity)
    fund.total_debt = _first(_safe_fact(facts, "totalDebt"), fund.total_debt)
    fund.curr_assets = _first(_safe_fact(facts, "currentAssets"), fund.curr_assets)
    fund.curr_liab = _first(_safe_fact(facts, "currentLiabilities"), fund.curr_liab)
    fund.shares = _first(
        _safe_fact(facts, "sharesOutstanding"),
        _safe_fact(facts, "currentSharesOutstanding"),
        _safe_fact(facts, "sharesIssued"),
        fund.shares,
    )


def fetch_asset_stockfit(
    ticker: str, min_market_cap: float = 0.0, with_improvements: bool = True
) -> AssetData:
    """StockFit-backed stock fetch — the ``--data-source stockfit`` workhorse.

    **US stocks only** (StockFit covers US filers; ETFs are not supported,
    like the FMP source) and it needs a key plus a Starter plan.  Caching makes
    warm runs ~free: profile + dividends + TTM facts in the 1-day
    `stockfit_info` entry, statements from the local PIT snapshot when under 7
    days old, prices from the shared 15-minute history cache (first run ≈ 7
    calls per ticker).  Builds the same ``StockData`` as the yfinance path via
    the shared ``_derive_stock_data()``; the YoY-improvement flags come free
    from the statements (``with_improvements`` is accepted for interface
    compatibility and always computed).  Forward P/E, PEG and ``eps_surprise``
    stay ``None`` — StockFit has no analyst estimates — so the quant model
    scores Value with one metric fewer and EPS Revisions neutral.

    Fetch failures raise :exc:`StockfitError` so the caller can fall back to
    yfinance; user errors (ETF ticker, below market-cap threshold) are
    returned as ``data.errors`` and skipped by the orchestrator.

    **Semantics (2026-09-27):** current fundamentals are **TTM** — flows from
    the last four reported quarters, balances from the latest quarter — the
    same trailing semantics yfinance serves, so ``trailing_pe``,
    ``price_to_book``, ``roe``, ``profit_margin`` & co. line up with the
    yfinance source (MU P/E 24.5 vs 24.45 after the switch; the previous
    as-filed fiscal-year basis showed 142.6 because a stale 10-K EPS lagged
    an earnings explosion).  The YoY comparisons (``earnings_growth``,
    ``revenue_growth``, the improvement flags, ``eps_acceleration``) never
    read the TTM figures: they compare the **as-filed fiscal years**
    (FY(n) vs FY(n−1) vs FY(n−2)), the same non-overlapping basis the
    backtest builders use.  Trend fields match yfinance
    exactly (same adjusted price series).  No analyst estimates exist in
    StockFit, so ``forward_pe``/``peg_ratio``/``eps_surprise`` are always
    ``None``.
    """
    ticker = _norm_ticker(ticker)
    now = datetime.now()
    data = StockData(ticker=ticker)

    # 1) Profile + dividends + TTM facts — all change rarely, one 1-day cache
    # entry (same idea as the FMP `fmp_info` key).
    cached_info = _load_stockfit_info(ticker)
    profile: dict[str, Any] = cached_info[0] if cached_info else {}
    ttm_div: Optional[float] = cached_info[1] if cached_info else None
    ttm_facts: Optional[dict[str, Any]] = cached_info[2] if cached_info else None
    info_dirty = cached_info is None
    if not profile:
        profile = _lookup_profile(ticker)
        if not profile:
            raise StockfitError(f"StockFit: unknown ticker {ticker}")
    ptype = (profile.get("type") or "stock").lower()
    if ptype != "stock":
        data.errors.append(f"--data-source stockfit supports stocks only (type={ptype})")
        return data

    # 2) Statements — reuses the PIT client (which also refreshes the local
    # snapshot), giving current + previous fiscal year facts.  A fresh local
    # snapshot short-circuits the fetch entirely.
    pit = _fresh_pit_snapshot(ticker) or fetch_pit_statements(ticker)
    if not pit.income:
        raise StockfitError(f"StockFit: no statements for {ticker}")
    fund = _Fundamentals(
        ni=pit_fact_asof(pit.income, "netIncome", now)[0],
        rev=pit_fact_asof(pit.income, "revenue", now)[0],
        eps=_first(
            pit_fact_asof(pit.income, "epsDiluted", now)[0],
            pit_fact_asof(pit.income, "eps", now)[0],
        ),
        eps_prev=_first(
            pit_fact_asof(pit.income, "epsDiluted", now)[1],
            pit_fact_asof(pit.income, "eps", now)[1],
        ),
        eps_prev2=_first(
            pit_fact_n_back(pit.income, "epsDiluted", now, 2),
            pit_fact_n_back(pit.income, "eps", now, 2),
        ),
        ni_prev=pit_fact_asof(pit.income, "netIncome", now)[1],
        rev_prev=pit_fact_asof(pit.income, "revenue", now)[1],
        gross_profit=pit_fact_asof(pit.income, "grossProfit", now)[0],
        gross_profit_prev=pit_fact_asof(pit.income, "grossProfit", now)[1],
        total_assets_prev=pit_fact_asof(pit.balance, "assets", now)[1],
        equity=pit_fact_asof(pit.balance, "stockholdersEquity", now)[0],
        total_assets=pit_fact_asof(pit.balance, "assets", now)[0],
        total_debt=pit_fact_asof(pit.balance, "totalDebt", now)[0],
        curr_assets=pit_fact_asof(pit.balance, "currentAssets", now)[0],
        curr_liab=pit_fact_asof(pit.balance, "currentLiabilities", now)[0],
        shares=_first(
            pit_fact_asof(pit.balance, "sharesOutstanding", now)[0],
            pit_fact_asof(pit.balance, "currentSharesOutstanding", now)[0],
            pit_fact_asof(pit.balance, "sharesIssued", now)[0],
        ),
        fcf=pit_fact_asof(pit.cash_flow, "freeCashFlow", now)[0],
    )

    # 2b) TTM overlay — current fundamentals switch to trailing-twelve-month
    # figures (last 4 reported quarters for flows, latest quarter for
    # balances), matching yfinance's trailing semantics.  Best-effort: an
    # unavailable TTM block keeps the as-filed fiscal-year values above.
    if ttm_facts is None:
        ttm_facts = _fetch_ttm_facts(ticker, pit)
        info_dirty = True
    if ttm_facts:
        _apply_ttm_overlay(fund, ttm_facts)

    # 3) Prices — 2y of daily adjusted closes (trend + latest price).  The
    # 15-minute history cache is shared with the yfinance path on purpose:
    # both serve the same split/dividend-adjusted closes (verified to 8
    # decimals), and every consumer here reads only the `Close` column.
    history_str = cache_history_get(ticker)
    history = None
    if history_str is not None:
        try:
            history = pd.read_json(StringIO(history_str))
        except Exception:
            history = None
    if history is None or history.empty or "Close" not in history:
        hist_raw = _get("price/history", {
            "symbol": ticker,
            "resolution": "1d",
            "from": (now - timedelta(days=730)).strftime("%Y-%m-%d"),
        })
        points = hist_raw.get("data") if isinstance(hist_raw, dict) else None
        if not points:
            raise StockfitError(f"StockFit: no price history for {ticker}")
        history = pd.DataFrame(
            {"Close": [float(v) for _, v in points]},
            index=pd.DatetimeIndex([datetime.fromtimestamp(ts / 1000) for ts, _ in points]),
        )
        cache_history_set(ticker, history.to_json())
    price = float(history["Close"].iloc[-1])

    # 4) Dividends — latest fiscal-year DPS ≈ TTM (payout is derived from it,
    # same convention as the yfinance path).  Some filers return empty shells
    # (all-None rows, e.g. JNJ) — scan a few rows and degrade to None cleanly.
    if cached_info is None:
        div_rows = _get("earnings/dividend-history", {"symbol": ticker, "limit": "5"})
        for row in div_rows if isinstance(div_rows, list) else []:
            if isinstance(row, dict):
                candidate = _safe_float(row.get("dividendPerShare"))
                if candidate is not None:
                    ttm_div = candidate
                    break
    if info_dirty:
        try:
            # An empty TTM block is stored as null so a transient failure is
            # retried on the next run instead of sticking for a full day.
            cache_stockfit_info_set(ticker, json.dumps({
                "profile": profile, "dividend_per_share": ttm_div,
                "ttm": ttm_facts or None,
            }))
        except (TypeError, ValueError):
            logger.debug("Could not cache StockFit info for %s", ticker)

    sector_raw = profile.get("sector")
    sector = sector_raw if isinstance(sector_raw, str) else None
    data = _derive_stock_data(
        ticker,
        {
            "shortName": profile.get("name"),
            "longName": profile.get("name"),
            "sector": _GICS_TO_YFINANCE_SECTOR.get(sector, sector) if sector else None,
            "currency": "USD",
            "exchange": (profile.get("exchanges") or [None])[0],
        },
        price,
        (None, None, None, None, None, None),
        None,
        None,
        fund,
        ttm_div,
        None,  # eps_surprise — no analyst estimates in StockFit
    )
    _apply_history_common(data, history)

    if min_market_cap and data.market_cap and data.market_cap < min_market_cap:
        data.errors.append("market cap below threshold")
    return data


def _safe_float(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) else None


def _load_stockfit_info(
    ticker: str,
) -> Optional[tuple[dict[str, Any], Optional[float], Optional[dict[str, Any]]]]:
    """Cached ``(profile, dividend_per_share, ttm_facts)`` triple, or ``None``."""
    raw = cache_stockfit_info_get(ticker)
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    profile = payload.get("profile")
    ttm = payload.get("ttm")
    return (
        (profile if isinstance(profile, dict) else {}),
        _safe_float(payload.get("dividend_per_share")),
        (ttm if isinstance(ttm, dict) else None),
    )
