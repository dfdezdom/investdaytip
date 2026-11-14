"""SEC EDGAR PIT source: parsing, caching and provider wiring.

No HTTP anywhere: every test mocks ``investdaytip.data_source_edgar._get_json``
(the only function that opens a socket), and ``EDGAR_PIT_DIR`` is pointed at
``tmp_path`` by the autouse ``edgar_env`` fixture in ``conftest.py``.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime

import pytest

from investdaytip.data_source_edgar import (
    _from_cache,
    _payload_to_cache,
    cache_dir,
    cik_for,
    fetch_pit_statements,
    periods_from_companyfacts,
)
from investdaytip.data_source_stockfit import PitPeriod, PitSourceError, PitStatements

# ── payload helpers ──────────────────────────────────────────────────────────


def _row(
    end: str,
    filed: str,
    value: float,
    *,
    form: str = "10-K",
    fp: str = "FY",
) -> dict[str, object]:
    return {"end": end, "filed": filed, "val": value, "form": form, "fp": fp}


def _payload(
    gaap: dict[str, list[dict[str, object]]],
    dei: dict[str, list[dict[str, object]]] | None = None,
) -> dict[str, object]:
    facts: dict[str, object] = {
        "us-gaap": {
            concept: {"units": {"USD": rows}} for concept, rows in gaap.items()
        }
    }
    if dei:
        facts["dei"] = {
            concept: {"units": {"shares": rows}} for concept, rows in dei.items()
        }
    return {"cik": 320193, "entityName": "Test Co", "facts": facts}


def _annual(
    end: str,
    filed: str,
    *,
    net_income: float = 100.0,
    revenue: float = 1000.0,
    eps: float = 5.0,
    **extra: float,
) -> dict[str, list[dict[str, object]]]:
    rows: dict[str, list[dict[str, object]]] = {
        "NetIncomeLoss": [_row(end, filed, net_income)],
        "Revenues": [_row(end, filed, revenue)],
        "EarningsPerShareBasic": [_row(end, filed, eps)],
    }
    for concept, value in extra.items():
        rows[concept] = [_row(end, filed, value)]
    return rows


def _write_cache(name: str, obj: object) -> None:
    path = cache_dir() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))


def _age_cache(name: str, days: float) -> None:
    stamp = time.time() - days * 86400
    os.utime(cache_dir() / name, (stamp, stamp))


def _cached_periods() -> list[PitPeriod]:
    return [
        PitPeriod(
            period_end=datetime(2020, 9, 26),
            fiscal_year=2020,
            fiscal_period="FY",
            date_filed=datetime(2020, 10, 30),
            facts={"netIncome": 100.0, "revenue": 1000.0},
        ),
        PitPeriod(
            period_end=datetime(2019, 9, 28),
            fiscal_year=2019,
            fiscal_period="FY",
            date_filed=datetime(2019, 10, 31),
            facts={"netIncome": 90.0, "revenue": 900.0},
        ),
    ]


# ── periods_from_companyfacts: period selection ──────────────────────────────


def test_only_annual_10k_rows_become_periods():
    gaap = _annual("2020-09-26", "2020-10-30")
    gaap["NetIncomeLoss"].append(_row("2020-06-27", "2020-07-31", 20.0, form="10-Q", fp="Q3"))
    payload = _payload(
        gaap,
        # dei carries cover-page dates, not fiscal periods: they used to
        # create phantom periods with no financials.
        dei={"EntityCommonStockSharesOutstanding": [_row("2020-11-06", "2020-11-06", 15e9)]},
    )

    periods = periods_from_companyfacts("AAPL", 320193, payload)

    assert [p.period_end.strftime("%Y-%m-%d") for p in periods] == ["2020-09-26"]
    assert set(periods[0].facts) == {"netIncome", "revenue", "eps"}
    assert periods[0].fiscal_period == "FY"
    assert periods[0].date_filed.strftime("%Y-%m-%d") == "2020-10-30"


def test_comparatives_collapse_to_one_period_per_fiscal_year():
    # Every later 10-K re-reports the prior year as a comparative. Keying on
    # (end, filed) naively produced 156 periods for AAPL instead of 15.
    gaap = {
        "NetIncomeLoss": [
            _row("2020-09-26", "2021-10-29", 100.0),
            _row("2020-09-26", "2020-10-30", 100.0),
            _row("2019-09-28", "2020-10-30", 90.0),
            _row("2019-09-28", "2019-10-31", 90.0),
        ]
    }

    periods = periods_from_companyfacts("AAPL", 320193, _payload(gaap))

    assert [p.period_end.strftime("%Y-%m-%d") for p in periods] == [
        "2020-09-26",
        "2019-09-28",
    ]
    # Dated by its own 10-K, not by the comparative that repeated it.
    assert periods[0].date_filed.strftime("%Y-%m-%d") == "2020-10-30"
    assert periods[1].date_filed.strftime("%Y-%m-%d") == "2019-10-31"


def test_quarter_length_periods_are_dropped():
    gaap = {
        "NetIncomeLoss": [
            _row("2020-09-26", "2020-10-30", 100.0),
            _row("2020-06-27", "2020-07-31", 20.0),  # a quarter tagged on a 10-K
            _row("2019-09-28", "2019-10-31", 90.0),
        ]
    }

    periods = periods_from_companyfacts("AAPL", 320193, _payload(gaap))

    assert [p.period_end.strftime("%Y-%m-%d") for p in periods] == [
        "2020-09-26",
        "2019-09-28",
    ]


# ── periods_from_companyfacts: concept resolution and derivations ────────────


def test_eps_prefers_basic_and_revenue_prefers_total():
    gaap = _annual("2020-09-26", "2020-10-30", eps=9.01, revenue=713.2)
    gaap["EarningsPerShareDiluted"] = [_row("2020-09-26", "2020-10-30", 8.98)]
    gaap["RevenueFromContractWithCustomerExcludingAssessedTax"] = [
        _row("2020-09-26", "2020-10-30", 706.4)
    ]

    facts = periods_from_companyfacts("AAPL", 320193, _payload(gaap))[0].facts

    assert facts["eps"] == pytest.approx(9.01)
    assert facts["revenue"] == pytest.approx(713.2)


def test_gross_profit_is_derived_when_untagged():
    gaap = _annual(
        "2020-09-26",
        "2020-10-30",
        revenue=1000.0,
        CostOfRevenue=400.0,
    )

    facts = periods_from_companyfacts("AAPL", 320193, _payload(gaap))[0].facts

    assert facts["grossProfit"] == pytest.approx(600.0)


def test_total_debt_prefers_a_total_and_adds_commercial_paper():
    gaap = _annual(
        "2020-09-26",
        "2020-10-30",
        LongTermDebt=94.5,
        CommercialPaper=2.0,
    )

    facts = periods_from_companyfacts("AAPL", 320193, _payload(gaap))[0].facts

    assert facts["totalDebt"] == pytest.approx(96.5)


def test_total_debt_falls_back_to_components():
    gaap = _annual(
        "2020-09-26",
        "2020-10-30",
        LongTermDebtNoncurrent=80.0,
        LongTermDebtCurrent=10.0,
        CommercialPaper=5.0,
    )

    facts = periods_from_companyfacts("AAPL", 320193, _payload(gaap))[0].facts

    assert facts["totalDebt"] == pytest.approx(95.0)


def test_free_cash_flow_stays_none_without_capex():
    # REITs often tag no capex concept: silence, never operating cash flow
    # passed off as free cash flow.
    gaap = _annual("2020-09-26", "2020-10-30", NetCashProvidedByUsedInOperatingActivities=500.0)

    facts = periods_from_companyfacts("AMT", 1053507, _payload(gaap))[0].facts

    assert "freeCashFlow" not in facts
    assert facts["operatingCashFlow"] == pytest.approx(500.0)


def test_free_cash_flow_is_operating_cash_flow_minus_capex():
    gaap = _annual(
        "2020-09-26",
        "2020-10-30",
        NetCashProvidedByUsedInOperatingActivities=500.0,
        PaymentsToAcquirePropertyPlantAndEquipment=120.0,
    )

    facts = periods_from_companyfacts("AAPL", 320193, _payload(gaap))[0].facts

    assert facts["freeCashFlow"] == pytest.approx(380.0)


# ── cache layer ──────────────────────────────────────────────────────────────


def test_payload_roundtrip_preserves_periods_and_facts():
    cached = _payload_to_cache("AAPL", 320193, _cached_periods())
    statements = _from_cache("AAPL", cached)

    assert statements.ticker == "AAPL"
    assert statements.cik == 320193
    assert len(statements.income) == 2
    # The engine asks each series for its own keys; all three share the periods.
    assert statements.balance == statements.income == statements.cash_flow
    assert statements.income[0].period_end.strftime("%Y-%m-%d") == "2020-09-26"
    assert statements.income[0].facts["revenue"] == pytest.approx(1000.0)


def test_cik_map_is_cached_and_normalizes_dots(monkeypatch):
    _write_cache("_cik.json", {"BRK-B": 1067983})
    monkeypatch.setattr(
        "investdaytip.data_source_edgar._get_json",
        lambda *a, **k: pytest.fail("ticker map must come from cache"),
    )

    assert cik_for("brk.b") == 1067983
    assert cik_for("NOPE") is None


def test_unknown_ticker_returns_empty_without_network(monkeypatch):
    _write_cache("_cik.json", {})
    monkeypatch.setattr(
        "investdaytip.data_source_edgar._get_json",
        lambda *a, **k: pytest.fail("no network for an unknown ticker"),
    )

    statements = fetch_pit_statements("ZZZZ")

    assert statements.income == []
    assert statements.cik is None


def test_fresh_cache_avoids_the_network(monkeypatch):
    _write_cache("_cik.json", {"AAPL": 320193})
    _write_cache("AAPL.json", _payload_to_cache("AAPL", 320193, _cached_periods()))
    monkeypatch.setattr(
        "investdaytip.data_source_edgar._get_json",
        lambda *a, **k: pytest.fail("week-old parsed facts are fresh enough"),
    )

    statements = fetch_pit_statements("AAPL")

    assert [p.period_end.strftime("%Y-%m-%d") for p in statements.income] == [
        "2020-09-26",
        "2019-09-28",
    ]


def test_stale_cache_is_refreshed_and_rewritten(monkeypatch):
    _write_cache("_cik.json", {"AAPL": 320193})
    _write_cache("AAPL.json", _payload_to_cache("AAPL", 320193, _cached_periods()))
    _age_cache("AAPL.json", 30)

    payload = _payload(_annual("2021-09-25", "2021-10-29"))
    monkeypatch.setattr(
        "investdaytip.data_source_edgar._get_json", lambda *a, **k: payload
    )

    statements = fetch_pit_statements("AAPL")

    assert statements.income[0].period_end.strftime("%Y-%m-%d") == "2021-09-25"
    # Parsed facts only: the multi-megabyte payload never hits the disk.
    cached = json.loads((cache_dir() / "AAPL.json").read_text())
    assert set(cached) == {"ticker", "cik", "fetched_at", "periods"}
    assert "facts" not in cached


def test_companyfacts_failure_falls_back_to_stale_cache(monkeypatch):
    _write_cache("_cik.json", {"AAPL": 320193})
    _write_cache("AAPL.json", _payload_to_cache("AAPL", 320193, _cached_periods()))
    _age_cache("AAPL.json", 30)

    def boom(*_a, **_k):
        raise OSError("EDGAR unreachable")

    monkeypatch.setattr("investdaytip.data_source_edgar._get_json", boom)

    statements = fetch_pit_statements("AAPL")

    assert len(statements.income) == 2


def test_non_us_ticker_returns_empty_pit_statements(monkeypatch):
    _write_cache("_cik.json", {})
    monkeypatch.setattr(
        "investdaytip.data_source_edgar._get_json",
        lambda *a, **k: pytest.fail("no network for a non-filer"),
    )

    assert fetch_pit_statements("ASML.AS") == PitStatements(ticker="ASML-AS")


# ── provider wiring (backtest) ───────────────────────────────────────────────


def test_resolve_pit_source_keeps_the_explicit_flag(monkeypatch):
    from investdaytip.backtest import resolve_pit_source

    assert resolve_pit_source(None) is None
    assert resolve_pit_source("none") is None
    assert resolve_pit_source("stockfit") == "stockfit"
    assert resolve_pit_source("edgar") == "edgar"

    # Legacy env switch, documented when EDGAR landed.
    monkeypatch.setenv("PIT_SOURCE", "edgar")
    assert resolve_pit_source("stockfit") == "edgar"
    # ... but it never downgrades an explicit --pit-source edgar.
    monkeypatch.setenv("PIT_SOURCE", "stockfit")
    assert resolve_pit_source("edgar") == "edgar"
    # Unknown values pass through so run_backtest can reject them.
    assert resolve_pit_source("yahoo") == "yahoo"


def test_fetch_pit_statements_all_fail_raises_pit_source_error(mocker):
    from investdaytip.backtest import _fetch_pit_statements

    mocker.patch(
        "investdaytip.data_source_edgar.fetch_pit_statements",
        side_effect=PitSourceError("boom"),
    )

    with pytest.raises(PitSourceError, match="EDGAR PIT fetch failed for all 2"):
        _fetch_pit_statements(["A", "B"], source="edgar")


def test_fetch_pit_statements_partial_failure_keeps_good_tickers(mocker):
    from investdaytip.backtest import _fetch_pit_statements

    good = PitStatements(ticker="GOOD", income=_cached_periods())

    def fake(ticker: str) -> PitStatements:
        if ticker == "BAD":
            raise OSError("timeout")
        return good

    mocker.patch(
        "investdaytip.data_source_edgar.fetch_pit_statements", side_effect=fake
    )

    result = _fetch_pit_statements(["GOOD", "BAD"], source="edgar")

    assert set(result) == {"GOOD"}


def test_run_backtest_edgar_never_requires_stockfit_access(mocker, monkeypatch):
    from investdaytip.backtest import BacktestResult, run_backtest

    monkeypatch.delenv("STOCKFIT_API_KEY", raising=False)
    mocker.patch(
        "investdaytip.backtest.check_pit_access",
        side_effect=AssertionError("EDGAR must not require a key or snapshot"),
    )
    mocker.patch("investdaytip.backtest._fetch_pit_statements", return_value={})
    mocker.patch("investdaytip.backtest._fetch_all_data", return_value={})

    result = run_backtest(tickers=["AAPL"], pit_source="edgar", period="1y")

    assert isinstance(result, BacktestResult)


def test_cli_edgar_runs_keyless(mocker, monkeypatch):
    from investdaytip.backtest import BacktestResult
    from investdaytip.main import main

    monkeypatch.delenv("STOCKFIT_API_KEY", raising=False)
    result = BacktestResult(snapshots=[], total_snapshots=0)
    mock_run = mocker.patch("investdaytip.backtest.run_backtest", return_value=result)
    mocker.patch("investdaytip.main.export_backtest_html")

    rc = main(["backtest", "-t", "AAPL", "--pit-source", "edgar"])

    assert rc == 0
    assert mock_run.call_args.kwargs["pit_source"] == "edgar"
