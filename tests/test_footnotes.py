"""Tests for the StockFit footnotes layer — fetch gating and defensive parsing.

No network: ``_get`` is mocked (same pattern as the FMP / insights tests).
"""
from __future__ import annotations

import json

from investdaytip.data_source_stockfit import (
    _FOOTNOTE_ENDPOINTS,
    StockfitError,
    StockfitPlanError,
    fetch_footnotes,
)
from investdaytip.risk_signals import footnote_risk_signals

# ── fetch_footnotes ──────────────────────────────────────────────────────────


def _mock_get(mocker, payloads=None, fail=None, calls=None):
    payloads = payloads or {}
    fail = fail or set()
    calls = calls if calls is not None else []

    def fake(path, params=None, **_kw):
        calls.append((path, params or {}))
        if path in fail:
            raise StockfitError("boom")
        return payloads.get(path, [])

    mocker.patch("investdaytip.data_source_stockfit._get", side_effect=fake)
    return calls


def _paths(calls):
    return {path for path, _ in calls}


def test_fetch_footnotes_pro_plan_hits_all_endpoints(mocker, monkeypatch):
    monkeypatch.setenv("STOCKFIT_API_KEY", "k")
    mocker.patch("investdaytip.data_source_stockfit.detect_plan", return_value="pro")
    calls = _mock_get(mocker)
    result = fetch_footnotes("aapl")
    assert set(result) == {path.split("/", 1)[1] for path, _ in _FOOTNOTE_ENDPOINTS}
    assert _paths(calls) == {path for path, _ in _FOOTNOTE_ENDPOINTS}
    # every call carries the annual / 3-period params and the canonical symbol
    assert all(params == {"symbol": "AAPL", "period": "annual", "limit": "3"}
               for _, params in calls)


def test_fetch_footnotes_starter_plan_hits_segments_only(mocker, monkeypatch):
    monkeypatch.setenv("STOCKFIT_API_KEY", "k")
    mocker.patch("investdaytip.data_source_stockfit.detect_plan", return_value="starter")
    calls = _mock_get(mocker)
    result = fetch_footnotes("aapl")
    assert set(result) == {"revenue-segmentation", "business-segmentation"}
    assert _paths(calls) == {
        "footnotes/revenue-segmentation", "footnotes/business-segmentation",
    }


def test_fetch_footnotes_plan_gate_skips_pro_endpoints(mocker, monkeypatch):
    monkeypatch.setenv("STOCKFIT_API_KEY", "k")
    mocker.patch("investdaytip.data_source_stockfit.detect_plan", return_value="free")
    calls = _mock_get(mocker)
    assert fetch_footnotes("aapl") == {}
    assert calls == []  # nothing unlocked → no request at all


def test_fetch_footnotes_caches_complete_fetch(mocker, monkeypatch, enabled_temp_cache):
    monkeypatch.setenv("STOCKFIT_API_KEY", "k")
    mocker.patch("investdaytip.data_source_stockfit.detect_plan", return_value="starter")
    calls = _mock_get(mocker, payloads={"footnotes/revenue-segmentation": [{"fiscalYear": 2024}]})

    first = fetch_footnotes("aapl")
    second = fetch_footnotes("aapl")
    assert first == second
    assert len(calls) == 2  # cached — no refetch


def test_fetch_footnotes_partial_failure_not_cached(mocker, monkeypatch, enabled_temp_cache):
    monkeypatch.setenv("STOCKFIT_API_KEY", "k")
    mocker.patch("investdaytip.data_source_stockfit.detect_plan", return_value="starter")
    calls = _mock_get(mocker, fail={"footnotes/business-segmentation"})

    first = fetch_footnotes("aapl")
    assert set(first) == {"revenue-segmentation"}  # partial result, never fabricated
    calls.clear()
    fetch_footnotes("aapl")  # not cached → refetched
    assert len(calls) == 2


def test_fetch_footnotes_never_raises_on_total_failure(mocker, monkeypatch):
    monkeypatch.setenv("STOCKFIT_API_KEY", "k")
    mocker.patch("investdaytip.data_source_stockfit.detect_plan", return_value="pro")
    _mock_get(mocker, fail={path for path, _ in _FOOTNOTE_ENDPOINTS})
    assert fetch_footnotes("aapl") == {}


def test_fetch_footnotes_plan_error_skipped_gracefully(mocker, monkeypatch):
    monkeypatch.setenv("STOCKFIT_API_KEY", "k")
    mocker.patch("investdaytip.data_source_stockfit.detect_plan", return_value="pro")

    def fake(path, params=None, **_kw):
        if path == "footnotes/concentration":
            raise StockfitPlanError("gated")  # plan detection raced a downgrade
        return []

    mocker.patch("investdaytip.data_source_stockfit._get", side_effect=fake)
    result = fetch_footnotes("aapl")
    assert "concentration" not in result
    assert len(result) == len(_FOOTNOTE_ENDPOINTS) - 1


def test_fetch_footnotes_corrupt_cache_refetches(mocker, monkeypatch, enabled_temp_cache):
    monkeypatch.setenv("STOCKFIT_API_KEY", "k")
    mocker.patch("investdaytip.data_source_stockfit.detect_plan", return_value="starter")
    from investdaytip.cache import cache_stockfit_footnotes_set

    cache_stockfit_footnotes_set("AAPL", "not json {")
    calls = _mock_get(mocker)
    result = fetch_footnotes("aapl")
    assert len(calls) == 2
    assert set(result) == {"revenue-segmentation", "business-segmentation"}


# ── footnote_risk_signals: parsing ───────────────────────────────────────────


def test_footnote_risk_signals_empty():
    assert footnote_risk_signals(None) == []
    assert footnote_risk_signals({}) == []


def test_concentration_customer_alert_and_trend():
    footnotes = {"concentration": {"series": [
        {"riskType": "customer", "benchmark": "revenue", "counterparties": [
            {"name": "Apple", "history": [
                {"period": "2022-12-31", "share": 0.10},
                {"period": "2024-12-31", "share": 0.35},
            ]},
        ]},
    ]}}
    sigs = footnote_risk_signals(footnotes)
    assert len(sigs) == 1
    assert sigs[0].severity == "medium"
    assert sigs[0].label == "Key-customer concentration"
    assert "Apple" in sigs[0].detail and "35%" in sigs[0].detail
    assert "10% → 35%" in sigs[0].detail  # rising concentration is part of the bear case
    assert sigs[0].source == "stockfit"


def test_concentration_customer_watch_is_info():
    footnotes = {"concentration": {"series": [
        {"riskType": "customer", "benchmark": "revenue", "counterparties": [
            {"name": "Acme", "history": [{"period": "2024-12-31", "share": 0.20}]},
        ]},
    ]}}
    sigs = footnote_risk_signals(footnotes)
    assert [(s.severity, s.label) for s in sigs] == [("info", "Key-customer concentration")]


def test_concentration_supplier_and_unknown_types():
    footnotes = {"concentration": {"series": [
        {"riskType": "supplier", "counterparties": [
            {"name": "TSMC", "history": [{"period": "2024-12-31", "share": 0.40}]},
        ]},
        {"riskType": "geographic", "counterparties": [
            {"name": "Nowhere", "history": [{"period": "2024-12-31", "share": 0.90}]},
        ]},
    ]}}
    sigs = footnote_risk_signals(footnotes)
    assert [(s.severity, s.label) for s in sigs] == [("info", "Supplier concentration")]
    assert "TSMC" in sigs[0].detail


def test_concentration_unrecognized_shape_yields_silence():
    assert footnote_risk_signals({"concentration": {"unexpected": [{"foo": 1}]}}) == []
    assert footnote_risk_signals({"concentration": [{"alien": True}]}) == []


def test_debt_maturity_wall():
    footnotes = {"debt-structure": [{"fiscalYear": 2024, "instruments": [
        {"name": "Notes 26", "dueYear": 2026, "faceAmount": 500e6},
        {"name": "Notes 30", "dueYear": 2030, "faceAmount": 400e6},
        {"name": "Notes 35", "dueYear": 2035, "faceAmount": 100e6},
    ]}]}
    sigs = footnote_risk_signals(footnotes)
    assert [(s.severity, s.label) for s in sigs] == [("medium", "Debt maturity wall")]
    assert "$500M" in sigs[0].detail and "50%" in sigs[0].detail


def test_debt_maturity_wall_below_threshold_silent():
    footnotes = {"debt-structure": [{"fiscalYear": 2024, "instruments": [
        {"dueYear": 2026, "faceAmount": 100e6},
        {"dueYear": 2035, "faceAmount": 900e6},
    ]}]}
    assert footnote_risk_signals(footnotes) == []


def test_credit_facility_utilization():
    footnotes = {"credit-facilities": [{"fiscalYear": 2024, "facilities": [
        {"member": "Revolver", "outstanding": 750e6, "maxCapacity": 1e9,
         "utilization": 0.75},
    ]}]}
    sigs = footnote_risk_signals(footnotes)
    assert [(s.severity, s.label) for s in sigs] == [
        ("medium", "Credit facilities heavily drawn"),
    ]
    # watch band
    footnotes["credit-facilities"][0]["facilities"][0]["utilization"] = 0.60
    assert [s.severity for s in footnote_risk_signals(footnotes)] == ["info"]
    # calm
    footnotes["credit-facilities"][0]["facilities"][0]["utilization"] = 0.30
    assert footnote_risk_signals(footnotes) == []


def test_stock_comp_materiality_vs_market_cap():
    footnotes = {"stock-compensation": [{"fiscalYear": 2024, "planTotals": [
        {"unrecognizedCompensationCost": 20e9},
    ]}]}
    assert footnote_risk_signals(footnotes, market_cap=100e9)[0].severity == "medium"
    assert footnote_risk_signals(footnotes, market_cap=300e9)[0].severity == "info"
    assert footnote_risk_signals(footnotes, market_cap=10e12) == []  # immaterial


def test_pension_underfunding():
    footnotes = {"retirement-plans": [{"fiscalYear": 2024, "plans": [
        {"type": "pension", "fundedStatus": -8e9},
        {"type": "opeb", "fundedStatus": -2e9},
        {"type": "supplemental", "fundedStatus": 1e9},
    ]}]}
    sigs = footnote_risk_signals(footnotes, market_cap=100e9)
    assert [(s.severity, s.label) for s in sigs] == [("medium", "Underfunded pension plans")]
    assert "$10.0B" in sigs[0].detail
    # fully funded → silence
    footnotes["retirement-plans"][0]["plans"] = [{"type": "pension", "fundedStatus": 2e9}]
    assert footnote_risk_signals(footnotes, market_cap=100e9) == []


def test_supplier_finance_hidden_leverage():
    footnotes = {"supplier-finance": [{"fiscalYear": 2024, "totals": {
        "outstandingBalance": 3e9, "currentPortion": 1e9,
    }}]}
    sigs = footnote_risk_signals(footnotes, market_cap=100e9)
    assert [(s.severity, s.label) for s in sigs] == [("info", "Supplier-finance obligations")]
    assert "$3.0B" in sigs[0].detail
    # totals and programs overlap — never summed (docs: "never sum them together")
    footnotes["supplier-finance"][0]["programs"] = [
        {"name": "P1", "outstandingBalance": 2e9},
    ]
    detail = footnote_risk_signals(footnotes, market_cap=100e9)[0].detail
    assert "$3.0B" in detail and "$5.0B" not in detail


def test_level3_share():
    footnotes = {"fair-value-hierarchy": [{"fiscalYear": 2024, "measures": [
        {"measure": "assets", "level3Share": 0.40},
        {"measure": "investments", "level3Share": 0.10},
    ]}]}
    sigs = footnote_risk_signals(footnotes)
    assert [(s.severity, s.label) for s in sigs] == [("info", "Opaque Level 3 valuations")]
    assert "40%" in sigs[0].detail
    footnotes["fair-value-hierarchy"][0]["measures"][0]["level3Share"] = 0.15
    assert footnote_risk_signals(footnotes) == []


def test_revenue_segmentation_concentration():
    footnotes = {"revenue-segmentation": [{"fiscalYear": 2024,
        "geography": {
            "countries": [
                {"code": "US", "name": "United States", "continent": "North America",
                 "value": 65e9},
                {"code": "CN", "name": "China", "continent": "Asia", "value": 35e9},
            ],
            "usStates": [], "regions": [], "residuals": [],
        },
        "product": [
            {"member": "iPhone", "name": "iPhone", "value": 55e9},
            {"member": "Services", "name": "Services", "value": 45e9},
        ]}]}
    sigs = footnote_risk_signals(footnotes)
    assert {(s.severity, s.label) for s in sigs} == {
        ("info", "Geographic revenue concentration"),
        ("info", "Product revenue concentration"),
    }
    details = " ".join(s.detail for s in sigs)
    assert "65%" in details and "United States" in details
    assert "55%" in details and "iPhone" in details


def test_business_segmentation_concentration():
    footnotes = {"business-segmentation": [{"fiscalYear": 2024, "unit": "USD",
        "segments": [
            {"member": "a", "name": "Cloud", "role": "segment",
             "metrics": {"revenue": 70e9, "operatingIncome": 20e9}},
            {"member": "b", "name": "Hardware", "role": "segment",
             "metrics": {"revenue": 30e9}},
            {"member": "c", "name": "Corporate", "role": "reconciling",
             "metrics": {"revenue": -5e9}},
        ]}]}
    sigs = footnote_risk_signals(footnotes)
    assert [(s.severity, s.label) for s in sigs] == [
        ("info", "Segment revenue concentration"),
    ]
    assert "70%" in sigs[0].detail and "Cloud" in sigs[0].detail
    # a single reportable segment is not concentration — nothing to compare
    footnotes["business-segmentation"][0]["segments"] = footnotes[
        "business-segmentation"
    ][0]["segments"][:1]
    assert footnote_risk_signals(footnotes) == []


def test_footnote_signals_sorted_by_severity():
    footnotes = {
        "concentration": {"series": [
            {"riskType": "customer", "counterparties": [
                {"name": "Acme", "history": [{"period": "2024-12-31", "share": 0.20}]},
            ]},
        ]},
        "retirement-plans": [{"fiscalYear": 2024, "plans": [
            {"type": "pension", "fundedStatus": -8e9},
        ]}],
    }
    sigs = footnote_risk_signals(footnotes, market_cap=100e9)
    assert [s.severity for s in sigs] == ["medium", "info"]


def test_footnote_signals_unrecognized_payloads_stay_silent():
    """Unknown shapes never fabricate a bullet."""
    garbage = {
        "concentration": {"weird": 1},
        "debt-structure": {"weird": [{"x": 1}]},
        "credit-facilities": [{"utilization": "high"}],
        "stock-compensation": [{"awards": [{"name": "RSU"}]}],
        "retirement-plans": [{"plans": [{"type": "pension"}]}],
        "supplier-finance": [{"totals": {}}],
        "fair-value-hierarchy": [{"measures": [{"measure": "assets"}]}],
        "revenue-segmentation": [{"geography": {}}],
        "business-segmentation": [{"segments": []}],
    }
    assert footnote_risk_signals(garbage) == []


def test_footnote_json_roundtrip():
    """Cached payloads (JSON round-tripped) parse identically."""
    footnotes = {"concentration": {"series": [
        {"riskType": "customer", "counterparties": [
            {"name": "Acme", "history": [{"period": "2024-12-31", "share": 0.20}]},
        ]},
    ]}}
    roundtripped = json.loads(json.dumps(footnotes))
    assert footnote_risk_signals(roundtripped) == footnote_risk_signals(footnotes)
