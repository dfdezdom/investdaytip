"""Tests for financial_health — pure Piotroski / Altman computation, no I/O."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from investdaytip.financial_health import (
    altman_z_score,
    annual_facts,
    piotroski_f_score,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _facts(**kw) -> dict[str, float]:
    base = {
        # current year: healthy, improving everywhere
        "NetIncome": 100.0,
        "TotalAssets": 1000.0,
        "OperatingCashFlow": 150.0,
        "TotalDebt": 200.0,
        "CurrentAssets": 400.0,
        "CurrentLiabilities": 200.0,
        "OrdinarySharesNumber": 10.0,
        "GrossProfit": 500.0,
        "TotalRevenue": 1000.0,
    }
    base.update(kw)
    return base


_PREV = {
    "NetIncome": 80.0,
    "TotalAssets": 1000.0,
    "OperatingCashFlow": 90.0,
    "TotalDebt": 250.0,
    "CurrentAssets": 380.0,
    "CurrentLiabilities": 200.0,
    "OrdinarySharesNumber": 11.0,
    "GrossProfit": 430.0,
    "TotalRevenue": 900.0,
}


# ── Piotroski ────────────────────────────────────────────────────────────────


def test_piotroski_all_checks_pass():
    result = piotroski_f_score(_facts(), _PREV)
    assert result is not None
    assert result.score == 9
    assert all(result.checks.values())


def test_piotroski_all_checks_fail():
    cur = _facts(
        NetIncome=-100.0,
        OperatingCashFlow=-150.0,
        TotalAssets=2000.0,
        TotalDebt=900.0,
        CurrentAssets=100.0,
        OrdinarySharesNumber=50.0,
        GrossProfit=100.0,
    )
    prev = {
        **_PREV,
        "NetIncome": 200.0,
        "TotalDebt": 100.0,
        "CurrentAssets": 800.0,
        "OrdinarySharesNumber": 10.0,
        "GrossProfit": 700.0,
    }
    result = piotroski_f_score(cur, prev)
    assert result is not None
    assert result.score == 0
    assert not any(result.checks.values())


def test_piotroski_individual_checks():
    # only the accruals check fails (OCF positive but below NI)
    result = piotroski_f_score(_facts(OperatingCashFlow=50.0), _PREV)
    assert result is not None
    assert result.score == 8
    assert result.checks["accruals"] is False
    assert result.checks["roa_positive"] is True


def test_piotroski_missing_fact_returns_none():
    bad = _facts()
    del bad["GrossProfit"]
    assert piotroski_f_score(bad, _PREV) is None
    bad_prev = dict(_PREV)
    del bad_prev["TotalDebt"]
    assert piotroski_f_score(_facts(), bad_prev) is None


def test_piotroski_nonpositive_denominators_return_none():
    assert piotroski_f_score(_facts(TotalAssets=0.0), _PREV) is None
    assert piotroski_f_score(_facts(), {**_PREV, "CurrentLiabilities": -1.0}) is None
    assert piotroski_f_score(_facts(), {**_PREV, "TotalRevenue": 0.0}) is None


# ── Altman ───────────────────────────────────────────────────────────────────


def _altman_facts(**kw) -> dict[str, float]:
    base = {
        "TotalAssets": 1000.0,
        "CurrentAssets": 500.0,
        "CurrentLiabilities": 250.0,
        "RetainedEarnings": 300.0,
        "EBIT": 150.0,
        "TotalLiabilities": 400.0,
        "TotalRevenue": 1200.0,
    }
    base.update(kw)
    return base


def test_altman_z_hand_computed():
    # Z = 1.2*0.25 + 1.4*0.30 + 3.3*0.15 + 0.6*2.0 + 1.0*1.2
    #   = 0.30 + 0.42 + 0.495 + 1.20 + 1.20 = 3.615
    result = altman_z_score(_altman_facts(), market_cap=800.0)
    assert result is not None
    assert result.z_score == pytest.approx(3.615)
    assert result.zone == "safe"


def test_altman_zones():
    # scale EBIT to move Z across the boundaries
    safe = altman_z_score(_altman_facts(), market_cap=800.0)
    grey = altman_z_score(_altman_facts(EBIT=-100.0), market_cap=200.0)
    distress = altman_z_score(_altman_facts(EBIT=-300.0), market_cap=50.0)
    assert safe is not None and safe.zone == "safe"
    assert grey is not None and grey.zone == "grey"
    assert 1.81 <= grey.z_score < 2.99
    assert distress is not None and distress.zone == "distress"


def test_altman_missing_or_invalid_returns_none():
    bad = _altman_facts()
    del bad["RetainedEarnings"]
    assert altman_z_score(bad, market_cap=800.0) is None
    assert altman_z_score(_altman_facts(), market_cap=None) is None
    assert altman_z_score(_altman_facts(TotalLiabilities=0.0), market_cap=1.0) is None
    assert altman_z_score(_altman_facts(TotalAssets=-1.0), market_cap=1.0) is None


# ── annual_facts ─────────────────────────────────────────────────────────────


def _frame(rows: dict[str, list[tuple[str, float]]]) -> pd.DataFrame:
    """rows: label -> [(column-date, value), …]. Columns are Timestamps (as in yfinance)."""
    cols = sorted({pd.Timestamp(c) for vals in rows.values() for c, _ in vals})
    data = {
        label: {pd.Timestamp(c): v for c, v in vals}
        for label, vals in rows.items()
    }
    return pd.DataFrame(data, index=cols).T


def test_annual_facts_current_and_previous_year():
    income = _frame({
        "Net Income": [("2023-12-31", 80.0), ("2024-12-31", 100.0)],
        "Total Revenue": [("2023-12-31", 900.0), ("2024-12-31", 1000.0)],
        "Gross Profit": [("2023-12-31", 450.0), ("2024-12-31", 500.0)],
    })
    balance = _frame({
        "Total Assets": [("2023-12-31", 950.0), ("2024-12-31", 1000.0)],
        "Ordinary Shares Number": [("2023-12-31", 11.0), ("2024-12-31", 10.0)],
    })
    cash = _frame({
        "Operating Cash Flow": [("2023-12-31", 90.0), ("2024-12-31", 150.0)],
    })

    as_of = datetime(2025, 3, 1)
    cur = annual_facts(income, balance, cash, as_of)
    assert cur["NetIncome"] == 100.0
    assert cur["TotalAssets"] == 1000.0
    assert cur["OperatingCashFlow"] == 150.0

    prev = annual_facts(income, balance, cash, as_of, years_back=1)
    assert prev["NetIncome"] == 80.0
    assert prev["OrdinarySharesNumber"] == 11.0
    assert prev["TotalRevenue"] == 900.0

    assert annual_facts(income, balance, cash, as_of, years_back=2) == {}


def test_annual_facts_skips_missing_rows_and_nan():
    income = _frame({
        "Net Income": [("2024-12-31", 100.0)],
        "EBIT": [("2024-12-31", float("nan"))],
    })
    facts = annual_facts(income, pd.DataFrame(), pd.DataFrame(), datetime(2025, 3, 1))
    assert facts == {"NetIncome": 100.0}


def test_annual_facts_empty_frames():
    assert annual_facts(None, None, None, datetime(2025, 3, 1)) == {}
    assert annual_facts(pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
                        datetime(2025, 3, 1)) == {}
