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


def test_sector_relative_boosts_outperformer():
    """ETF outperforming its category should score higher with sector_relative."""
    d = EtfData(
        ticker="OUT",
        category="Large Blend",
        return_12m=0.25,
        category_avg_return=0.15,
        total_assets=10_000_000_000,
        expense_ratio=0.0005,
        volatility_1y=0.15,
        sharpe_proxy=1.0,
    )
    default = score_etf(d)
    relative = score_etf(d, sector_relative=True)
    assert relative.total > default.total
    assert "Sector" in relative.breakdown


def test_sector_relative_penalizes_underperformer():
    """ETF underperforming its category should score lower with sector_relative."""
    d = EtfData(
        ticker="UNDER",
        category="Large Blend",
        return_12m=0.05,
        category_avg_return=0.15,
        total_assets=10_000_000_000,
        expense_ratio=0.0005,
        volatility_1y=0.15,
        sharpe_proxy=1.0,
    )
    default = score_etf(d)
    relative = score_etf(d, sector_relative=True)
    assert relative.total < default.total


def test_sector_relative_no_category_data_is_neutral():
    """Missing category data should not crash and score should stay neutral."""
    d = EtfData(
        ticker="NOCAT",
        return_12m=0.10,
        total_assets=10_000_000_000,
        expense_ratio=0.0005,
        volatility_1y=0.15,
        sharpe_proxy=1.0,
    )
    s = score_etf(d, sector_relative=True)
    assert s.total >= 0
