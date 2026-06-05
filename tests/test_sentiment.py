"""Tests for the sentiment module — CNN Fear & Greed Index.

All network access is mocked via urllib.request.urlopen.
"""
from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import MagicMock

from investdaytip.sentiment import fear_greed_index

_SAMPLE_PAYLOAD = {
    "fear_and_greed": {
        "score": 42.6,
        "rating": "fear",
        "timestamp": 1780688351000,
    },
    "fear_and_greed_historical": {"score": 42.6, "rating": "fear"},
    "market_momentum_sp125": {"score": 59.0, "rating": "greed"},
    "stock_price_strength": {"score": 31.2, "rating": "fear"},
    "stock_price_breadth": {"score": 26.8, "rating": "fear"},
    "put_call_options": {"score": 76.8, "rating": "extreme greed"},
    "market_volatility_vix_50": {"score": 50.0, "rating": "neutral"},
    "junk_bond_demand": {"score": 7.2, "rating": "extreme fear"},
    "safe_haven_demand": {"score": 47.4, "rating": "neutral"},
}


def _mock_urlopen(data: dict) -> MagicMock:
    """Return a mock that acts like a successful urlopen response."""
    raw = json.dumps(data).encode("utf-8")
    cm = MagicMock()
    cm.__enter__.return_value = BytesIO(raw)
    cm.__exit__.return_value = None
    return cm


class TestFearGreedIndex:
    def test_successful_fetch(self, monkeypatch):
        monkeypatch.setattr(
            "investdaytip.sentiment.urlopen",
            lambda *a, **kw: _mock_urlopen(_SAMPLE_PAYLOAD),
        )
        result = fear_greed_index()
        assert result is not None
        assert result["score"] == 42.6
        assert result["rating"] == "fear"
        assert result["timestamp"] == 1780688351000

    def test_sub_indicators(self, monkeypatch):
        monkeypatch.setattr(
            "investdaytip.sentiment.urlopen",
            lambda *a, **kw: _mock_urlopen(_SAMPLE_PAYLOAD),
        )
        result = fear_greed_index()
        assert result is not None
        subs = result["sub_indicators"]
        assert subs["market_momentum"]["score"] == 59.0
        assert subs["market_momentum"]["rating"] == "greed"
        assert subs["stock_price_strength"]["score"] == 31.2
        assert subs["stock_price_breadth"]["rating"] == "fear"
        assert subs["put_call_options"]["score"] == 76.8
        assert subs["put_call_options"]["rating"] == "extreme greed"
        assert subs["market_volatility"]["score"] == 50.0
        assert subs["market_volatility"]["rating"] == "neutral"
        assert subs["junk_bond_demand"]["score"] == 7.2
        assert subs["junk_bond_demand"]["rating"] == "extreme fear"
        assert subs["safe_haven_demand"]["score"] == 47.4
        assert subs["safe_haven_demand"]["rating"] == "neutral"

    def test_network_error_returns_none(self, monkeypatch):
        def _raise(*a, **kw):
            raise OSError("Connection refused")

        monkeypatch.setattr("investdaytip.sentiment.urlopen", _raise)
        assert fear_greed_index() is None

    def test_bad_json_returns_none(self, monkeypatch):
        cm = MagicMock()
        cm.__enter__.return_value = BytesIO(b"not json")
        cm.__exit__.return_value = None
        monkeypatch.setattr(
            "investdaytip.sentiment.urlopen",
            lambda *a, **kw: cm,
        )
        assert fear_greed_index() is None

    def test_missing_fear_and_greed_key(self, monkeypatch):
        monkeypatch.setattr(
            "investdaytip.sentiment.urlopen",
            lambda *a, **kw: _mock_urlopen({}),
        )
        result = fear_greed_index()
        assert result is not None
        assert result["score"] is None
        assert result["rating"] is None

    def test_non_finite_score_returns_none(self, monkeypatch):
        payload = {"fear_and_greed": {"score": float("nan"), "rating": "fear"}}
        monkeypatch.setattr(
            "investdaytip.sentiment.urlopen",
            lambda *a, **kw: _mock_urlopen(payload),
        )
        result = fear_greed_index()
        assert result is not None
        assert result["score"] is None
        assert result["rating"] == "fear"

    def test_extreme_greed_rating(self, monkeypatch):
        payload = {
            "fear_and_greed": {
                "score": 82.0,
                "rating": "extreme greed",
                "timestamp": 1000,
            },
        }
        monkeypatch.setattr(
            "investdaytip.sentiment.urlopen",
            lambda *a, **kw: _mock_urlopen(payload),
        )
        result = fear_greed_index()
        assert result is not None
        assert result["score"] == 82.0
        assert result["rating"] == "extreme greed"
