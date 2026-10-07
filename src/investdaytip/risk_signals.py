"""Keyless risk signals — the local "devil's advocate" layer (product Fase 3).

Turns the data InvestDayTip already has into explicit bear-case bullets:
Altman zone, failed Piotroski checks, leverage, payout ratio, losses and
negative free cash flow.  Pure functions only — no I/O (same posture as
``scoring.py`` / ``financial_health.py``).

On top of the keyless layer, :func:`footnote_risk_signals` turns StockFit
``footnotes/*`` payloads (Pro tier) into additional bullets — the footnotes
based bear case.  Parsing is **defensive**: a bullet is emitted only when a
documented field is present and numeric, so an unknown response shape yields
silence, never a fabricated signal.

These signals are **context, never a score**: they inform the decision, they
do not change rankings.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterator, Mapping, Optional

from investdaytip.data_source import StockData
from investdaytip.financial_health import AltmanResult, PiotroskiResult

SEVERITY_ORDER = {"high": 0, "medium": 1, "info": 2}

# Heuristic thresholds (documented so they can be debated/tuned).
_DEBT_TO_EQUITY_ALERT = 200.0  # yfinance-style percent (200 = 2.0x)
_PAYOUT_ALERT = 1.0            # dividend > earnings
_PAYOUT_WATCH = 0.85

# Footnote-layer thresholds (shares are fractions of 1; materiality vs market cap).
_CUSTOMER_CONCENTRATION_ALERT = 0.30   # key customer at ≥30% → medium
_CUSTOMER_CONCENTRATION_WATCH = 0.15   # key customer at ≥15% → info
_SUPPLIER_CONCENTRATION_WATCH = 0.30
_MATURITY_WALL_RATIO = 0.25            # ≥25% of face amount due within 2 years
_MATURITY_WALL_YEARS = 2
_FACILITY_UTIL_ALERT = 0.75            # peak drawn ≥75% of capacity → medium
_FACILITY_UTIL_WATCH = 0.50
_EQUITY_COMP_ALERT = 0.15              # unrecognized SBC ≥15% of market cap
_EQUITY_COMP_WATCH = 0.05
_PENSION_ALERT = 0.05                  # underfunding ≥5% of market cap → medium
_SUPPLIER_FINANCE_WATCH = 0.02         # obligations ≥2% of market cap
_LEVEL3_WATCH = 0.30                   # Level 3 share ≥30% of fair-value book
_COUNTRY_CONCENTRATION_WATCH = 0.60
_PRODUCT_CONCENTRATION_WATCH = 0.40
_SEGMENT_CONCENTRATION_WATCH = 0.60
# A geography block only speaks for the whole company when it reconciles with
# the filer's own revenue.  Measured across the site's top 100 (2026-10-07):
# complete maps sit at 0.9999–1.0003 of it, partial ones at 0.39–0.54 — so
# this band separates the two with room for a fiscal year of drift.  Outside
# it a country's share would be invented either way: NFLX, CSCO, EQIX and EMR
# tag only a slice of the world in ``countries`` (the old rule printed "100%
# of revenue from United States"), and a filer whose ``Non-US`` region
# overlaps its other regions over-counts (JNJ: 1.43× its revenue).
_GEO_RECONCILIATION_BAND = (0.90, 1.10)


@dataclass
class RiskSignal:
    """One bear-case bullet: ``severity`` is high | medium | info."""

    severity: str
    label: str
    detail: str
    source: str = "local"  # "local" (keyless) | "stockfit" (footnotes layer)


def risk_signals(
    data: StockData,
    piotroski: Optional[PiotroskiResult] = None,
    altman: Optional[AltmanResult] = None,
) -> list[RiskSignal]:
    """Collect risk signals, most severe first.

    Rules (all from local data — no API key involved):

    - Altman Z zone: ``distress`` → high, ``grey`` → medium
    - losses (``profit_margin < 0``) → medium; negative free cash flow → medium
    - ``debt_to_equity > 200`` (percent form) → medium
    - ``payout_ratio > 1.0`` → medium, ``> 0.85`` → info
    - failed Piotroski checks: accruals → medium, dilution / rising leverage →
      info, *both* margin and ROA deteriorating → medium
    """
    out: list[RiskSignal] = []

    if altman is not None:
        if altman.zone == "distress":
            out.append(RiskSignal(
                "high", "Altman Z in distress zone",
                f"Z-Score {altman.z_score:.2f} (< 1.81) — financial distress "
                "risk per the Altman model",
            ))
        elif altman.zone == "grey":
            out.append(RiskSignal(
                "medium", "Altman Z in grey zone",
                f"Z-Score {altman.z_score:.2f} (1.81–2.99) — not clearly safe",
            ))

    if data.profit_margin is not None and data.profit_margin < 0:
        out.append(RiskSignal(
            "medium", "Loss-making",
            f"net margin {data.profit_margin * 100:.1f}%",
        ))
    if data.free_cashflow is not None and data.free_cashflow < 0:
        out.append(RiskSignal(
            "medium", "Negative free cash flow",
            f"FCF ${data.free_cashflow / 1e9:.1f}B — burning cash",
        ))

    if data.debt_to_equity is not None and data.debt_to_equity > _DEBT_TO_EQUITY_ALERT:
        out.append(RiskSignal(
            "medium", "High leverage",
            f"debt/equity {data.debt_to_equity:.0f}% (alert threshold "
            f"{_DEBT_TO_EQUITY_ALERT:.0f}%)",
        ))

    if data.payout_ratio is not None:
        if data.payout_ratio > _PAYOUT_ALERT:
            out.append(RiskSignal(
                "medium", "Dividend not covered by earnings",
                f"payout ratio {data.payout_ratio * 100:.0f}%",
            ))
        elif data.payout_ratio > _PAYOUT_WATCH:
            out.append(RiskSignal(
                "info", "High payout ratio",
                f"payout ratio {data.payout_ratio * 100:.0f}%",
            ))

    if piotroski is not None:
        checks = piotroski.checks
        if checks.get("accruals") is False:
            out.append(RiskSignal(
                "medium", "Earnings quality (accruals)",
                "operating cash flow below reported net income — profits not "
                "backed by cash",
            ))
        if checks.get("margin_improving") is False and checks.get("roa_improving") is False:
            out.append(RiskSignal(
                "medium", "Deteriorating fundamentals",
                "gross margin and ROA both lower than the previous fiscal year",
            ))
        if checks.get("no_dilution") is False:
            out.append(RiskSignal(
                "info", "Share dilution",
                "shares outstanding higher than the previous fiscal year",
            ))
        if checks.get("leverage_decreasing") is False:
            out.append(RiskSignal(
                "info", "Rising leverage",
                "debt/assets higher than the previous fiscal year",
            ))

    out.sort(key=lambda s: SEVERITY_ORDER.get(s.severity, 9))
    return out


# ── Footnote layer (StockFit footnotes/*) ────────────────────────────────────
# Parsing is deliberately defensive: the Pro-tier response shapes come from the
# API documentation (only the segmentation pair was verified live, 2026-10-06),
# so every reader looks up documented field names and gives up silently when
# they are absent.  Unknown shape → no bullet, never a fabricated signal.


def _num(value: Any) -> Optional[float]:
    """Finite float from a JSON value, else ``None``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


def _share(value: Optional[float]) -> Optional[float]:
    """Normalize a possibly mis-tagged share: ``1.5 < v <= 100`` → percent form."""
    if value is None:
        return None
    if 1.5 < value <= 100.0:
        return value / 100.0
    return value


def _walk_dicts(node: Any) -> Iterator[dict[str, Any]]:
    """Yield every dict in a nested JSON structure."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_dicts(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_dicts(item)


def _text(node: Mapping[str, Any], *keys: str) -> Optional[str]:
    for key in keys:
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _first_num(node: Mapping[str, Any], *keys: str) -> Optional[float]:
    for key in keys:
        value = _num(node.get(key))
        if value is not None:
            return value
    return None


def _list_of_dicts(node: Mapping[str, Any], *keys: str) -> list[dict[str, Any]]:
    """First list-of-dicts stored under any of *keys* (else empty)."""
    for key in keys:
        value = node.get(key)
        if isinstance(value, list):
            items = [i for i in value if isinstance(i, dict)]
            if items:
                return items
    return []


def _latest_block(payload: Any) -> Optional[dict[str, Any]]:
    """Most recent period block of a period-major payload.

    Prefers the block with the highest ``fiscalYear``; falls back to the first
    element (the API serves most-recent-first on its statements endpoints).
    """
    if isinstance(payload, dict):
        for key in ("data", "periods"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
        else:
            return payload
    if not isinstance(payload, list):
        return None
    blocks = [b for b in payload if isinstance(b, dict)]
    if not blocks:
        return None
    dated = [b for b in blocks if _num(b.get("fiscalYear")) is not None]
    if dated:
        return max(dated, key=lambda b: _num(b.get("fiscalYear")) or 0.0)
    return blocks[0]


def _usd(value: float) -> str:
    if abs(value) >= 1e9:
        return f"${value / 1e9:.1f}B"
    if abs(value) >= 1e6:
        return f"${value / 1e6:.0f}M"
    return f"${value:,.0f}"


def _trend_suffix(earliest: Optional[float], latest: float) -> str:
    if earliest is None or abs(latest - earliest) < 0.02:
        return ""
    return f" ({earliest:.0%} → {latest:.0%} over the disclosed history)"


def _concentration_rows(
    payload: Any,
) -> list[tuple[str, str, float, Optional[float]]]:
    """``(risk_type, counterparty, latest share, earliest share)`` series.

    The endpoint is series-major: disclosures are grouped by
    ``(riskType, benchmark)`` and each counterparty carries its own history of
    ``share`` points (fractions of 1).
    """
    if isinstance(payload, list):
        groups: list[Any] = payload
    elif isinstance(payload, dict):
        groups = _list_of_dicts(payload, "series", "categories", "groups", "data") or [payload]
    else:
        return []

    rows: list[tuple[str, str, float, Optional[float]]] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        risk = (_text(group, "riskType", "risk_type", "type", "category", "risk") or "").lower()
        parties = _list_of_dicts(group, "counterparties", "parties", "items", "members", "series", "data")
        if not parties and ("history" in group or "share" in group):
            parties = [group]
        for party in parties:
            points: list[tuple[str, float]] = []
            for entry in _list_of_dicts(party, "history", "points", "values", "data") or [party]:
                share = _share(_first_num(entry, "share", "shareOfTotal", "value", "percent"))
                if share is None:
                    continue
                points.append((str(entry.get("period") or ""), share))
            if not points:
                continue
            dated = sorted((p for p in points if p[0]), key=lambda p: p[0])
            ordered = dated or points
            _, latest = ordered[-1]
            earliest = ordered[0][1] if len(ordered) > 1 else None
            name = (_text(party, "name", "label", "counterparty", "party")
                    or _text(party, "member") or "a key counterparty")
            rows.append((risk, name, latest, earliest))
    return rows


def _concentration_signals(payload: Any) -> list[RiskSignal]:
    rows = _concentration_rows(payload)
    out: list[RiskSignal] = []

    customers = [r for r in rows if "customer" in r[0] or "credit" in r[0]]
    if customers:
        risk, name, latest, earliest = max(customers, key=lambda r: r[2])
        trend = _trend_suffix(earliest, latest)
        if latest >= _CUSTOMER_CONCENTRATION_ALERT:
            out.append(RiskSignal(
                "medium", "Key-customer concentration",
                f"{name} at {latest:.0%} of "
                f"{'receivables' if 'credit' in risk else 'revenue'}{trend}",
                "stockfit",
            ))
        elif latest >= _CUSTOMER_CONCENTRATION_WATCH:
            out.append(RiskSignal(
                "info", "Key-customer concentration",
                f"{name} at {latest:.0%} of "
                f"{'receivables' if 'credit' in risk else 'revenue'}{trend}",
                "stockfit",
            ))

    suppliers = [r for r in rows if "supplier" in r[0]]
    if suppliers:
        _, name, latest, earliest = max(suppliers, key=lambda r: r[2])
        if latest >= _SUPPLIER_CONCENTRATION_WATCH:
            out.append(RiskSignal(
                "info", "Supplier concentration",
                f"{name} at {latest:.0%} of purchases"
                f"{_trend_suffix(earliest, latest)}",
                "stockfit",
            ))
    return out


def _maturity_wall_signals(payload: Any) -> list[RiskSignal]:
    block = _latest_block(payload)
    if block is None:
        return []
    instruments = _list_of_dicts(block, "instruments", "tranches", "data")
    if not instruments:
        instruments = [d for d in _walk_dicts(payload) if _num(d.get("dueYear")) is not None]
    amounts = [
        (due, amount)
        for inst in instruments
        if (due := _num(inst.get("dueYear"))) is not None
        and (amount := _first_num(inst, "faceAmount", "face", "carryingAmount",
                                  "amount", "principal")) is not None
        and amount > 0
    ]
    total = sum(a for _, a in amounts)
    if total <= 0:
        return []
    horizon = date.today().year + _MATURITY_WALL_YEARS
    near = sum(a for due, a in amounts if due <= horizon)
    ratio = near / total
    if near <= 0 or ratio < _MATURITY_WALL_RATIO:
        return []
    return [RiskSignal(
        "medium", "Debt maturity wall",
        f"{_usd(near)} of {_usd(total)} face amount ({ratio:.0%}) matures "
        f"by {horizon}",
        "stockfit",
    )]


def _facility_signals(payload: Any) -> list[RiskSignal]:
    utils = [
        u for d in _walk_dicts(payload)
        if (u := _share(_first_num(d, "utilization"))) is not None and u > 0
    ]
    if not utils:
        return []
    peak = max(utils)
    if peak >= _FACILITY_UTIL_ALERT:
        return [RiskSignal(
            "medium", "Credit facilities heavily drawn",
            f"peak utilization {peak:.0%} of committed capacity across the "
            "disclosed facilities",
            "stockfit",
        )]
    if peak >= _FACILITY_UTIL_WATCH:
        return [RiskSignal(
            "info", "High credit-line utilization",
            f"peak utilization {peak:.0%} of committed capacity",
            "stockfit",
        )]
    return []


def _stock_comp_signals(payload: Any, market_cap: Optional[float]) -> list[RiskSignal]:
    costs = [
        n for d in _walk_dicts(_latest_block(payload))
        for key, value in d.items()
        if "unrecognized" in key.lower() and (n := _num(value)) is not None and n > 0
    ]
    if not costs:
        return []
    cost = max(costs)
    ratio = (cost / market_cap) if market_cap else None
    if ratio is not None and ratio < _EQUITY_COMP_WATCH:
        return []
    severity = "medium" if (ratio or 0.0) >= _EQUITY_COMP_ALERT else "info"
    detail = f"{_usd(cost)} of unrecognized equity compensation cost"
    if ratio is not None:
        detail += f" ({ratio:.0%} of market cap) — future expense and dilution"
    else:
        detail += " — future expense and dilution"
    return [RiskSignal(severity, "Unvested equity compensation", detail, "stockfit")]


def _pension_signals(payload: Any, market_cap: Optional[float]) -> list[RiskSignal]:
    statuses = [
        n for d in _walk_dicts(_latest_block(payload))
        if (n := _num(d.get("fundedStatus"))) is not None
    ]
    under = -sum(min(0.0, s) for s in statuses)
    if under <= 0:
        return []
    ratio = (under / market_cap) if market_cap else None
    severity = "medium" if (ratio or 0.0) >= _PENSION_ALERT else "info"
    detail = f"{_usd(under)} net underfunded across the disclosed plans"
    if ratio is not None:
        detail += f" ({ratio:.0%} of market cap)"
    return [RiskSignal(severity, "Underfunded pension plans", detail, "stockfit")]


def _supplier_finance_signals(payload: Any, market_cap: Optional[float]) -> list[RiskSignal]:
    block = _latest_block(payload)
    candidates: list[float] = []
    for node in _walk_dicts(block):
        for key, value in node.items():
            lowered = key.lower()
            if not ("outstanding" in lowered or "obligation" in lowered):
                continue
            if any(skip in lowered for skip in ("current", "settled", "added", "paid")):
                continue
            amount = _num(value)
            if amount is not None and amount > 0:
                candidates.append(amount)
    if not candidates:
        return []
    # max, never sum: `totals` and `programs` overlap per the API docs.
    amount = max(candidates)
    ratio = (amount / market_cap) if market_cap else None
    if ratio is not None and ratio < _SUPPLIER_FINANCE_WATCH:
        return []
    severity = "medium" if (ratio or 0.0) >= _EQUITY_COMP_ALERT else "info"
    detail = (f"{_usd(amount)} outstanding under supplier-finance programs "
              "(reverse factoring — leverage parked in accounts payable)")
    if ratio is not None:
        detail += f" — {ratio:.0%} of market cap"
    return [RiskSignal(severity, "Supplier-finance obligations", detail, "stockfit")]


def _level3_signals(payload: Any) -> list[RiskSignal]:
    shares = [
        s for d in _walk_dicts(_latest_block(payload))
        if (s := _share(_num(d.get("level3Share")))) is not None and s > 0
    ]
    if not shares:
        return []
    level3 = max(shares)
    if level3 < _LEVEL3_WATCH:
        return []
    return [RiskSignal(
        "info", "Opaque Level 3 valuations",
        f"{level3:.0%} of recurring fair-value measurements rely on "
        "unobservable (model) inputs",
        "stockfit",
    )]


def _region_remainders(
    regions: list[dict[str, Any]], countries: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Regions reduced to the revenue their country leaves do not explain.

    StockFit documents every region with ``explainedByCountries`` (sum of the
    country leaves on that region's continent) and ``other = value −
    explainedByCountries`` precisely so a region and its country leaves
    reconcile without double counting.  Counting that remainder — and nothing
    more — is what makes a filer that tags "United States" *next to* a regional
    bucket read as its real mix: MSFT (US 144.5 + Non-US 137.2), KO, and GOOGL
    (US + EMEA + APAC + Other Americas).  A region fully covered by country
    leaves (Apple's ``srt:AmericasMember`` around ``country:US``) yields 0 and
    is dropped, so the rollup never counts twice.  Reconciliation fields are
    optional: without them the remainder falls back to continent matching, and
    a region with no matching country leaf (a "Non-US" bucket, ``continent``
    null) counts in full.
    """
    by_continent: dict[str, float] = {}
    for country in countries:
        continent = _text(country, "continent")
        value = _first_num(country, "value", "revenue", "amount")
        if continent and value is not None and value > 0:
            by_continent[continent] = by_continent.get(continent, 0.0) + value

    remainders: list[dict[str, Any]] = []
    for region in regions:
        value = _first_num(region, "value", "revenue", "amount")
        if value is None or value <= 0:
            continue
        remainder = _first_num(region, "other")
        if remainder is None:
            explained = _first_num(region, "explainedByCountries")
            if explained is None:
                continent = _text(region, "continent")
                explained = by_continent.get(continent, 0.0) if continent else 0.0
            remainder = value - explained
        if remainder <= 0:
            continue  # pure rollup of country leaves — adding it would double count
        leaf = dict(region)
        leaf["value"] = remainder
        remainders.append(leaf)
    return remainders


def _geo_total(geo: Mapping[str, Any]) -> tuple[float, list[dict[str, Any]]]:
    """Leaves with values and their total.

    StockFit partitions geography into ``countries``, ``usStates``, ``regions``
    and ``residuals``, and **no bucket may be read alone**: a filer tags the
    United States as a country while the rest of the world lands in
    ``regions``, so the old countries-wins rule left the total holding the US
    line alone — GOOGL read "100% of revenue from United States" against **48%
    in its FY2025 10-K**, and MSFT and KO (both document a US + Non-US split)
    read the same way; 19 top-100 tickers carried that 100% bullet in the
    site's 2026-10-06 run.  The leaves are the country and residual buckets
    plus each region's remainder (:func:`_region_remainders`).  ``usStates``
    are US-internal splits of the US leaf, never added (they would double
    count).
    """
    countries = _list_of_dicts(geo, "countries")
    regions = _region_remainders(_list_of_dicts(geo, "regions"), countries)
    residuals = _list_of_dicts(geo, "residuals")
    leaves = countries + regions + residuals
    total = sum(v for entry in leaves if (v := _first_num(entry, "value", "revenue", "amount")) and v > 0)
    return total, leaves


def _geo_reconciles(total: float, revenue: Optional[float]) -> bool:
    """True when a geography block accounts for the filer's own revenue.

    The reference is the latest annual top line from the ticker's statements
    (``annual_facts``).  No reference — statements missing, a source without
    them — means nothing to reconcile against, so the answer is False: silence
    is the safe side of a share the map cannot support.
    """
    if revenue is None or revenue <= 0 or total <= 0:
        return False
    low, high = _GEO_RECONCILIATION_BAND
    return low <= total / revenue <= high


def _revenue_segmentation_signals(
    payload: Any, revenue: Optional[float] = None
) -> list[RiskSignal]:
    """Concentration bullets from the ``revenue-segmentation`` footnote.

    Both bullets say "of revenue", so both are shares of ``revenue`` — the
    filer's own annual top line, which the geographic total only stands in for
    when the statements are unavailable.  Two guards keep those shares honest,
    both learned from the site's 2026-10-07 run:

    * the geography bullet needs the map to reconcile with that revenue
      (:func:`_geo_reconciles`) — a partial map (NFLX discloses 41% of its
      revenue in ``countries``, all of it US) or an over-counted one cannot
      carry a country's share of the company;
    * a product line is only comparable when it fits inside the denominator —
      BMY's ``Sales Revenue Gross`` footnote line is 1.83× its revenue — so
      entries above it are dropped instead of printed as ">100% of revenue".
    """
    block = _latest_block(payload)
    if not isinstance(block, dict):
        return []
    geo = block.get("geography")
    out: list[RiskSignal] = []
    total = 0.0
    if isinstance(geo, dict):
        total, leaves = _geo_total(geo)
        entries = [(name, v) for entry in leaves
                   if (name := _text(entry, "name", "code", "member"))
                   and (v := _first_num(entry, "value", "revenue", "amount")) and v > 0]
        if total > 0 and entries and _geo_reconciles(total, revenue):
            name, value = max(entries, key=lambda e: e[1])
            if value / total >= _COUNTRY_CONCENTRATION_WATCH:
                out.append(RiskSignal(
                    "info", "Geographic revenue concentration",
                    f"{value / total:.0%} of revenue from {name}",
                    "stockfit",
                ))
    products = [(name, v) for entry in _list_of_dicts(block, "product")
                if (name := _text(entry, "name", "member"))
                and (v := _first_num(entry, "value", "revenue", "amount")) and v > 0]
    denominator = revenue if revenue is not None and revenue > 0 else total
    comparable = [(name, v) for name, v in products if v <= denominator]
    if denominator > 0 and comparable:
        name, value = max(comparable, key=lambda e: e[1])
        if value / denominator >= _PRODUCT_CONCENTRATION_WATCH:
            out.append(RiskSignal(
                "info", "Product revenue concentration",
                f"{value / denominator:.0%} of revenue from {name}",
                "stockfit",
            ))
    return out


def _segment_signals(payload: Any) -> list[RiskSignal]:
    block = _latest_block(payload)
    if not isinstance(block, dict):
        return []
    segments = [
        (name, revenue)
        for entry in _list_of_dicts(block, "segments")
        if entry.get("role", "segment") == "segment"
        and (name := _text(entry, "name", "member"))
        and isinstance(entry.get("metrics"), dict)
        and (revenue := _first_num(entry["metrics"], "revenue")) is not None
        and revenue > 0
    ]
    if len(segments) < 2:
        return []
    total = sum(revenue for _, revenue in segments)
    name, revenue = max(segments, key=lambda e: e[1])
    if total <= 0 or revenue / total < _SEGMENT_CONCENTRATION_WATCH:
        return []
    return [RiskSignal(
        "info", "Segment revenue concentration",
        f"{revenue / total:.0%} of revenue from the {name} segment",
        "stockfit",
    )]


def footnote_risk_signals(
    footnotes: Optional[Mapping[str, Any]],
    market_cap: Optional[float] = None,
    revenue: Optional[float] = None,
) -> list[RiskSignal]:
    """Bear-case bullets from StockFit ``footnotes/*`` payloads (layer 2).

    Context only — never scored.  A bullet requires a documented, numeric
    field; unrecognized or missing data yields silence, never a fabricated
    signal.  ``market_cap`` (optional) is used solely for materiality checks,
    ``revenue`` (optional) is the filer's latest annual total revenue: the
    segmentation bullets are shares of it, and a geography block that does not
    reconcile with it says nothing at all.
    """
    if not footnotes:
        return []
    out: list[RiskSignal] = []
    out.extend(_concentration_signals(footnotes.get("concentration")))
    out.extend(_maturity_wall_signals(footnotes.get("debt-structure")))
    out.extend(_facility_signals(footnotes.get("credit-facilities")))
    out.extend(_stock_comp_signals(footnotes.get("stock-compensation"), market_cap))
    out.extend(_pension_signals(footnotes.get("retirement-plans"), market_cap))
    out.extend(_supplier_finance_signals(footnotes.get("supplier-finance"), market_cap))
    out.extend(_level3_signals(footnotes.get("fair-value-hierarchy")))
    out.extend(_revenue_segmentation_signals(footnotes.get("revenue-segmentation"), revenue))
    out.extend(_segment_signals(footnotes.get("business-segmentation")))
    out.sort(key=lambda s: SEVERITY_ORDER.get(s.severity, 9))
    return out
