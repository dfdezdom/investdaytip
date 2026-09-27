"""Tests for StockFit tier detection and capability gating — no network."""

from __future__ import annotations

import pytest

from investdaytip.data_source_stockfit import (
    CAPABILITIES,
    StockfitError,
    StockfitPlanError,
    detect_plan,
    plan_allows,
    stockfit_status,
)


@pytest.fixture(autouse=True)
def _reset_plan_cache(monkeypatch):
    monkeypatch.setattr("investdaytip.data_source_stockfit._plan_cache", None)


# ── detect_plan ──────────────────────────────────────────────────────────────


def test_detect_plan_without_key(monkeypatch):
    monkeypatch.delenv("STOCKFIT_API_KEY", raising=False)
    assert detect_plan() == "none"


def test_detect_plan_pro(mocker, monkeypatch):
    monkeypatch.setenv("STOCKFIT_API_KEY", "k")
    mocker.patch("investdaytip.data_source_stockfit._get", return_value=[])
    assert detect_plan() == "pro"


def test_detect_plan_stock(mocker, monkeypatch):
    monkeypatch.setenv("STOCKFIT_API_KEY", "k")

    def fake(path, params=None, **_kw):
        if path == "footnotes/concentration":
            raise StockfitPlanError("gated")
        return []  # economic-model answers → stock

    mocker.patch("investdaytip.data_source_stockfit._get", side_effect=fake)
    assert detect_plan() == "stock"


def test_detect_plan_starter(mocker, monkeypatch):
    monkeypatch.setenv("STOCKFIT_API_KEY", "k")

    def fake(path, params=None, **_kw):
        if path == "financials/scores":
            return []
        raise StockfitPlanError("gated")

    mocker.patch("investdaytip.data_source_stockfit._get", side_effect=fake)
    assert detect_plan() == "starter"


def test_detect_plan_free_when_all_gated(mocker, monkeypatch):
    monkeypatch.setenv("STOCKFIT_API_KEY", "k")
    mocker.patch(
        "investdaytip.data_source_stockfit._get",
        side_effect=StockfitPlanError("gated"),
    )
    assert detect_plan() == "free"


def test_detect_plan_unknown_on_network_error(mocker, monkeypatch):
    monkeypatch.setenv("STOCKFIT_API_KEY", "k")
    mocker.patch(
        "investdaytip.data_source_stockfit._get",
        side_effect=StockfitError("network down"),
    )
    assert detect_plan() == "unknown"


# ── plan_allows / stockfit_status ────────────────────────────────────────────


def test_plan_allows_matrix():
    assert plan_allows("free", "pit_statements")
    assert plan_allows("free", "fundamental_insights")
    assert not plan_allows("free", "deep_dive_summary")
    assert plan_allows("starter", "deep_dive_summary")
    assert not plan_allows("starter", "footnotes")
    assert plan_allows("stock", "deep_dive_summary")
    assert not plan_allows("stock", "footnotes")
    assert plan_allows("pro", "footnotes")
    assert plan_allows("none", "pit_statements")  # snapshot path needs no key
    assert not plan_allows("none", "fundamental_insights")  # needs any key
    assert not plan_allows("none", "deep_dive_summary")
    # an undetected plan never blocks a feature (degrade at the fetch)
    assert plan_allows("unknown", "footnotes")


def test_plan_allows_unknown_capability_raises():
    with pytest.raises(KeyError):
        plan_allows("pro", "nope")


def test_stockfit_status_matrix(mocker, monkeypatch):
    monkeypatch.setenv("STOCKFIT_API_KEY", "k")
    mocker.patch(
        "investdaytip.data_source_stockfit._get",
        side_effect=StockfitPlanError("gated"),
    )
    status = stockfit_status()
    assert status["key_present"] is True
    assert status["plan"] == "free"
    assert set(status["capabilities"]) == set(CAPABILITIES)
    assert status["capabilities"]["deep_dive_summary"] is False
