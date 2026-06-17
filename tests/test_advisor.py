"""Tests for the advisor module — VIX regime, bubble risk, portfolio review.

All network access is mocked; ``_fetch_index`` / ``yf.Ticker`` / ``recommend``
are patched so no live calls occur.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from investdaytip import advisor
from investdaytip.data_source import StockData
from investdaytip.scoring import ScoredAsset, score_stock


class TestMarketRegime:
    @pytest.mark.parametrize(
        "vix, regime, action",
        [
            (12.0, "bullish", "buy"),
            (20.0, "neutral", "buy"),
            (30.0, "bearish", "hold"),
            (45.0, "crash", "sell"),
        ],
    )
    def test_thresholds(self, mocker, vix, regime, action):
        mocker.patch(
            "investdaytip.advisor._fetch_index",
            side_effect=lambda t: vix if t == "^VIX" else 18.0,
        )
        r = advisor.market_regime()
        assert r["regime"] == regime
        assert r["action"] == action
        assert r["vix"] == vix

    def test_boundary_values(self, mocker):
        # Exactly at a threshold uses the next (lower-fear) bucket (> comparisons).
        mocker.patch(
            "investdaytip.advisor._fetch_index",
            side_effect=lambda t: 15.0 if t == "^VIX" else None,
        )
        assert advisor.market_regime()["regime"] == "bullish"

    def test_unknown_when_vix_missing(self, mocker):
        mocker.patch("investdaytip.advisor._fetch_index", return_value=None)
        r = advisor.market_regime()
        assert r["regime"] == "unknown"
        assert r["action"] == "hold"
        assert r["vix"] is None


class TestFetchIndex:
    def test_returns_last_close(self, mocker):
        df = pd.DataFrame({"Close": [10.0, 11.0, 12.5]})
        tick = mocker.Mock()
        tick.history.return_value = df
        mocker.patch("investdaytip.advisor.yf.Ticker", return_value=tick)
        assert advisor._fetch_index("^VIX") == 12.5

    def test_nan_close_returns_none(self, mocker):
        # A NaN last close must NOT produce a spurious finite value.
        df = pd.DataFrame({"Close": [10.0, 11.0, float("nan")]})
        tick = mocker.Mock()
        tick.history.return_value = df
        mocker.patch("investdaytip.advisor.yf.Ticker", return_value=tick)
        assert advisor._fetch_index("^VIX") is None

    def test_empty_history_returns_none(self, mocker):
        tick = mocker.Mock()
        tick.history.return_value = pd.DataFrame()
        mocker.patch("investdaytip.advisor.yf.Ticker", return_value=tick)
        assert advisor._fetch_index("^VIX") is None

    def test_nan_vix_does_not_trigger_buy(self, mocker):
        # Regression: NaN VIX previously fell through to the bullish/buy branch.
        df = pd.DataFrame({"Close": [float("nan")]})
        tick = mocker.Mock()
        tick.history.return_value = df
        mocker.patch("investdaytip.advisor.yf.Ticker", return_value=tick)
        r = advisor.market_regime()
        assert r["regime"] == "unknown"
        assert r["action"] == "hold"


class TestMacroRegime:
    def test_healthy_all_good(self, mocker):
        """All signals favorable -> healthy macro."""
        def _fetch(ticker):
            vals = {
                "^VIX": 12.0,
                "^VXN": 15.0,
                "^TNX": 5.50,
                "2YY=F": 3.50,
                "^MOVE": 50.0,
                "DX-Y.NYB": 92.0,
            }
            return vals.get(ticker)
        mocker.patch("investdaytip.advisor._fetch_index", side_effect=_fetch)
        r = advisor.macro_regime()
        assert r["regime"] == "healthy"
        assert r["score"] >= 70

    def test_danger_inverted_curve(self, mocker):
        """Inverted yield curve + high VIX + high MOVE -> danger."""
        def _fetch(ticker):
            vals = {
                "^VIX": 40.0,
                "^VXN": 45.0,
                "^TNX": 3.50,
                "2YY=F": 4.00,
                "^MOVE": 130.0,
                "DX-Y.NYB": 108.0,
            }
            return vals.get(ticker)
        mocker.patch("investdaytip.advisor._fetch_index", side_effect=_fetch)
        r = advisor.macro_regime()
        assert r["regime"] == "danger"
        assert r["score"] < 25

    def test_warning_flat_curve(self, mocker):
        """Flat curve + elevated MOVE -> warning."""
        def _fetch(ticker):
            vals = {
                "^VIX": 30.0,
                "^VXN": 32.0,
                "^TNX": 4.80,
                "2YY=F": 3.90,
                "^MOVE": 105.0,
                "DX-Y.NYB": 102.0,
            }
            return vals.get(ticker)
        mocker.patch("investdaytip.advisor._fetch_index", side_effect=_fetch)
        r = advisor.macro_regime()
        assert r["regime"] == "warning"
        assert 25 <= r["score"] < 45

    def test_missing_data_neutral(self, mocker):
        """Missing macro data defaults to neutral score (~50)."""
        mocker.patch("investdaytip.advisor._fetch_index", return_value=None)
        r = advisor.macro_regime()
        assert r["regime"] == "neutral"
        assert 45 <= r["score"] <= 55


class TestBubbleRisk:
    def _patch_vix_history(self, mocker, closes):
        df = pd.DataFrame({"Close": closes})
        tick = mocker.Mock()
        tick.history.return_value = df
        mocker.patch("investdaytip.advisor.yf.Ticker", return_value=tick)

    def test_low_percentile_is_high_risk(self, mocker):
        # Current value below most of the 2y range -> complacency / bubble risk.
        closes = list(np.linspace(40, 12, 500))  # ends near the bottom
        self._patch_vix_history(mocker, closes)
        r = advisor.bubble_risk()
        assert r["level"] == "high"
        assert r["pct_rank"] < 15

    def test_normal_range_is_low_risk(self, mocker):
        closes = list(np.linspace(10, 30, 250)) + list(np.linspace(30, 18, 250))
        self._patch_vix_history(mocker, closes)
        r = advisor.bubble_risk()
        assert r["level"] in {"low", "medium"}

    def test_insufficient_data(self, mocker):
        self._patch_vix_history(mocker, [15.0, 16.0, 17.0])
        r = advisor.bubble_risk()
        assert r["level"] == "unknown"
        assert r["pct_rank"] is None

    def test_no_history(self, mocker):
        tick = mocker.Mock()
        tick.history.return_value = pd.DataFrame()
        mocker.patch("investdaytip.advisor.yf.Ticker", return_value=tick)
        r = advisor.bubble_risk()
        assert r["level"] == "unknown"


class TestPortfolioReview:
    def test_missing_file(self, tmp_path):
        r = advisor.portfolio_review(str(tmp_path / "nope.txt"))
        assert "error" in r

    def test_empty_file(self, tmp_path):
        p = tmp_path / "empty.txt"
        p.write_text("# only comments\n")
        r = advisor.portfolio_review(str(p))
        assert "error" in r

    def test_categorizes_positions(self, tmp_path, mocker):
        p = tmp_path / "cartera.txt"
        p.write_text("AAA\nBBB\nCCC\n")

        def _scored(ticker, total, sector):
            data = StockData(ticker=ticker, sector=sector)
            s = score_stock(data)
            s.total = total  # force a deterministic bucket
            return s

        mocker.patch(
            "investdaytip.advisor.recommend",
            return_value=[
                _scored("AAA", 25.0, "Tech"),
                _scored("BBB", 50.0, "Health"),
                _scored("CCC", 75.0, "Tech"),
            ],
        )
        r = advisor.portfolio_review(str(p))
        assert r["count"] == 3
        assert [s.data.ticker for s in r["weak_positions"]] == ["AAA"]
        assert [s.data.ticker for s in r["moderate_positions"]] == ["BBB"]
        assert [s.data.ticker for s in r["strong_positions"]] == ["CCC"]
        assert r["sectors"] == ["Health", "Tech"]


class TestAdvisorMain:
    """Full CLI flow: ``advisor_main()`` with mocked market data + portfolio."""

    def test_runs_end_to_end_with_mocked_data(self, mocker, tmp_path):
        p = tmp_path / "portfolio.txt"
        p.write_text("AAPL\nMSFT\n")

        mocker.patch("investdaytip.advisor.macro_regime", return_value={
            "vix": {"vix": 15.0, "vxn": 18.0, "regime": "bullish"},
            "yield": {"y10": 4.5, "y2": 4.0, "spread": 0.5},
            "move": 80.0, "dxy": 100.0,
            "fear_greed": {"score": 50.0, "rating": "neutral"},
            "regime": "healthy", "label": "Healthy", "score": 75,
            "action": "buy", "description": "Favorable conditions.",
        })
        mocker.patch("investdaytip.advisor.bubble_risk", return_value={
            "level": "low", "pct_rank": 45.0, "note": "Normal volatility.",
        })

        def _scored(ticker, total, sector):
            data = StockData(ticker=ticker, sector=sector)
            s = score_stock(data)
            s.total = total
            return s

        port_mock = mocker.patch("investdaytip.advisor.portfolio_review", return_value={
            "count": 2,
            "results": [
                _scored("AAPL", 75.0, "Technology"),
                _scored("MSFT", 65.0, "Technology"),
            ],
            "weak_positions": [],
            "moderate_positions": [],
            "strong_positions": [_scored("AAPL", 75.0, "Technology")],
            "sectors": ["Technology"],
        })

        rec_mock = mocker.patch("investdaytip.advisor.recommend", return_value=[
            ScoredAsset(
                data=StockData(ticker="NVDA", sector="Technology", currency="USD"),
                asset_type="STOCK", total=80.0,
                breakdown={"Q": 80, "V": 60, "H": 70, "T": 90},
                rationale=["Strong momentum"],
            )
        ])

        html_mock = mocker.patch(
            "investdaytip.advisor.export_recommendations_html",
            return_value=str(tmp_path / "report.html"),
        )
        mocker.patch("investdaytip.advisor.Path.mkdir")

        rc = advisor.advisor_main([
            "--portfolio", str(p),
            "--risk", "moderate",
            "-a", "stocks",
            "-r", "us",
            "-c", "USD",
        ])

        assert rc == 0
        port_mock.assert_called_once_with(str(p), 2_000_000_000, "quant")
        rec_mock.assert_called_once_with(
            asset_class="stocks", region=["us"], top_n=10,
            currency=["USD"], min_market_cap=2_000_000_000,
            sector=None, scoring_model="quant",
        )
        html_mock.assert_called_once()
