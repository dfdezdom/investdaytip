"""Tests for financial_health — pure Piotroski / Altman computation, no I/O."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from investdaytip.financial_health import (
    altman_z_score,
    annual_facts,
    improvement_flags,
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
        "StockholdersEquity": 800.0,
    }
    base.update(kw)
    return base


def test_altman_z_hand_computed():
    # Z = 1.2*0.25 + 1.4*0.30 + 3.3*0.15 + 0.6*2.0 + 1.0*1.2
    #   = 0.30 + 0.42 + 0.495 + 1.20 + 1.20 = 3.615
    # X4 = book equity / total liabilities (StockFit-aligned, not market cap)
    result = altman_z_score(_altman_facts())
    assert result is not None
    assert result.z_score == pytest.approx(3.615)
    assert result.zone == "safe"


def test_altman_zones():
    # scale EBIT to move Z across the boundaries
    safe = altman_z_score(_altman_facts())
    grey = altman_z_score(_altman_facts(EBIT=-100.0, StockholdersEquity=200.0))
    distress = altman_z_score(_altman_facts(EBIT=-300.0, StockholdersEquity=50.0))
    assert safe is not None and safe.zone == "safe"
    assert grey is not None and grey.zone == "grey"
    assert 1.81 <= grey.z_score < 2.99
    assert distress is not None and distress.zone == "distress"


def test_altman_missing_or_invalid_returns_none():
    bad = _altman_facts()
    del bad["RetainedEarnings"]
    assert altman_z_score(bad) is None
    no_equity = _altman_facts()
    del no_equity["StockholdersEquity"]
    assert altman_z_score(no_equity) is None
    assert altman_z_score(_altman_facts(TotalLiabilities=0.0)) is None
    assert altman_z_score(_altman_facts(TotalAssets=-1.0)) is None


def test_altman_matches_stockfit_aapl_fy2025():
    """Regression: real AAPL FY2025 facts reproduce StockFit's ``altmanZScore`` (2.42)."""
    result = altman_z_score({
        "TotalAssets": 359241000000.0,
        "CurrentAssets": 147957000000.0,
        "CurrentLiabilities": 165631000000.0,
        "RetainedEarnings": -14264000000.0,
        "TotalLiabilities": 285508000000.0,
        "StockholdersEquity": 73733000000.0,
        "EBIT": 133050000000.0,
        "TotalRevenue": 416161000000.0,
    })
    assert result is not None
    assert round(result.z_score, 2) == 2.42
    assert result.zone == "grey"


def test_altman_x4_uses_parent_equity_not_total():
    """AMT FY2025: parent equity gives StockFit's 0.24; total equity would be 0.32."""
    facts = {
        "TotalAssets": 63190400000.0,
        "CurrentAssets": 2741800000.0,
        "CurrentLiabilities": 6913800000.0,
        "RetainedEarnings": -5086000000.0,
        "TotalLiabilities": 52835100000.0,
        "StockholdersEquity": 3652500000.0,   # attributable to the parent
        "EBIT": 4269600000.0,
        "TotalRevenue": 10644600000.0,
    }
    result = altman_z_score(facts)
    assert result is not None
    assert round(result.z_score, 2) == 0.24
    assert result.zone == "distress"


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


# ── improvement_flags ────────────────────────────────────────────────────────


def test_ttm_facts_sums_flows_and_takes_latest_balances():
    from investdaytip.financial_health import ttm_facts

    def frame(rows):
        cols = sorted({pd.Timestamp(c) for vals in rows.values() for c, _ in vals})
        data = {label: {pd.Timestamp(c): v for c, v in vals} for label, vals in rows.items()}
        return pd.DataFrame(data, index=cols).T

    q_income = frame({
        "Net Income": [("2024-03-31", 20.0), ("2024-06-30", 25.0),
                       ("2024-09-30", 30.0), ("2024-12-31", 35.0), ("2025-03-31", 40.0)],
        "Total Revenue": [("2024-03-31", 80.0), ("2024-06-30", 90.0),
                          ("2024-09-30", 100.0), ("2024-12-31", 110.0), ("2025-03-31", 120.0)],
    })
    q_balance = frame({
        "Total Assets": [("2024-12-31", 900.0), ("2025-03-31", 950.0)],
        "Stockholders Equity": [("2024-12-31", 190.0), ("2025-03-31", 200.0)],
    })
    q_cash = frame({
        "Free Cash Flow": [("2024-03-31", 5.0), ("2024-06-30", 6.0),
                           ("2024-09-30", 7.0), ("2024-12-31", 8.0), ("2025-03-31", 9.0)],
    })

    facts = ttm_facts(q_income, q_balance, q_cash)
    assert facts["NetIncome"] == pytest.approx(25.0 + 30.0 + 35.0 + 40.0)  # last 4 quarters
    assert facts["TotalRevenue"] == pytest.approx(90.0 + 100.0 + 110.0 + 120.0)
    assert facts["FreeCashFlow"] == pytest.approx(6.0 + 7.0 + 8.0 + 9.0)
    assert facts["TotalAssets"] == pytest.approx(950.0)        # latest quarter
    assert facts["StockholdersEquity"] == pytest.approx(200.0)


def test_ttm_facts_needs_four_quarters():
    from investdaytip.financial_health import ttm_facts

    short = pd.DataFrame(
        {"Net Income": [1.0, 2.0]},
        index=pd.DatetimeIndex([pd.Timestamp("2024-03-31"), pd.Timestamp("2024-06-30")]),
    ).T
    assert ttm_facts(short, None, None) == {}
    assert ttm_facts(None, None, None) == {}


def test_improvement_flags_basic():
    cur = {"GrossProfit": 200.0, "TotalRevenue": 400.0,
           "NetIncome": 100.0, "TotalAssets": 1000.0}
    prev = {"GrossProfit": 157.5, "TotalRevenue": 350.0,
            "NetIncome": 80.0, "TotalAssets": 1000.0}
    margin, roa = improvement_flags(cur, prev)
    assert margin is True  # 0.50 > 0.45
    assert roa is True     # 0.10 > 0.08


def test_improvement_flags_deterioration_and_unknown():
    cur = {"GrossProfit": 160.0, "TotalRevenue": 400.0,
           "NetIncome": 100.0, "TotalAssets": 1000.0}
    prev = {"GrossProfit": 157.5, "TotalRevenue": 350.0,
            "NetIncome": 80.0, "TotalAssets": 1000.0}
    margin, roa = improvement_flags(cur, prev)
    assert margin is False  # 0.40 < 0.45
    assert roa is True

    # missing previous gross profit → unknown, never False
    margin, _ = improvement_flags(cur, {"TotalRevenue": 350.0})
    assert margin is None
    # zero denominator → unknown
    _, roa = improvement_flags({**cur, "TotalAssets": 0.0}, prev)
    assert roa is None
