"""Tests for the optional ``quant`` scoring model."""

from investdaytip.data_source import StockData
from investdaytip.scoring import (
    QuantStockScorer,
    ScoredAsset,
    score_asset,
    score_stock,
)


def _base_data() -> StockData:
    """Return a StockData instance with healthy baseline values."""
    return StockData(
        ticker="TEST",
        name="TestCo",
        trailing_pe=15,
        price_to_book=2.0,
        peg_ratio=1.0,
        return_on_equity=0.25,
        return_on_assets=0.12,
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


def test_quant_model_returns_scored_asset():
    s = score_stock(_base_data(), model="quant")
    assert isinstance(s, ScoredAsset)
    assert s.asset_type == "STOCK"
    assert 0 <= s.total <= 100


def test_quant_model_has_five_breakdown_factors():
    s = score_stock(_base_data(), model="quant")
    assert set(s.breakdown.keys()) == {
        "Value",
        "Growth",
        "Profitability",
        "Momentum",
        "EPS Revisions",
    }
    for v in s.breakdown.values():
        assert 0 <= v <= 100


def test_quant_strong_stock_scores_high():
    s = score_stock(_base_data(), model="quant")
    assert s.total > 70
    assert len(s.rationale) > 0


def test_quant_weak_stock_is_capped_by_disqualifying_grades():
    """A stock with catastrophic factors should be capped at neutral."""
    data = StockData(
        ticker="BAD",
        name="BadCo",
        trailing_pe=80,
        price_to_book=10,
        peg_ratio=4.0,
        return_on_equity=0.01,
        return_on_assets=0.005,
        profit_margin=-0.05,
        earnings_growth=-0.20,
        revenue_growth=-0.10,
        debt_to_equity=300,
        current_ratio=0.5,
        free_cashflow=-500_000_000,
        market_cap=1_000_000_000,
        price_vs_sma200=-0.30,
        return_12m=-0.40,
        sma200_slope=-0.20,
    )
    s = score_stock(data, model="quant")
    assert s.total <= 50.0
    assert any("Disqualifying" in note for note in s.rationale)


def test_quant_missing_data_gives_neutral_score():
    s = score_stock(StockData(ticker="UNK"), model="quant")
    assert 40 <= s.total <= 60


def test_quant_include_technical_changes_momentum():
    base = _base_data()
    s_without = score_stock(base, model="quant", include_technical=False)
    base.rsi_14 = 25.0
    base.macd_histogram = 0.02
    s_with = score_stock(base, model="quant", include_technical=True)
    # Technical blending alters the Momentum factor value in the breakdown.
    assert s_with.breakdown["Momentum"] != s_without.breakdown["Momentum"]


def test_quant_is_default_model():
    """Calling score_stock without an explicit model uses the quant model."""
    base = _base_data()
    s_default = score_stock(base)
    s_quant = score_stock(base, model="quant")
    assert s_default.total == s_quant.total
    assert s_default.breakdown == s_quant.breakdown
    assert set(s_quant.breakdown.keys()) == {
        "Value", "Growth", "Profitability", "Momentum", "EPS Revisions"
    }


def test_classic_model_still_works():
    """The classic scorer remains available and behaves as before."""
    base = _base_data()
    s_classic = score_stock(base, model="classic")
    assert set(s_classic.breakdown.keys()) == {"Quality", "Value", "Health", "Trend"}


def test_score_asset_dispatches_by_model():
    base = _base_data()
    s_classic = score_asset(base, model="classic")
    s_quant = score_asset(base, model="quant")
    assert set(s_classic.breakdown.keys()) == {"Quality", "Value", "Health", "Trend"}
    assert set(s_quant.breakdown.keys()) == {
        "Value", "Growth", "Profitability", "Momentum", "EPS Revisions"
    }


def test_quant_scorer_directly():
    scorer = QuantStockScorer()
    s = scorer.score(_base_data())
    assert isinstance(s, ScoredAsset)
    assert s.total > 70
