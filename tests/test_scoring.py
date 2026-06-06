"""Tests for the scoring engine (pure functions, no network)."""

from investdaytip.data_source import StockData
from investdaytip.scoring import STOCK_WEIGHTS, _adjust_stock_weights, score_stock


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


def test_roic_boosts_health_score():
    """High ROIC should improve the health sub-score."""
    base = StockData(
        ticker="BASE",
        debt_to_equity=30,
        current_ratio=2.2,
        free_cashflow=1_000_000_000,
    )
    with_roic = StockData(
        ticker="ROIC",
        debt_to_equity=30,
        current_ratio=2.2,
        free_cashflow=1_000_000_000,
        roic=0.25,
    )
    assert score_stock(with_roic).breakdown["Health"] > score_stock(base).breakdown["Health"]


def test_negative_roic_lowers_health():
    """Negative ROIC should pull the health sub-score below neutral."""
    data = StockData(
        ticker="NEG",
        debt_to_equity=110,   # neutral debt score
        current_ratio=1.75,   # neutral liquidity score
        roic=-0.05,
    )
    s = score_stock(data)
    assert s.breakdown["Health"] < 50


def test_adjust_weights_bullish():
    w = _adjust_stock_weights(STOCK_WEIGHTS, "bullish")
    assert w["trend"] > STOCK_WEIGHTS["trend"]
    assert w["health"] < STOCK_WEIGHTS["health"]


def test_adjust_weights_bearish():
    w = _adjust_stock_weights(STOCK_WEIGHTS, "bearish")
    assert w["health"] > STOCK_WEIGHTS["health"]
    assert w["trend"] < STOCK_WEIGHTS["trend"]


def test_adjust_weights_healthy_maps_to_bullish():
    """Macro regime 'healthy' should use bullish (risk-on) weights."""
    w = _adjust_stock_weights(STOCK_WEIGHTS, "healthy")
    assert w["trend"] > STOCK_WEIGHTS["trend"]
    assert w["health"] < STOCK_WEIGHTS["health"]


def test_adjust_weights_warning_maps_to_bearish():
    """Macro regime 'warning' should use bearish (defensive) weights."""
    w = _adjust_stock_weights(STOCK_WEIGHTS, "warning")
    assert w["health"] > STOCK_WEIGHTS["health"]
    assert w["trend"] < STOCK_WEIGHTS["trend"]


def test_adjust_weights_danger_maps_to_bearish():
    """Macro regime 'danger' should use bearish (defensive) weights."""
    w = _adjust_stock_weights(STOCK_WEIGHTS, "danger")
    assert w["health"] > STOCK_WEIGHTS["health"]
    assert w["trend"] < STOCK_WEIGHTS["trend"]


def test_adjust_weights_unknown_returns_default():
    w = _adjust_stock_weights(STOCK_WEIGHTS, "something weird")
    assert w == STOCK_WEIGHTS


def test_dynamic_weights_changes_total():
    """Dynamic weights should produce a different total score."""
    data = StockData(
        ticker="DW",
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
    default = score_stock(data)
    bullish = score_stock(data, dynamic_weights=True, regime="bullish")
    bearish = score_stock(data, dynamic_weights=True, regime="bearish")
    assert bullish.total != default.total
    assert bearish.total != default.total
