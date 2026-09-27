"""Keyless risk signals — the local "devil's advocate" layer (product Fase 3).

Turns the data InvestDayTip already has into explicit bear-case bullets:
Altman zone, failed Piotroski checks, leverage, payout ratio, losses and
negative free cash flow.  Pure functions only — no I/O (same posture as
``scoring.py`` / ``financial_health.py``).

These signals are **context, never a score**: they inform the decision, they
do not change rankings.  The richer footnotes-based bear case (StockFit
``footnotes/*``) is the key-gated layer on top of this one.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from investdaytip.data_source import StockData
from investdaytip.financial_health import AltmanResult, PiotroskiResult

SEVERITY_ORDER = {"high": 0, "medium": 1, "info": 2}

# Heuristic thresholds (documented so they can be debated/tuned).
_DEBT_TO_EQUITY_ALERT = 200.0  # yfinance-style percent (200 = 2.0x)
_PAYOUT_ALERT = 1.0            # dividend > earnings
_PAYOUT_WATCH = 0.85


@dataclass
class RiskSignal:
    """One bear-case bullet: ``severity`` is high | medium | info."""

    severity: str
    label: str
    detail: str


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
