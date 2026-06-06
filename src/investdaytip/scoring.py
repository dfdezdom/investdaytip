"""Multi-factor scoring engines for long-term buy recommendations.

Stocks and ETFs are scored with different models but produce a unified
:class:`ScoredAsset` so they can be ranked together.

**Stock model** (Graham/Buffett + momentum, weights):
    Quality (35%)  — ROE, margins, growth
    Value   (25%)  — P/E, P/B, PEG
    Health  (20%)  — debt, liquidity, FCF
    Trend   (20%)  — 200d-SMA position, 12m return, SMA slope

**ETF model**:
    Returns        (40%)  — 3y, 5y, 12m
    Risk-Adjusted  (25%)  — Sharpe proxy, volatility
    Size/Liquidity (15%)  — AUM
    Cost & Yield   (20%)  — expense ratio (lower=better), dividend yield

Both models normalize each metric to 0–100 via piecewise-linear
functions and fall back to a neutral 50 when data is missing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from investdaytip.data_source import AssetData, EtfData, StockData
from investdaytip.dataroma import get_superinvestor_data

STOCK_WEIGHTS = {
    "quality": 0.35,
    "value": 0.25,
    "health": 0.20,
    "trend": 0.20,
}

ETF_WEIGHTS = {
    "returns": 0.40,
    "risk_adj": 0.25,
    "size": 0.15,
    "cost_yield": 0.20,
}


@dataclass
class ScoredAsset:
    data: AssetData
    asset_type: str  # "STOCK" or "ETF"
    total: float
    breakdown: dict[str, float] = field(default_factory=dict)
    rationale: list[str] = field(default_factory=list)
    superinvestor_count: Optional[int] = None


# Backwards-compatible alias (existing tests / external callers)
ScoredStock = ScoredAsset


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _linear(value: Optional[float], best: float, worst: float, *, default: float = 50.0) -> float:
    """Piecewise-linear normalization of ``value`` to a 0-100 score.

    ``best`` maps to 100 and ``worst`` to 0, clamped at both ends. The
    direction is implied by the arguments: for "higher is better" metrics pass
    ``best > worst``; for "lower is better" metrics (e.g. debt, expense ratio)
    pass ``best < worst`` and the negative denominator flips the slope
    correctly. Missing data (``None``) or a degenerate ``best == worst`` range
    falls back to the neutral ``default``.
    """
    if value is None:
        return default
    if best == worst:
        return default
    pct = (value - worst) / (best - worst) * 100.0
    return _clamp(pct)


# ---------------------------------------------------------------------------
# Stock scoring
# ---------------------------------------------------------------------------

def _value_score(d: StockData) -> tuple[float, list[str]]:
    notes: list[str] = []
    pe = _linear(d.trailing_pe, best=10, worst=40)
    pb = _linear(d.price_to_book, best=1.0, worst=6.0)
    peg = _linear(d.peg_ratio, best=0.8, worst=3.0)
    if d.trailing_pe is not None and d.trailing_pe < 20:
        notes.append(f"attractive P/E of {d.trailing_pe:.1f}")
    if d.peg_ratio is not None and d.peg_ratio < 1.5:
        notes.append(f"PEG of {d.peg_ratio:.2f} suggests growth at reasonable price")
    return (pe * 0.45) + (pb * 0.20) + (peg * 0.35), notes


def _quality_score(d: StockData) -> tuple[float, list[str]]:
    notes: list[str] = []
    roe = _linear(d.return_on_equity, best=0.30, worst=0.05)
    margin = _linear(d.profit_margin, best=0.25, worst=0.02)
    earn_g = _linear(d.earnings_growth, best=0.25, worst=-0.05)
    rev_g = _linear(d.revenue_growth, best=0.20, worst=-0.05)
    if d.return_on_equity is not None and d.return_on_equity > 0.15:
        notes.append(f"strong ROE of {d.return_on_equity * 100:.1f}%")
    if d.profit_margin is not None and d.profit_margin > 0.15:
        notes.append(f"healthy profit margin of {d.profit_margin * 100:.1f}%")
    if d.earnings_growth is not None and d.earnings_growth > 0.10:
        notes.append(f"earnings growth of {d.earnings_growth * 100:.1f}%")
    return (roe * 0.35) + (margin * 0.25) + (earn_g * 0.25) + (rev_g * 0.15), notes


def _health_score(d: StockData) -> tuple[float, list[str]]:
    notes: list[str] = []
    # yfinance reports debtToEquity as a percentage (e.g. 45.3 == 0.453x), so
    # divide by 100 to get the ratio compared against best=0.2x / worst=2.0x.
    de = d.debt_to_equity / 100.0 if d.debt_to_equity is not None else None
    debt = _linear(de, best=0.2, worst=2.0)
    liq = _linear(d.current_ratio, best=2.5, worst=1.0)
    fcf = 50.0
    if d.free_cashflow is not None:
        fcf = 80.0 if d.free_cashflow > 0 else 20.0
    if de is not None and de < 0.5:
        notes.append(f"low leverage (D/E={de:.2f})")
    if d.free_cashflow is not None and d.free_cashflow > 0:
        notes.append("positive free cash flow")
    return (debt * 0.45) + (liq * 0.25) + (fcf * 0.30), notes


def _technical_score(d: StockData | EtfData) -> tuple[float, list[str]]:
    """Score technical indicators (RSI + MACD) as a sub-component of Trend.

    RSI is inverted (lower = better entry) with a floor at 20 to avoid
    rewarding stocks in free-fall.  MACD histogram rewards positive momentum.
    """
    notes: list[str] = []
    rsi_raw = d.rsi_14
    if rsi_raw is not None and rsi_raw < 20.0:
        rsi_raw = 20.0
    rsi = _linear(rsi_raw, best=35.0, worst=65.0, default=50.0)
    macd = _linear(d.macd_histogram, best=0.05, worst=-0.05, default=50.0)
    if d.rsi_14 is not None and d.rsi_14 < 30.0:
        notes.append(f"RSI {d.rsi_14:.1f} suggests oversold")
    if d.macd_histogram is not None and d.macd_histogram > 0.0:
        notes.append("MACD histogram positive")
    return (rsi * 0.15) + (macd * 0.25), notes


def _trend_score(d: StockData, *, include_technical: bool = False) -> tuple[float, list[str]]:
    notes: list[str] = []
    pvs = _linear(d.price_vs_sma200, best=0.15, worst=-0.20)
    r12 = _linear(d.return_12m, best=0.30, worst=-0.20)
    slope = _linear(d.sma200_slope, best=0.10, worst=-0.10)
    if include_technical:
        tech, tech_notes = _technical_score(d)
        notes.extend(tech_notes)
    else:
        tech = 0.0
    if d.price_vs_sma200 is not None and d.price_vs_sma200 > 0:
        notes.append(f"trading {d.price_vs_sma200 * 100:.1f}% above 200d SMA")
    if d.return_12m is not None and d.return_12m > 0.10:
        notes.append(f"12-month return of {d.return_12m * 100:.1f}%")
    if include_technical:
        return (pvs * 0.20) + (r12 * 0.20) + (slope * 0.20) + tech, notes
    return (pvs * 0.35) + (r12 * 0.30) + (slope * 0.35), notes


def score_stock(data: StockData, *, include_technical: bool = False) -> ScoredAsset:
    quality, q_notes = _quality_score(data)
    value, v_notes = _value_score(data)
    health, h_notes = _health_score(data)
    trend, t_notes = _trend_score(data, include_technical=include_technical)

    total = (
        quality * STOCK_WEIGHTS["quality"]
        + value * STOCK_WEIGHTS["value"]
        + health * STOCK_WEIGHTS["health"]
        + trend * STOCK_WEIGHTS["trend"]
    )
    rationale = q_notes + v_notes + h_notes + t_notes
    if not rationale:
        rationale.append("Limited data available; score based on neutral defaults.")

    si_data = get_superinvestor_data()
    si_count = si_data.get(data.ticker, {}).get("manager_count")

    return ScoredAsset(
        data=data,
        asset_type="STOCK",
        total=total,
        breakdown={"Quality": quality, "Value": value, "Health": health, "Trend": trend},
        rationale=rationale,
        superinvestor_count=si_count,
    )


# ---------------------------------------------------------------------------
# ETF scoring
# ---------------------------------------------------------------------------

def _etf_returns_score(d: EtfData) -> tuple[float, list[str]]:
    notes: list[str] = []
    r3 = _linear(d.three_year_return, best=0.15, worst=0.00)
    r5 = _linear(d.five_year_return, best=0.12, worst=0.00)
    r12 = _linear(d.return_12m, best=0.20, worst=-0.10)
    if d.five_year_return is not None and d.five_year_return > 0.08:
        notes.append(f"5y avg return {d.five_year_return * 100:.1f}%")
    if d.three_year_return is not None and d.three_year_return > 0.10:
        notes.append(f"3y avg return {d.three_year_return * 100:.1f}%")
    return (r3 * 0.35) + (r5 * 0.40) + (r12 * 0.25), notes


def _etf_risk_adj_score(d: EtfData) -> tuple[float, list[str]]:
    notes: list[str] = []
    sharpe = _linear(d.sharpe_proxy, best=1.5, worst=-0.5)
    # Lower volatility preferred for long-term, but only mildly
    vol = _linear(d.volatility_1y, best=0.10, worst=0.40)
    if d.sharpe_proxy is not None and d.sharpe_proxy > 0.8:
        notes.append(f"strong risk-adjusted return (Sharpe≈{d.sharpe_proxy:.2f})")
    if d.volatility_1y is not None and d.volatility_1y < 0.15:
        notes.append(f"low volatility ({d.volatility_1y * 100:.1f}%)")
    return (sharpe * 0.70) + (vol * 0.30), notes


def _etf_size_score(d: EtfData) -> tuple[float, list[str]]:
    notes: list[str] = []
    # log-scale AUM: $100M -> 0, $100B -> 100
    if d.total_assets is None or d.total_assets <= 0:
        return 50.0, notes
    log_aum = math.log10(d.total_assets)
    score = _linear(log_aum, best=11.0, worst=8.0)  # 1e11=100B, 1e8=100M
    if d.total_assets >= 10_000_000_000:
        notes.append(f"large AUM of ${d.total_assets / 1e9:.1f}B")
    return score, notes


def _etf_cost_yield_score(d: EtfData) -> tuple[float, list[str]]:
    notes: list[str] = []
    # expense ratio: 0.03% best, 0.75% worst
    cost = _linear(d.expense_ratio, best=0.0003, worst=0.0075, default=60.0)
    yld = _linear(d.yield_, best=0.04, worst=0.00, default=50.0)
    if d.expense_ratio is not None and d.expense_ratio < 0.001:
        notes.append(f"ultra-low expense ratio ({d.expense_ratio * 100:.2f}%)")
    if d.yield_ is not None and d.yield_ > 0.02:
        notes.append(f"yields {d.yield_ * 100:.2f}%")
    return (cost * 0.65) + (yld * 0.35), notes


def score_etf(data: EtfData) -> ScoredAsset:
    ret, r_notes = _etf_returns_score(data)
    risk, k_notes = _etf_risk_adj_score(data)
    size, s_notes = _etf_size_score(data)
    cy, c_notes = _etf_cost_yield_score(data)

    total = (
        ret * ETF_WEIGHTS["returns"]
        + risk * ETF_WEIGHTS["risk_adj"]
        + size * ETF_WEIGHTS["size"]
        + cy * ETF_WEIGHTS["cost_yield"]
    )
    rationale = r_notes + k_notes + s_notes + c_notes
    if not rationale:
        rationale.append("Limited data available; score based on neutral defaults.")

    return ScoredAsset(
        data=data,
        asset_type="ETF",
        total=total,
        breakdown={"Returns": ret, "RiskAdj": risk, "Size": size, "Cost/Yield": cy},
        rationale=rationale,
    )


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def score_asset(data: AssetData, *, include_technical: bool = False) -> ScoredAsset:
    if isinstance(data, EtfData):
        return score_etf(data)
    return score_stock(data, include_technical=include_technical)

