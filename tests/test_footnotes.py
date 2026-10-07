"""Tests for the StockFit footnotes layer — fetch gating and defensive parsing.

No network: ``_get`` is mocked (same pattern as the FMP / insights tests).
"""
from __future__ import annotations

import json
from datetime import date

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
    y = date.today().year  # relative years — the rule uses the current year
    footnotes = {"debt-structure": [{"fiscalYear": y, "instruments": [
        {"name": "Near", "dueYear": y + 1, "faceAmount": 500e6},
        {"name": "Mid", "dueYear": y + 10, "faceAmount": 400e6},
        {"name": "Far", "dueYear": y + 15, "faceAmount": 100e6},
    ]}]}
    sigs = footnote_risk_signals(footnotes)
    assert [(s.severity, s.label) for s in sigs] == [("medium", "Debt maturity wall")]
    assert "$500M" in sigs[0].detail and "50%" in sigs[0].detail


def test_debt_maturity_wall_below_threshold_silent():
    y = date.today().year
    footnotes = {"debt-structure": [{"fiscalYear": y, "instruments": [
        {"dueYear": y + 1, "faceAmount": 100e6},
        {"dueYear": y + 15, "faceAmount": 900e6},
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
    sigs = footnote_risk_signals(footnotes, revenue=100e9)
    assert {(s.severity, s.label) for s in sigs} == {
        ("info", "Geographic revenue concentration"),
        ("info", "Product revenue concentration"),
    }
    details = " ".join(s.detail for s in sigs)
    assert "65%" in details and "United States" in details
    assert "55%" in details and "iPhone" in details


def test_geography_country_alone_never_reads_100_percent():
    """US as a country + the rest of the world as regions is one mix, not one country.

    GOOGL's FY2025 10-K: US 194,229 (48%), EMEA 117,152 (29%), APAC 67,680
    (17%), Other Americas 23,902 (6%).  StockFit buckets the US under
    ``countries`` and the other three under ``regions``, so reading countries
    alone (the pre-0.17.2 rule) reported "100% of revenue from United States"
    — on 19 of the top-100 tickers, MSFT and KO included.
    """
    footnotes = {"revenue-segmentation": [{"fiscalYear": 2025,
        "geography": {
            "countries": [
                {"code": "US", "name": "United States", "continent": "Americas",
                 "value": 194229e6},
            ],
            "usStates": [],
            "regions": [
                {"member": "goog:EMEAMember", "name": "EMEA", "continent": None,
                 "value": 117152e6, "explainedByCountries": 0.0, "other": 117152e6},
                {"member": "goog:APACMember", "name": "APAC", "continent": None,
                 "value": 67680e6, "explainedByCountries": 0.0, "other": 67680e6},
                {"member": "goog:OtherAmericasMember", "name": "Other Americas",
                 "continent": None, "value": 23902e6,
                 "explainedByCountries": 0.0, "other": 23902e6},
            ],
            "residuals": [],
        },
        "product": []}]}
    # revenue reconciles with the map (402,963e6), and 48% US is under the 60%
    # watch threshold, so no bullet at all
    assert footnote_risk_signals(footnotes, revenue=402963e6) == []


def test_geography_concentration_counts_the_region_remainder():
    """The share is over the whole disclosed mix (regions included)."""
    footnotes = {"revenue-segmentation": [{"fiscalYear": 2025,
        "geography": {
            "countries": [
                {"code": "US", "name": "United States", "continent": "Americas",
                 "value": 70e9},
            ],
            "usStates": [],
            "regions": [
                {"member": "us-gaap:NonUsMember", "name": "Non-US",
                 "continent": None, "value": 30e9,
                 "explainedByCountries": 0.0, "other": 30e9},
            ],
            "residuals": [],
        },
        "product": []}]}
    sigs = footnote_risk_signals(footnotes, revenue=100e9)
    assert [(s.severity, s.label) for s in sigs] == [
        ("info", "Geographic revenue concentration"),
    ]
    assert sigs[0].detail == "70% of revenue from United States"


def test_geography_rollup_region_does_not_double_count():
    """A region fully covered by country leaves contributes nothing (no 2× count)."""
    footnotes = {"revenue-segmentation": [{"fiscalYear": 2025,
        "geography": {
            "countries": [
                {"code": "US", "name": "United States", "continent": "Americas",
                 "value": 65e9},
                {"code": "CN", "name": "China", "continent": "Asia", "value": 35e9},
            ],
            "usStates": [],
            "regions": [
                {"member": "srt:AmericasMember", "name": "Americas",
                 "continent": "Americas", "value": 65e9,
                 "explainedByCountries": 65e9, "other": 0.0},
            ],
            "residuals": [],
        },
        "product": []}]}
    sigs = footnote_risk_signals(footnotes, revenue=100e9)
    assert sigs[0].detail == "65% of revenue from United States"


def test_geography_region_without_reconciliation_fields_falls_back_to_continent():
    """No ``explainedByCountries``/``other`` (older shape) → continent matching."""
    footnotes = {"revenue-segmentation": [{"fiscalYear": 2025,
        "geography": {
            "countries": [
                {"code": "US", "name": "United States", "continent": "Americas",
                 "value": 62e9},
            ],
            "usStates": [],
            "regions": [
                {"member": "us-gaap:EMEAMember", "name": "EMEA",
                 "continent": "Europe", "value": 38e9},
            ],
            "residuals": [],
        },
        "product": []}]}
    sigs = footnote_risk_signals(footnotes, revenue=100e9)
    assert sigs[0].detail == "62% of revenue from United States"


def test_geography_partial_map_yields_no_bullet():
    """A map that never reaches the company's revenue cannot speak for it.

    NFLX in the 2026-10-07 run: ``countries`` held the US line alone — 41% of
    its 45.183e9 revenue — while ``product`` held Streaming in full.  Dividing
    by that truncated total shipped both "100% of revenue from United States"
    and "244% of revenue from Streaming"; the map is simply partial.
    """
    footnotes = {"revenue-segmentation": [{"fiscalYear": 2025,
        "geography": {
            "countries": [
                {"code": "US", "name": "United States", "continent": "Americas",
                 "value": 18.5e9},
            ],
            "usStates": [], "regions": [], "residuals": [],
        },
        "product": [
            {"member": "nflx:StreamingMember", "name": "Streaming",
             "value": 45.183e9},
        ]}]}
    sigs = footnote_risk_signals(footnotes, revenue=45.183e9)
    assert [(s.severity, s.label) for s in sigs] == [
        ("info", "Product revenue concentration"),
    ]
    assert sigs[0].detail == "100% of revenue from Streaming"


def test_geography_over_counted_map_yields_no_bullet():
    """Leaves that overlap each other sum past the revenue — no share either.

    A ``Non-US`` region repeated beside the regions it already contains (JNJ
    measured 1.43× its revenue in the 2026-10-07 run) inflates the total, so
    every country's share is deflated by the double count — here US would read
    83% of a 120e9 total that is really 100e9 of revenue.
    """
    footnotes = {"revenue-segmentation": [{"fiscalYear": 2025,
        "geography": {
            "countries": [
                {"code": "US", "name": "United States", "continent": "Americas",
                 "value": 100e9},
            ],
            "usStates": [],
            "regions": [
                {"member": "x:AmericasMember", "name": "Americas",
                 "continent": None, "value": 20e9,
                 "explainedByCountries": 0.0, "other": 20e9},
            ],
            "residuals": [],
        },
        "product": []}]}
    assert footnote_risk_signals(footnotes, revenue=100e9) == []


def test_product_line_above_revenue_is_dropped():
    """A footnote line that outsizes the company is not a share of it.

    BMY's ``Sales Revenue Gross`` is 1.83× its revenue (88,085e9 against
    48,195e9): the pre-0.17.3 rule printed "183% of revenue from Sales Revenue
    Gross".  The line is dropped and the largest line that fits takes its
    place; the geography side, which does reconcile, keeps its bullet.
    """
    footnotes = {"revenue-segmentation": [{"fiscalYear": 2024,
        "geography": {
            "countries": [
                {"code": "US", "name": "United States", "continent": "Americas",
                 "value": 33.279e9},
            ],
            "usStates": [],
            "regions": [
                {"member": "us-gaap:NonUsMember", "name": "Non-US",
                 "continent": None, "value": 13.828e9,
                 "explainedByCountries": 0.0, "other": 13.828e9},
            ],
            "residuals": [
                {"member": "bmy:OtherRegionMember", "name": "Other Region",
                 "value": 1.087e9},
            ],
        },
        "product": [
            {"member": "bmy:SalesRevenueGrossMember", "name": "Sales Revenue Gross",
             "value": 88.085e9},
            {"member": "bmy:NetProductSalesMember", "name": "Net Product Sales",
             "value": 46.756e9},
        ]}]}
    sigs = footnote_risk_signals(footnotes, revenue=48.195e9)
    details = {s.label: s.detail for s in sigs}
    assert details["Geographic revenue concentration"] == \
        "69% of revenue from United States"
    assert details["Product revenue concentration"] == \
        "97% of revenue from Net Product Sales"


def test_product_share_without_geography_uses_the_revenue():
    """No geography block is no obstacle: the product line is a share of the
    revenue, which does not need the map to exist (it used to be a share of
    the geographic total, so a missing map silenced the bullet)."""
    footnotes = {"revenue-segmentation": [{"fiscalYear": 2025,
        "geography": {},
        "product": [
            {"member": "nflx:StreamingMember", "name": "Streaming",
             "value": 45.183e9},
        ]}]}
    sigs = footnote_risk_signals(footnotes, revenue=45.183e9)
    assert [s.detail for s in sigs] == ["100% of revenue from Streaming"]


def test_without_revenue_no_share_is_invented():
    """Statements unavailable → silence, never a share of a truncated total."""
    footnotes = {"revenue-segmentation": [{"fiscalYear": 2025,
        "geography": {
            "countries": [
                {"code": "US", "name": "United States", "continent": "Americas",
                 "value": 18.5e9},
            ],
            "usStates": [], "regions": [], "residuals": [],
        },
        "product": [
            {"member": "nflx:StreamingMember", "name": "Streaming",
             "value": 45.183e9},
        ]}]}
    # geography has nothing to reconcile against, and the only total left —
    # the truncated geo one — is smaller than the product line
    assert footnote_risk_signals(footnotes) == []
    # …while a line that does fit still gets its share of that total
    footnotes["revenue-segmentation"][0]["product"] = [
        {"member": "nflx:StreamingMember", "name": "Streaming", "value": 8e9},
    ]
    sigs = footnote_risk_signals(footnotes)
    assert [s.detail for s in sigs] == ["43% of revenue from Streaming"]


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
