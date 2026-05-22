"""Tests for the ETF scoring engine."""

from investdaytip.data_source import EtfData
from investdaytip.scoring import score_etf, score_asset


def test_strong_etf_scores_high():
    d = EtfData(
        ticker="GOODETF",
        name="Good Index ETF",
        category="Large Blend",
        total_assets=200_000_000_000,
        expense_ratio=0.0003,
        three_year_return=0.18,
        five_year_return=0.14,
        beta_3y=1.0,
        yield_=0.02,
        return_12m=0.22,
        volatility_1y=0.12,
        sharpe_proxy=1.4,
    )
    s = score_etf(d)
    assert s.asset_type == "ETF"
    assert s.total > 70
    assert "Returns" in s.breakdown


def test_weak_etf_scores_low():
    d = EtfData(
        ticker="BADETF",
        total_assets=50_000_000,
        expense_ratio=0.0095,
        three_year_return=-0.02,
        five_year_return=0.00,
        return_12m=-0.15,
        volatility_1y=0.45,
        sharpe_proxy=-0.3,
        yield_=0.0,
    )
    s = score_etf(d)
    assert s.total < 35


def test_score_asset_dispatches_to_etf():
    d = EtfData(ticker="VOO")
    s = score_asset(d)
    assert s.asset_type == "ETF"
