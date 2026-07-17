"""Tests for the optional ``quant`` scoring model."""

import pytest

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


def test_quant_eps_revisions_high_surprise():
    base = _base_data()
    base.eps_surprise = 15.0
    s = score_stock(base, model="quant")
    assert s.breakdown["EPS Revisions"] == pytest.approx(100.0)
    assert any("beat estimates" in note for note in s.rationale)


def test_quant_eps_revisions_low_surprise():
    base = _base_data()
    base.eps_surprise = -15.0
    s = score_stock(base, model="quant")
    assert s.breakdown["EPS Revisions"] == pytest.approx(0.0)
    assert s.total <= 50.0  # disqualified
    assert any("missed estimates" in note for note in s.rationale)


def test_quant_eps_revisions_missing_is_neutral():
    base = _base_data()
    base.eps_surprise = None
    s = score_stock(base, model="quant")
    assert s.breakdown["EPS Revisions"] == pytest.approx(50.0)


def test_quant_eps_revisions_does_not_use_growth():
    """When eps_surprise is present, Growth and EPS Revisions differ."""
    base = _base_data()
    base.eps_surprise = 15.0
    base.earnings_growth = -0.20
    base.revenue_growth = -0.10
    s = score_stock(base, model="quant")
    assert s.breakdown["EPS Revisions"] > 90.0
    assert s.breakdown["Growth"] < 20.0


def test_quant_negative_peg_gets_neutral():
    """Negative PEG in quant model → neutral value PEG sub-score."""
    d = StockData(
        ticker="NEGPEG", currency="USD",
        trailing_pe=20.0, price_to_book=3.0, peg_ratio=-1.5,
        return_on_equity=0.15, profit_margin=0.15,
        return_on_assets=0.08, earnings_growth=0.10, revenue_growth=0.10,
        market_cap=10_000_000_000,
        price_vs_sma200=0.10,
        return_12m=0.20, sma200_slope=0.08,
    )
    s = score_stock(d, model="quant")
    assert s.total < 90  # PEG negative → neutral, not perfect 100
    assert s.breakdown["Value"] < 100


def test_quant_negative_pe_pb_give_neutral():
    """Negative P/E or P/B in quant model → neutral value sub-scores."""
    d = StockData(
        ticker="NEG", currency="USD",
        trailing_pe=-10.0, price_to_book=-0.5, peg_ratio=1.0,
        return_on_equity=0.15, profit_margin=0.15,
        return_on_assets=0.08, earnings_growth=0.10, revenue_growth=0.10,
        market_cap=10_000_000_000,
        price_vs_sma200=0.10,
        return_12m=0.20, sma200_slope=0.08,
    )
    s = score_stock(d, model="quant")
    assert s.total < 90
