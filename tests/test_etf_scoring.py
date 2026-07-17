"""Tests for the ETF scoring engine."""

from investdaytip.data_source import EtfData
from investdaytip.scoring import score_asset, score_etf


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


def test_etf_quant_extreme_risk_caps_score():
    """Extreme volatility/beta → risk flagged as disqualifying → total capped at 50."""
    d = EtfData(
        ticker="WILD", name="Wild Vol ETF", category="Volatility",
        current_price=20.0, currency="USD",
        volatility_1y=0.80,  # extreme: 80% annual vol
        beta_3y=2.8,         # extreme beta
        expense_ratio=0.05,
        total_assets=1_000_000_000,
        three_year_return=0.05, five_year_return=0.03,
        return_1m=0.05, return_3m=0.05, return_6m=0.05, return_12m=0.05,
    )
    s = score_asset(d, model="quant")
    assert s.total <= 50.0
    assert "risk flagged as disqualifying" in str(s.rationale)


def test_etf_quant_extreme_cost_caps_score():
    """Extreme expense ratio → cost flagged as disqualifying → total capped at 50."""
    d = EtfData(
        ticker="COSTLY", name="Expensive ETF", category="Alternative",
        current_price=30.0, currency="USD",
        volatility_1y=0.15,
        beta_3y=1.0,
        expense_ratio=1.60,  # extreme: 1.60%
        total_assets=5_000_000_000,
        three_year_return=0.08, five_year_return=0.07,
        return_1m=0.02, return_3m=0.03, return_6m=0.05, return_12m=0.10,
    )
    s = score_asset(d, model="quant")
    assert s.total <= 50.0
    assert "cost flagged as disqualifying" in str(s.rationale)


def test_etf_quant_reasonable_etf_not_capped():
    """A reasonable ETF (low vol, low cost) is not capped and scores above 50."""
    d = EtfData(
        ticker="GOOD", name="Good ETF", category="Large Blend",
        current_price=100.0, currency="USD",
        volatility_1y=0.12,
        beta_3y=0.9,
        expense_ratio=0.03,
        total_assets=50_000_000_000,
        three_year_return=0.12, five_year_return=0.10,
        return_1m=0.02, return_3m=0.04, return_6m=0.08, return_12m=0.15,
    )
    s = score_asset(d, model="quant")
    assert s.total > 50.0
    assert "Disqualifying factor" not in str(s.rationale)
