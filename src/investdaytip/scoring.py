"""Multi-factor scoring engines for long-term buy recommendations.

Stocks and ETFs are scored with different models but produce a unified
:class:`ScoredAsset` so they can be ranked together.

Two stock scoring models are available, selectable via ``model=``:

**``quant``** (default) — Seeking-Alpha-inspired five-factor model:
    Value            (25%)  — P/E, P/B, PEG, FCF yield
    Growth           (20%)  — earnings growth, revenue growth
    Profitability    (25%)  — ROE, ROA, profit margin
    Momentum         (15%)  — 12m return, vs SMA200, SMA200 slope
    EPS Revisions    (15%)  — average EPS surprise vs analyst estimates

**``classic``** — Graham/Buffett + momentum:
    Quality (35%)  — ROE, margins, growth
    Value   (25%)  — P/E, P/B, PEG
    Health  (20%)  — debt, liquidity, FCF
    Trend   (20%)  — 200d-SMA position, 12m return, SMA slope
Stocks that fail a "disqualifying grade" on any high-impact factor are capped
at a neutral total score (50), preventing one strong factor from masking a
serious red flag elsewhere.

**ETF model**:
    Returns        (40%)  — 3y, 5y, 12m
    Risk-Adjusted  (25%)  — Sharpe proxy, volatility
    Size/Liquidity (15%)  — AUM
    Cost & Yield   (20%)  — expense ratio (lower=better), dividend yield

Both stock models and the ETF model normalize each metric to 0–100 via
piecewise-linear functions and fall back to a neutral 50 when data is missing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from investdaytip.data_source import AssetData, EtfData, StockData
from investdaytip.dataroma import get_superinvestor_data

STOCK_WEIGHTS_CLASSIC = {
    "quality": 0.35,
    "value": 0.25,
    "health": 0.20,
    "trend": 0.20,
}

STOCK_WEIGHTS_QUANT = {
    "value": 0.25,
    "growth": 0.20,
    "profitability": 0.25,
    "momentum": 0.15,
    "eps_revisions": 0.15,
}

ETF_WEIGHTS = {
    "returns": 0.40,
    "risk_adj": 0.25,
    "size": 0.15,
    "cost_yield": 0.20,
}


def resolve_include_technical(include_technical: bool | None, scoring_model: str) -> bool:
    """Return the effective ``include_technical`` flag.

    ``None`` defaults to ``True`` for the ``"quant"`` model and ``False`` for
    ``"classic"``, so users get the validated best default for each model.
    """
    if include_technical is not None:
        return include_technical
    return scoring_model == "quant"


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
    correctly. Missing data (``None``), non-finite values (``NaN``/``inf``),
    or a degenerate ``best == worst`` range falls back to the neutral ``default``.
    """
    if value is None or not math.isfinite(value):
        return default
    if best == worst:
        return default
    pct = (value - worst) / (best - worst) * 100.0
    return _clamp(pct)


# ---------------------------------------------------------------------------
# Helpers shared by both stock scorers
# ---------------------------------------------------------------------------

def _technical_score(d: StockData | EtfData) -> tuple[float, list[str]]:
    """Score technical indicators (RSI + MACD) as a 0-100 sub-component of Trend.

    RSI is inverted (lower = better entry) with a floor at 20 to avoid
    rewarding stocks in free-fall.  MACD histogram rewards positive momentum.
    Returns a normalized 0-100 score; caller multiplies by the trend weight (0.40).
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
    return (rsi * 0.375) + (macd * 0.625), notes


# ---------------------------------------------------------------------------
# Classic stock scoring
# ---------------------------------------------------------------------------

class ClassicStockScorer:
    """Original InvestDayTip stock scoring model."""

    def _value_score(self, d: StockData) -> tuple[float, list[str]]:
        notes: list[str] = []
        pe = _linear(d.trailing_pe, best=10, worst=40)
        pb = _linear(d.price_to_book, best=1.0, worst=6.0)
        peg = _linear(d.peg_ratio, best=0.8, worst=3.0)
        if d.trailing_pe is not None and d.trailing_pe < 20:
            notes.append(f"attractive P/E of {d.trailing_pe:.1f}")
        if d.peg_ratio is not None and d.peg_ratio < 1.5:
            notes.append(f"PEG of {d.peg_ratio:.2f} suggests growth at reasonable price")
        return (pe * 0.45) + (pb * 0.20) + (peg * 0.35), notes

    def _quality_score(self, d: StockData) -> tuple[float, list[str]]:
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

    def _health_score(self, d: StockData) -> tuple[float, list[str]]:
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

    def _trend_score(self, d: StockData, *, include_technical: bool = False) -> tuple[float, list[str]]:
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
            return (pvs * 0.20) + (r12 * 0.20) + (slope * 0.20) + (tech * 0.40), notes
        return (pvs * 0.35) + (r12 * 0.30) + (slope * 0.35), notes

    def score(
        self,
        data: StockData,
        *,
        include_technical: bool = False,
        si_data: dict | None = None,
    ) -> ScoredAsset:
        quality, q_notes = self._quality_score(data)
        value, v_notes = self._value_score(data)
        health, h_notes = self._health_score(data)
        trend, t_notes = self._trend_score(data, include_technical=include_technical)

        total = (
            quality * STOCK_WEIGHTS_CLASSIC["quality"]
            + value * STOCK_WEIGHTS_CLASSIC["value"]
            + health * STOCK_WEIGHTS_CLASSIC["health"]
            + trend * STOCK_WEIGHTS_CLASSIC["trend"]
        )
        rationale = q_notes + v_notes + h_notes + t_notes
        if not rationale:
            rationale.append("Limited data available; score based on neutral defaults.")

        if si_data is None:
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
# Quant stock scoring (Seeking-Alpha-inspired)
# ---------------------------------------------------------------------------

class QuantStockScorer:
    """Five-factor stock scoring model with disqualifying grades.

    Inspired by Seeking Alpha Quant Ratings: Value, Growth, Profitability,
    Momentum and EPS Revisions. A single very poor factor caps the overall
    score at neutral, preventing red flags from being hidden by strengths
    elsewhere.
    """

    # Thresholds below which a factor is considered a disqualifying red flag.
    _DISQUALIFY_SOFT = 20.0  # Growth, Momentum, EPS Revisions
    _DISQUALIFY_HARD = 15.0  # Value, Profitability

    def _value_score(self, d: StockData) -> tuple[float, list[str]]:
        notes: list[str] = []
        pe = _linear(d.trailing_pe, best=10, worst=40)
        pb = _linear(d.price_to_book, best=1.0, worst=6.0)
        peg = _linear(d.peg_ratio, best=0.8, worst=3.0)

        fcf_yield = 50.0
        if d.free_cashflow is not None and d.market_cap is not None and d.market_cap > 0:
            fcf_yield = _linear(d.free_cashflow / d.market_cap, best=0.10, worst=0.00, default=50.0)

        if d.trailing_pe is not None and d.trailing_pe < 20:
            notes.append(f"attractive P/E of {d.trailing_pe:.1f}")
        if d.peg_ratio is not None and d.peg_ratio < 1.5:
            notes.append(f"PEG of {d.peg_ratio:.2f} suggests growth at reasonable price")
        if fcf_yield > 70:
            notes.append("strong free cash flow yield")

        return (pe * 0.35) + (pb * 0.20) + (peg * 0.25) + (fcf_yield * 0.20), notes

    def _growth_score(self, d: StockData) -> tuple[float, list[str]]:
        notes: list[str] = []
        earn_g = _linear(d.earnings_growth, best=0.25, worst=-0.05)
        rev_g = _linear(d.revenue_growth, best=0.20, worst=-0.05)
        if d.earnings_growth is not None and d.earnings_growth > 0.10:
            notes.append(f"earnings growth of {d.earnings_growth * 100:.1f}%")
        if d.revenue_growth is not None and d.revenue_growth > 0.08:
            notes.append(f"revenue growth of {d.revenue_growth * 100:.1f}%")
        return (earn_g * 0.55) + (rev_g * 0.45), notes

    def _profitability_score(self, d: StockData) -> tuple[float, list[str]]:
        notes: list[str] = []
        roe = _linear(d.return_on_equity, best=0.30, worst=0.05)
        roa = _linear(d.return_on_assets, best=0.15, worst=0.01)
        margin = _linear(d.profit_margin, best=0.25, worst=0.02)
        if d.return_on_equity is not None and d.return_on_equity > 0.15:
            notes.append(f"strong ROE of {d.return_on_equity * 100:.1f}%")
        if d.profit_margin is not None and d.profit_margin > 0.15:
            notes.append(f"healthy profit margin of {d.profit_margin * 100:.1f}%")
        return (roe * 0.45) + (margin * 0.35) + (roa * 0.20), notes

    def _momentum_score(self, d: StockData) -> tuple[float, list[str]]:
        notes: list[str] = []
        pvs = _linear(d.price_vs_sma200, best=0.15, worst=-0.20)
        r12 = _linear(d.return_12m, best=0.30, worst=-0.20)
        slope = _linear(d.sma200_slope, best=0.10, worst=-0.10)
        if d.return_12m is not None and d.return_12m > 0.10:
            notes.append(f"12-month return of {d.return_12m * 100:.1f}%")
        if d.price_vs_sma200 is not None and d.price_vs_sma200 > 0:
            notes.append(f"trading {d.price_vs_sma200 * 100:.1f}% above 200d SMA")
        return (pvs * 0.35) + (r12 * 0.40) + (slope * 0.25), notes

    def _eps_revisions_score(self, d: StockData) -> tuple[float, list[str]]:
        """EPS estimate-revision factor based on reported EPS surprises.

        We average the percentage surprise (Reported EPS vs Estimate) over the
        last four reported quarters.  Positive surprises indicate analysts were
        too pessimistic and are likely revising estimates upward; negative
        surprises suggest downward revisions.  When no earnings-dates data is
        available the factor falls back to neutral.
        """
        notes: list[str] = []
        if d.eps_surprise is None:
            notes.append("EPS surprise data unavailable — install lxml for earnings analysis")
            return 50.0, notes

        score = _linear(d.eps_surprise, best=15.0, worst=-15.0, default=50.0)
        if d.eps_surprise > 5.0:
            notes.append(f"EPS beat estimates by {d.eps_surprise:.1f}% on average")
        elif d.eps_surprise < -5.0:
            notes.append(f"EPS missed estimates by {abs(d.eps_surprise):.1f}% on average")
        return score, notes

    def score(
        self,
        data: StockData,
        *,
        include_technical: bool = False,
        si_data: dict | None = None,
    ) -> ScoredAsset:
        value, v_notes = self._value_score(data)
        growth, g_notes = self._growth_score(data)
        profitability, p_notes = self._profitability_score(data)
        momentum, m_notes = self._momentum_score(data)
        eps_rev, e_notes = self._eps_revisions_score(data)

        if include_technical:
            tech, tech_notes = _technical_score(data)
            momentum = (momentum * 0.70) + (tech * 0.30)
            m_notes.extend(tech_notes)

        disqualified = False
        if growth < self._DISQUALIFY_SOFT:
            disqualified = True
            g_notes.append("growth flagged as disqualifying")
        if momentum < self._DISQUALIFY_SOFT:
            disqualified = True
            m_notes.append("momentum flagged as disqualifying")
        if eps_rev < self._DISQUALIFY_SOFT:
            disqualified = True
            e_notes.append("EPS revisions flagged as disqualifying")
        if value < self._DISQUALIFY_HARD:
            disqualified = True
            v_notes.append("valuation flagged as disqualifying")
        if profitability < self._DISQUALIFY_HARD:
            disqualified = True
            p_notes.append("profitability flagged as disqualifying")

        total = (
            value * STOCK_WEIGHTS_QUANT["value"]
            + growth * STOCK_WEIGHTS_QUANT["growth"]
            + profitability * STOCK_WEIGHTS_QUANT["profitability"]
            + momentum * STOCK_WEIGHTS_QUANT["momentum"]
            + eps_rev * STOCK_WEIGHTS_QUANT["eps_revisions"]
        )
        if disqualified:
            total = min(total, 50.0)
            if total <= 50.0:
                rationale = ["Disqualifying factor(s) capped score at neutral"] + v_notes + g_notes + p_notes + m_notes + e_notes
            else:
                rationale = v_notes + g_notes + p_notes + m_notes + e_notes
        else:
            rationale = v_notes + g_notes + p_notes + m_notes + e_notes

        if not rationale:
            rationale.append("Limited data available; score based on neutral defaults.")

        breakdown = {
            "Value": value,
            "Growth": growth,
            "Profitability": profitability,
            "Momentum": momentum,
            "EPS Revisions": eps_rev,
        }

        if si_data is None:
            si_data = get_superinvestor_data()
        si_count = si_data.get(data.ticker, {}).get("manager_count")

        return ScoredAsset(
            data=data,
            asset_type="STOCK",
            total=total,
            breakdown=breakdown,
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

def score_stock(
    data: StockData,
    *,
    model: str = "quant",
    include_technical: bool = False,
    si_data: dict | None = None,
) -> ScoredAsset:
    """Score a single stock using the selected model.

    Args:
        data: populated ``StockData`` instance.
        model: ``"quant"`` (default) or ``"classic"``.
        include_technical: blend RSI/MACD into the trend/momentum component.
        si_data: optional pre-loaded superinvestor data cache.
    """
    if model == "classic":
        return ClassicStockScorer().score(data, include_technical=include_technical, si_data=si_data)
    return QuantStockScorer().score(data, include_technical=include_technical, si_data=si_data)


def score_asset(
    data: AssetData,
    *,
    model: str = "quant",
    include_technical: bool = False,
    si_data: dict | None = None,
) -> ScoredAsset:
    if isinstance(data, EtfData):
        return score_etf(data)
    return score_stock(data, model=model, include_technical=include_technical, si_data=si_data)
