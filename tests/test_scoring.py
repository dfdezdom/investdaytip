"""Tests for the scoring engine (pure functions, no network)."""

from investdaytip.data_source import StockData
from investdaytip.scoring import score_stock


def test_strong_stock_scores_high():
    data = StockData(
        ticker="GOOD",
        name="GoodCo",
        trailing_pe=15,
        price_to_book=2.0,
        peg_ratio=1.0,
        return_on_equity=0.25,
        profit_margin=0.20,
        earnings_growth=0.15,
        revenue_growth=0.12,
        debt_to_equity=30,
        current_ratio=2.2,
        free_cashflow=1_000_000_000,
        market_cap=50_000_000_000,
        price_vs_sma200=0.10,
        return_12m=0.20,
        sma200_slope=0.08,
    )
    s = score_stock(data)
    assert s.asset_type == "STOCK"
    assert s.total > 70
    assert set(s.breakdown.keys()) == {"Quality", "Value", "Health", "Trend"}
    assert len(s.rationale) > 0


def test_weak_stock_scores_low():
    data = StockData(
        ticker="BAD",
        name="BadCo",
        trailing_pe=80,
        price_to_book=10,
        peg_ratio=4.0,
        return_on_equity=0.01,
        profit_margin=-0.05,
        earnings_growth=-0.20,
        revenue_growth=-0.10,
        debt_to_equity=300,
        current_ratio=0.5,
        free_cashflow=-500_000_000,
        price_vs_sma200=-0.30,
        return_12m=-0.40,
        sma200_slope=-0.20,
    )
    s = score_stock(data)
    assert s.total < 30


def test_missing_data_gives_neutral_score():
    s = score_stock(StockData(ticker="UNK"))
    assert 40 <= s.total <= 60
