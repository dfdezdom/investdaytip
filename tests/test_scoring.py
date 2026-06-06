"""Tests for the scoring engine (pure functions, no network)."""

from investdaytip.data_source import StockData
from investdaytip.scoring import score_stock


def _base_data() -> StockData:
    """Return a StockData instance with baseline values for comparison."""
    return StockData(
        ticker="TEST",
        name="TestCo",
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


def test_technical_indicators_boost_trend():
    """Strong technical signals (deeply oversold + strong MACD) boost total score."""
    base = _base_data()
    s_without = score_stock(base, include_technical=False)
    base.rsi_14 = 15.0   # deeply oversold → max RSI score
    base.macd_histogram = 0.08  # strongly positive → max MACD score
    s_with = score_stock(base, include_technical=True)
    # Strong technical signals should outweigh the reduced SMA/return/slope weights
    assert s_with.total > s_without.total


def test_rsi_floor_below_20():
    """RSI below 20 is floored to 20 so collapsing stocks are not rewarded."""
    base = _base_data()
    base.rsi_14 = 10.0  # would score 100 if un-floored
    base.macd_histogram = 0.0
    s_low = score_stock(base, include_technical=True)
    base.rsi_14 = 19.0  # just below floor
    s_floor = score_stock(base, include_technical=True)
    base.rsi_14 = 20.0  # exactly at floor
    s_exact = score_stock(base, include_technical=True)
    # Scores should be very close (10 and 19 both map to 20)
    assert abs(s_low.total - s_floor.total) < 1.0
    assert abs(s_floor.total - s_exact.total) < 1.0


def test_include_technical_false_preserves_original_scoring():
    """When disabled, the score must be identical to the pre-technical model."""
    base = _base_data()
    base.rsi_14 = 25.0
    base.macd_histogram = 0.02
    s_disabled = score_stock(base, include_technical=False)
    # Build an equivalent StockData without technical fields and score it
    equivalent = _base_data()
    s_original = score_stock(equivalent, include_technical=False)
    assert s_disabled.total == s_original.total
    assert s_disabled.breakdown == s_original.breakdown
