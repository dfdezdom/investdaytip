"""Tests for the keyless risk-signal layer — pure functions, no I/O."""

from __future__ import annotations

from investdaytip.data_source import StockData
from investdaytip.financial_health import AltmanResult, PiotroskiResult
from investdaytip.risk_signals import risk_signals


def _alt(zone: str, z: float = 5.0) -> AltmanResult:
    return AltmanResult(z_score=z, zone=zone)


def _pio(**overrides: bool) -> PiotroskiResult:
    checks = {
        "roa_positive": True, "ocf_positive": True, "roa_improving": True,
        "accruals": True, "leverage_decreasing": True,
        "liquidity_improving": True, "no_dilution": True,
        "margin_improving": True, "turnover_improving": True,
    }
    checks.update(overrides)
    return PiotroskiResult(score=sum(checks.values()), checks=checks)


def test_clean_company_has_no_signals():
    data = StockData(
        ticker="CLEAN", profit_margin=0.20, free_cashflow=1e9,
        debt_to_equity=50.0, payout_ratio=0.4,
    )
    assert risk_signals(data, _pio(), _alt("safe")) == []


def test_altman_zones_drive_severity():
    data = StockData(ticker="X")
    assert risk_signals(data, altman=_alt("distress", 1.0))[0].severity == "high"
    assert risk_signals(data, altman=_alt("grey", 2.0))[0].severity == "medium"
    assert risk_signals(data, altman=_alt("safe")) == []


def test_balance_and_earnings_rules():
    data = StockData(
        ticker="RISK", profit_margin=-0.05, free_cashflow=-2e9,
        debt_to_equity=350.0, payout_ratio=1.2,
    )
    labels = [s.label for s in risk_signals(data)]
    assert "Loss-making" in labels
    assert "Negative free cash flow" in labels
    assert "High leverage" in labels
    assert "Dividend not covered by earnings" in labels


def test_payout_watch_is_info():
    data = StockData(ticker="X", payout_ratio=0.9)
    sigs = risk_signals(data)
    assert len(sigs) == 1
    assert sigs[0].severity == "info"


def test_piotroski_failed_checks_become_signals():
    pio = _pio(accruals=False, no_dilution=False, leverage_decreasing=False)
    labels = [s.label for s in risk_signals(StockData(ticker="X"), pio)]
    assert "Earnings quality (accruals)" in labels
    assert "Share dilution" in labels
    assert "Rising leverage" in labels


def test_both_deteriorating_checks_flagged_once():
    pio = _pio(margin_improving=False, roa_improving=False)
    sigs = risk_signals(StockData(ticker="X"), pio)
    labels = [s.label for s in sigs]
    assert labels.count("Deteriorating fundamentals") == 1
    # only one of the two failing → no deterioration signal
    assert [s.label for s in risk_signals(StockData(ticker="X"), _pio(roa_improving=False))] == []


def test_severity_ordering():
    data = StockData(ticker="X", profit_margin=-0.01)
    sigs = risk_signals(data, _pio(no_dilution=False), _alt("distress", 0.9))
    assert [s.severity for s in sigs] == ["high", "medium", "info"]
