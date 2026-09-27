"""Piotroski F-Score and Altman Z-Score — pure computation from annual facts.

No I/O, no side effects (same posture as ``scoring.py``).  Both metrics are
computed from **annual** (fiscal-year) statements; the callers are
``scripts/factor_ic.py`` (validation) and, later, the scoring models.

Fact keys are the normalized camelCase names used across the backtest layer
(``NetIncome``, ``TotalAssets``, …) and are produced by
:func:`annual_facts` from yfinance statement frames.

Missing data is never "passed": a metric that cannot be fully computed
returns ``None`` rather than a partial score, so cross-sectional
comparisons only ever rank complete observations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pandas as pd

# Internal fact name -> yfinance yearly-statement row label.
_FACT_ROWS: dict[str, str] = {
    "NetIncome": "Net Income",
    "TotalRevenue": "Total Revenue",
    "GrossProfit": "Gross Profit",
    "EBIT": "EBIT",
    "OperatingCashFlow": "Operating Cash Flow",
    "TotalAssets": "Total Assets",
    "CurrentAssets": "Current Assets",
    "CurrentLiabilities": "Current Liabilities",
    "TotalDebt": "Total Debt",
    "TotalLiabilities": "Total Liabilities Net Minority Interest",
    "RetainedEarnings": "Retained Earnings",
    "OrdinarySharesNumber": "Ordinary Shares Number",
}

_PIOTROSKI_CHECKS: tuple[str, ...] = (
    "roa_positive",
    "ocf_positive",
    "roa_improving",
    "accruals",
    "leverage_decreasing",
    "liquidity_improving",
    "no_dilution",
    "margin_improving",
    "turnover_improving",
)


@dataclass
class PiotroskiResult:
    """Nine binary checks (1 = passed) and the resulting F-Score (0-9)."""

    score: int
    checks: dict[str, bool] = field(default_factory=dict)


@dataclass
class AltmanResult:
    """Altman Z-Score plus its zone ("safe" | "grey" | "distress")."""

    z_score: float
    zone: str


# ── Statement extraction ─────────────────────────────────────────────────────


def _col_at(df: pd.DataFrame, as_of: datetime, years_back: int) -> Optional[pd.Timestamp]:
    """Fiscal-year column ``years_back`` steps before the latest column ≤ *as_of*."""
    if df is None or df.empty:
        return None
    cols = sorted(c for c in df.columns if not pd.isna(c))
    candidates = [c for c in cols if c <= pd.Timestamp(as_of)]
    if not candidates:
        return None
    idx = len(candidates) - 1 - years_back
    return candidates[idx] if idx >= 0 else None


def annual_facts(
    income_stmt: Optional[pd.DataFrame],
    balance_sheet: Optional[pd.DataFrame],
    cash_flow: Optional[pd.DataFrame],
    as_of: datetime,
    years_back: int = 0,
) -> dict[str, float]:
    """Normalized facts for one fiscal year visible at *as_of*.

    ``years_back=0`` picks the latest fiscal year ending on or before
    *as_of* (the caller is expected to already have gated *as_of* by the
    reporting lag); ``years_back=1`` the previous one.  Only finite values
    are returned — missing rows/NaNs are simply absent from the dict.
    """
    frames = {
        "income": income_stmt,
        "balance": balance_sheet,
        "cash": cash_flow,
    }
    facts: dict[str, float] = {}
    for name, row_label in _FACT_ROWS.items():
        for frame in frames.values():
            if frame is None or frame.empty or row_label not in frame.index:
                continue
            col = _col_at(frame, as_of, years_back)
            if col is None:
                continue
            val = frame.loc[row_label, col]
            if isinstance(val, pd.Series):
                val = val.iloc[0]
            if not pd.isna(val):
                facts[name] = float(val)
            break
    return facts


# ── Piotroski F-Score ────────────────────────────────────────────────────────


def _require(*values: Optional[float]) -> Optional[tuple[float, ...]]:
    """Return all values non-None, or ``None`` if any is missing (mypy-safe)."""
    out: list[float] = []
    for v in values:
        if v is None:
            return None
        out.append(v)
    return tuple(out)


def piotroski_f_score(
    cur: dict[str, float], prev: dict[str, Optional[float]]
) -> Optional[PiotroskiResult]:
    """Piotroski F-Score (0-9) over two consecutive fiscal years.

    Checks (Piotroski 2000):

    - profitability: ROA > 0, OCF > 0, ROA improving, OCF > NI (accruals)
    - leverage/liquidity: debt/assets improving, current ratio improving,
      no share dilution
    - operating efficiency: gross margin improving, asset turnover improving

    Returns ``None`` when any required fact is missing or a denominator is
    non-positive — a score built from fewer than nine checks would not be
    comparable across companies.
    """
    def _get(d: dict, key: str) -> Optional[float]:
        v = d.get(key)
        return v if isinstance(v, (int, float)) else None

    vals = _require(
        _get(cur, "NetIncome"), _get(cur, "TotalAssets"), _get(cur, "OperatingCashFlow"),
        _get(cur, "TotalDebt"), _get(cur, "CurrentAssets"), _get(cur, "CurrentLiabilities"),
        _get(cur, "OrdinarySharesNumber"), _get(cur, "GrossProfit"), _get(cur, "TotalRevenue"),
        _get(prev, "NetIncome"), _get(prev, "TotalAssets"), _get(prev, "TotalDebt"),
        _get(prev, "CurrentAssets"), _get(prev, "CurrentLiabilities"),
        _get(prev, "OrdinarySharesNumber"), _get(prev, "GrossProfit"),
        _get(prev, "TotalRevenue"),
    )
    if vals is None:
        return None
    (ni, ta, ocf, td, ca, cl, sh, gp, rev,
     p_ni, p_ta, p_td, p_ca, p_cl, p_sh, p_gp, p_rev) = vals
    if ta <= 0 or cl <= 0 or rev <= 0 or p_ta <= 0 or p_cl <= 0 or p_rev <= 0:
        return None

    roa = ni / ta
    roa_prev = p_ni / p_ta
    checks = {
        "roa_positive": roa > 0,
        "ocf_positive": ocf > 0,
        "roa_improving": roa > roa_prev,
        "accruals": ocf > ni,
        "leverage_decreasing": (td / ta) < (p_td / p_ta),
        "liquidity_improving": (ca / cl) > (p_ca / p_cl),
        "no_dilution": sh <= p_sh,
        "margin_improving": (gp / rev) > (p_gp / p_rev),
        "turnover_improving": (rev / ta) > (p_rev / p_ta),
    }
    return PiotroskiResult(
        score=sum(1 for k in _PIOTROSKI_CHECKS if checks[k]),
        checks=checks,
    )


# ── Altman Z-Score ───────────────────────────────────────────────────────────


def altman_z_score(
    cur: dict[str, float], market_cap: Optional[float]
) -> Optional[AltmanResult]:
    """Altman Z-Score (1968, manufacturing model) and its zone.

    ``Z = 1.2·WC/TA + 1.4·RE/TA + 3.3·EBIT/TA + 0.6·MV/TL + 1.0·Sales/TA``

    Zones: ``>= 2.99`` safe, ``>= 1.81`` grey, else distress.  Returns
    ``None`` when any input is missing or ``TotalAssets`` / ``TotalLiabilities``
    are non-positive.  Note the model was built for non-financial
    manufacturers — banks (no current-asset split) simply return ``None``.
    """
    vals = _require(
        cur.get("TotalAssets"), cur.get("CurrentAssets"),
        cur.get("CurrentLiabilities"), cur.get("RetainedEarnings"),
        cur.get("EBIT"), cur.get("TotalLiabilities"),
        cur.get("TotalRevenue"), market_cap,
    )
    if vals is None:
        return None
    (ta, ca, cl, re, ebit, tl, rev, mv) = vals
    if ta <= 0 or tl <= 0:
        return None

    z = (
        1.2 * (ca - cl) / ta
        + 1.4 * re / ta
        + 3.3 * ebit / ta
        + 0.6 * mv / tl
        + 1.0 * rev / ta
    )
    zone = "safe" if z >= 2.99 else ("grey" if z >= 1.81 else "distress")
    return AltmanResult(z_score=z, zone=zone)
