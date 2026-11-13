"""Point-in-time annual statements from SEC EDGAR's XBRL API.

Why this exists: StockFit's plan caps the annual series at five periods (Free 1,
Starter 5, Pro 20+), so ``backtest`` cannot build a snapshot older than ~2021 —
every fundamental resolves to ``None``, every factor lands on the neutral 50 and
no score clears the 60 gate. EDGAR's ``companyfacts`` endpoint is free, tags
every US-GAAP fact with its own ``filed`` date, and reaches back to ~2009, which
is what makes an out-of-regime test (2018-2022) possible at all.

Scope, deliberately narrow:

* **US filers only**, annual periods only — the engine's PIT path reads annual.
* Only the facts ``backtest._build_pit_stock_data`` reads, plus their priors.
  This is not a statement normalizer; it is a resolver for eleven figures.
* ``totalDebt`` and ``freeCashFlow`` are **derived**, as they are in StockFit,
  and their wording is a choice rather than a fact (see the notes below).

The concept mapping is not guesswork: ``scripts/edgar_pit_check.py`` in the
investnowtip repo compares these series against five years of StockFit over a
sector sample, joining on the filing date. It found that nine of the eleven
facts reproduce exactly and named the exceptions this module encodes:

* ``eps`` is **basic**, not diluted (AAPL FY2026: 8.98 diluted, 9.01 basic, and
  StockFit reports 9.01), so ``EarningsPerShareBasic`` goes first.
* ``revenue`` prefers total ``Revenues`` — a retailer like WMT files both and
  StockFit reports the total (713.2 B, not the 706.4 B contract figure).
* ``totalDebt`` never matches StockFit (AAPL FY2026: 94.5 B vs no combination of
  US-GAAP concepts) because it is a vendor definition. This module uses
  **non-lease debt** — ``LongTermDebt`` plus commercial paper — and the engine
  only feeds it to a debt/equity ratio, so what matters is that it is
  consistent, not that it is identical.
* REITs often file no capex concept, so ``freeCashFlow`` stays ``None`` for them
  rather than being faked as operating cash flow.

Cache: parsed facts only (~15 KB/ticker), never the multi-megabyte payload.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .data_source_stockfit import PitPeriod, PitStatements

logger = logging.getLogger(__name__)

BASE = "https://data.sec.gov/api/xbrl"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

#: EDGAR answers **403** to a User-Agent without a contact address, so this has
#: to be email-shaped. Set ``EDGAR_USER_AGENT`` to put your own in front of SEC.
USER_AGENT = os.environ.get(
    "EDGAR_USER_AGENT", "investdaytip edgar-pit (contact@example.com)"
)
CACHE_DIR_ENV = "EDGAR_PIT_DIR"
SNAPSHOT_MAX_AGE_DAYS = 7.0
CIK_MAX_AGE_DAYS = 30.0
#: EDGAR allows 10 requests/second; stay under it across threads.
_MIN_INTERVAL = 0.12
_last_call = 0.0
_rate_lock = threading.Lock()

#: Concept resolution order per fact. The first concept that carries a period
#: wins, which is how the 2018 revenue re-tagging is handled without a special
#: case. Order is evidence, not taste — see ``scripts/edgar_pit_check.py``.
CONCEPTS: dict[str, tuple[str, ...]] = {
    "netIncome": ("NetIncomeLoss", "ProfitLoss"),
    "revenue": (
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
    ),
    "eps": ("EarningsPerShareBasic", "EarningsPerShareDiluted"),
    "grossProfit": ("GrossProfit",),
    "operatingIncome": ("OperatingIncomeLoss",),
    "stockholdersEquity": (
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ),
    "assets": ("Assets",),
    "currentAssets": ("AssetsCurrent",),
    "currentLiabilities": ("LiabilitiesCurrent",),
    "sharesOutstanding": ("CommonStockSharesOutstanding", "EntityCommonStockSharesOutstanding"),
    "operatingCashFlow": ("NetCashProvidedByUsedInOperatingActivities",),
    "costOfRevenue": ("CostOfRevenue", "CostOfGoodsAndServicesSold"),
    "capitalExpenditure": (
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
        "PaymentsToAcquireOtherPropertyPlantAndEquipment",
    ),
}

#: Non-lease debt. StockFit reports a figure for filers that tag no single
#: total, so try a total first, then the components, and add commercial paper:
#: ``LongTermDebt`` already contains the current portion, which is why summing
#: every debt concept double-counts (156 B against StockFit's 94 B for AAPL).
DEBT_TOTAL = ("LongTermDebt", "DebtInstrumentCarryingAmount")
DEBT_PARTS = ("LongTermDebtNoncurrent", "LongTermDebtCurrent", "DebtCurrent", "LongTermDebtCurrentMaturities")
DEBT_ADD = ("CommercialPaper",)


def cache_dir() -> Path:
    """Where parsed PIT facts live (``EDGAR_PIT_DIR`` overrides)."""
    override = os.environ.get(CACHE_DIR_ENV)
    if override:
        return Path(override)
    return Path.home() / ".investdaytip" / "edgar"


def _get_json(url: str, *, timeout: float = 90.0) -> Any:
    global _last_call
    # Serialised across the fetch pool: six threads sharing an unlocked clock
    # would burst past EDGAR's 10 requests/second.
    with _rate_lock:
        wait = _MIN_INTERVAL - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def _read_json(path: Path) -> Optional[Any]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _age_days(path: Path) -> float:
    try:
        return (time.time() - path.stat().st_mtime) / 86400.0
    except OSError:
        return float("inf")


def cik_for(ticker: str) -> Optional[int]:
    """CIK for *ticker*, from a month-long cache of EDGAR's symbol map."""
    ticker = ticker.upper().replace(".", "-")
    path = cache_dir() / "_cik.json"
    mapping = _read_json(path) if _age_days(path) <= CIK_MAX_AGE_DAYS else None
    if not isinstance(mapping, dict):
        try:
            payload = _get_json(TICKERS_URL, timeout=60.0)
            mapping = {
                str(row["ticker"]).upper(): int(row["cik_str"]) for row in payload.values()
            }
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(mapping))
        except (OSError, urllib.error.URLError, ValueError) as exc:
            logger.debug("EDGAR ticker map unavailable: %s", exc)
            return None
    cik = mapping.get(ticker)
    return int(cik) if cik else None


def _annual_by_concept(facts: dict[str, Any]) -> dict[str, dict[tuple[str, str], float]]:
    """``{concept: {(end, filed): value}}`` for annual 10-K facts.

    ``us-gaap`` only: the ``dei`` namespace carries cover-page dates (a share
    count "as of" the filing date), which are not fiscal period ends and created
    phantom periods with no financials at all.
    """
    out: dict[str, dict[tuple[str, str], float]] = {}
    for concept, node in facts.get("us-gaap", {}).items():
        for unit_rows in node.get("units", {}).values():
            for row in unit_rows:
                if row.get("form") != "10-K" or row.get("fp") != "FY":
                    continue
                end, filed, value = row.get("end"), row.get("filed"), row.get("val")
                if not end or not filed or value is None:
                    continue
                out.setdefault(concept, {})[(end, filed)] = float(value)
    return out


def _days(iso_date: str) -> int:
    """Ordinal day number for a ``YYYY-MM-DD`` string, for the annual filter."""
    try:
        return datetime.strptime(iso_date, "%Y-%m-%d").toordinal()
    except ValueError:
        return 0


def periods_from_companyfacts(ticker: str, cik: int, payload: dict[str, Any]) -> list[PitPeriod]:
    """Build newest-first :class:`PitPeriod` objects from one companyfacts payload.

    One period per fiscal year, dated by **its own 10-K**: a figure reappears in
    every later 10-K as a comparative, so keying on ``(end, filed)`` naively
    produced 156 periods for AAPL instead of ~15. Restatements are therefore not
    modelled — the engine wants what was knowable from each annual report, which
    is what StockFit serves and what the eleven-fact check validated.
    """
    concepts = _annual_by_concept(payload.get("facts", {}))
    resolved: dict[tuple[str, str], dict[str, float]] = {}
    for fact, candidates in CONCEPTS.items():
        for concept in candidates:
            for key, value in concepts.get(concept, {}).items():
                resolved.setdefault(key, {}).setdefault(fact, value)

    # Anchor on the income statement: only there does a fiscal year exist.
    anchor = [k for k, facts in resolved.items() if {"netIncome", "revenue", "eps"} & facts.keys()]
    first_filing: dict[str, str] = {}
    for end, filed in anchor:
        if end not in first_filing or filed < first_filing[end]:
            first_filing[end] = filed

    # Quarter-length periods leak in (a 10-K instance tags some concepts on a
    # quarter end), so keep only periods a year apart. Greedy from newest to
    # oldest so any fiscal calendar works, including a September year end.
    kept: list[str] = []
    for end in sorted(first_filing, reverse=True):
        if not kept or (_days(kept[-1]) - _days(end)) >= 300:
            kept.append(end)

    periods: list[PitPeriod] = []
    for end in kept:
        filed = first_filing[end]
        facts = dict(resolved[(end, filed)])
        # StockFit fills gross profit even when a filer tags no GrossProfit, and
        # the engine reads it, so derive it the same way when it is absent.
        if "grossProfit" not in facts:
            revenue, cost = facts.get("revenue"), facts.get("costOfRevenue")
            if revenue is not None and cost is not None:
                facts["grossProfit"] = revenue - cost
        total = next(
            (concepts[c][(end, filed)] for c in DEBT_TOTAL if (end, filed) in concepts.get(c, {})),
            None,
        )
        if total is None:
            parts = [
                concepts[c][(end, filed)] for c in DEBT_PARTS if (end, filed) in concepts.get(c, {})
            ]
            total = sum(parts) if parts else None
        if total is not None:
            extra = [
                concepts[c][(end, filed)] for c in DEBT_ADD if (end, filed) in concepts.get(c, {})
            ]
            facts["totalDebt"] = float(total + sum(extra))
        ocf = facts.get("operatingCashFlow")
        capex = facts.get("capitalExpenditure")
        if ocf is not None and capex is not None:
            facts["freeCashFlow"] = ocf - capex
        try:
            period_end = datetime.strptime(end, "%Y-%m-%d")
            date_filed = datetime.strptime(filed, "%Y-%m-%d")
        except ValueError:
            continue
        periods.append(
            PitPeriod(
                period_end=period_end,
                fiscal_year=period_end.year,
                fiscal_period="FY",
                date_filed=date_filed,
                facts=facts,
            )
        )
    periods.sort(key=lambda p: p.period_end, reverse=True)
    return periods


def _payload_to_cache(ticker: str, cik: int, periods: list[PitPeriod]) -> dict[str, Any]:
    return {
        "ticker": ticker.upper(),
        "cik": cik,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "periods": [
            {
                "period_end": p.period_end.strftime("%Y-%m-%d"),
                "date_filed": p.date_filed.strftime("%Y-%m-%d"),
                "facts": p.facts,
            }
            for p in periods
        ],
    }


def _from_cache(ticker: str, payload: dict[str, Any]) -> PitStatements:
    periods = [
        PitPeriod(
            period_end=datetime.strptime(row["period_end"], "%Y-%m-%d"),
            fiscal_year=int(row["period_end"][:4]),
            fiscal_period="FY",
            date_filed=datetime.strptime(row["date_filed"], "%Y-%m-%d"),
            facts=row.get("facts") or {},
        )
        for row in payload.get("periods", [])
    ]
    periods.sort(key=lambda p: p.period_end, reverse=True)
    cik = payload.get("cik")
    return PitStatements(
        ticker=ticker.upper(),
        cik=int(cik) if cik else None,
        # Every series carries the same periods: the engine asks each statement
        # for its own keys, and a figure absent from one is simply None.
        income=list(periods),
        balance=list(periods),
        cash_flow=list(periods),
    )


def fetch_pit_statements(ticker: str, *, refresh: bool = False) -> PitStatements:
    """EDGAR equivalent of ``data_source_stockfit.fetch_pit_statements``.

    Reads the parsed cache when it is younger than a week (filings arrive
    annually, so a week is generous), otherwise fetches ``companyfacts``.
    Returns an empty :class:`PitStatements` when the ticker is not a US filer,
    which is the caller's signal to fall back to the classic path.
    """
    ticker = ticker.upper().replace(".", "-")
    path = cache_dir() / f"{ticker}.json"
    cached = _read_json(path)
    if not refresh and isinstance(cached, dict) and _age_days(path) <= SNAPSHOT_MAX_AGE_DAYS:
        return _from_cache(ticker, cached)

    cik = cik_for(ticker)
    if cik is None:
        logger.warning("No EDGAR CIK for %s", ticker)
        return PitStatements(ticker=ticker)
    try:
        payload = _get_json(f"{BASE}/companyfacts/CIK{cik:010d}.json")
    except (urllib.error.URLError, ValueError) as exc:
        logger.warning("EDGAR companyfacts failed for %s: %s", ticker, exc)
        if isinstance(cached, dict):
            return _from_cache(ticker, cached)
        return PitStatements(ticker=ticker)

    periods = periods_from_companyfacts(ticker, cik, payload)
    if not periods:
        logger.warning("EDGAR has no annual periods for %s", ticker)
        return PitStatements(ticker=ticker, cik=cik)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_payload_to_cache(ticker, cik, periods)))
    except OSError as exc:
        logger.debug("Could not cache EDGAR PIT for %s: %s", ticker, exc)
    return _from_cache(ticker, _payload_to_cache(ticker, cik, periods))
