"""Tests for the deep-dive report — mocked data sources, no live network."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
from rich.console import Console

from investdaytip.data_source import StockData
from investdaytip.data_source_stockfit import ResearchSummary, fetch_research_summary
from investdaytip.deep_dive import (
    DeepDive,
    build_deep_dive,
    render_html,
    render_rich,
)
from investdaytip.financial_health import altman_z_score, piotroski_f_score

_RESEARCH_PAYLOAD = {
    "profile": {"name": "Apple Inc.", "sector": "Technology"},
    "snapshot": {
        "period": "2025-09-28",
        "fiscalYear": 2025,
        "eps": 7.0,
        "revenue": 400e9,
        "netIncome": 100e9,
        "grossMargin": 45.0,      # percent form (45.0 = 45%)
        "operatingMargin": 30.0,
        "netMargin": 25.0,
        "roe": 150.0,
        "roic": 55.0,
        "freeCashFlow": 95e9,
        "fcfToNetIncome": 95.0,
        "revenueGrowth": 0.05,    # decimal form (0.05 = 5%)
        "epsGrowth": 0.10,
        "piotroskiFScore": 7,
        "altmanZScore": 5.2,
        "nextEarningsDate": "2026-10-30",
    },
    "keyMetrics": {"sector": "services", "general": {"pe": 25.0}},
}


# ── fetch_research_summary ───────────────────────────────────────────────────


def test_fetch_research_summary_parses_payload(mocker, monkeypatch):
    monkeypatch.setenv("STOCKFIT_API_KEY", "k")
    monkeypatch.setenv("STOCKFIT_PIT_SNAPSHOT_DIR", "/nonexistent")
    get = mocker.patch(
        "investdaytip.data_source_stockfit._get", return_value=_RESEARCH_PAYLOAD
    )
    summary = fetch_research_summary("AAPL")
    assert summary is not None
    assert summary.profile["name"] == "Apple Inc."
    assert summary.snapshot["eps"] == 7.0
    assert summary.key_metrics["sector"] == "services"
    get.assert_called_once_with("company/research-summary", {"symbol": "AAPL"})


def test_fetch_research_summary_failure_returns_none(mocker, monkeypatch):
    from investdaytip.data_source_stockfit import StockfitError

    monkeypatch.setenv("STOCKFIT_API_KEY", "k")
    mocker.patch(
        "investdaytip.data_source_stockfit._get",
        side_effect=StockfitError("boom"),
    )
    assert fetch_research_summary("AAPL") is None  # soft-fail, never raises


def test_fetch_research_summary_cached(enabled_temp_cache, mocker, monkeypatch):
    monkeypatch.setenv("STOCKFIT_API_KEY", "k")
    get = mocker.patch(
        "investdaytip.data_source_stockfit._get", return_value=_RESEARCH_PAYLOAD
    )
    first = fetch_research_summary("AAPL")
    second = fetch_research_summary("AAPL")
    assert get.call_count == 1
    assert first is not None and second is not None
    assert second.snapshot == first.snapshot


# ── build_deep_dive ──────────────────────────────────────────────────────────


def _frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    income = pd.DataFrame(
        {
            pd.Timestamp("2023-12-31"): {
                "Net Income": 80.0, "Total Revenue": 350.0,
                "Gross Profit": 157.5, "EBIT": 120.0,
            },
            pd.Timestamp("2024-12-31"): {
                "Net Income": 100.0, "Total Revenue": 400.0,
                "Gross Profit": 200.0, "EBIT": 150.0,
            },
        }
    )
    balance = pd.DataFrame(
        {
            pd.Timestamp("2023-12-31"): {
                "Total Assets": 1000.0, "Current Assets": 500.0,
                "Current Liabilities": 250.0, "Total Debt": 200.0,
                "Total Liabilities Net Minority Interest": 400.0,
                "Ordinary Shares Number": 11.0, "Retained Earnings": 300.0,
            },
            pd.Timestamp("2024-12-31"): {
                "Total Assets": 1000.0, "Current Assets": 520.0,
                "Current Liabilities": 250.0, "Total Debt": 180.0,
                "Total Liabilities Net Minority Interest": 380.0,
                "Ordinary Shares Number": 10.0, "Retained Earnings": 350.0,
            },
        }
    )
    cash = pd.DataFrame(
        {
            pd.Timestamp("2023-12-31"): {"Operating Cash Flow": 90.0},
            pd.Timestamp("2024-12-31"): {"Operating Cash Flow": 150.0},
        }
    )
    return income, balance, cash


def _patch_statements(mocker):
    income, balance, cash = _frames()
    mocker.patch(
        "investdaytip.deep_dive.fetch_statement_frames", return_value=(income, balance)
    )
    mocker.patch("investdaytip.deep_dive.fetch_cash_flow_frame", return_value=cash)


def test_build_deep_dive_local_diagnostics_without_key(mocker, monkeypatch):
    monkeypatch.delenv("STOCKFIT_API_KEY", raising=False)
    monkeypatch.setenv("STOCKFIT_PIT_SNAPSHOT_DIR", "/nonexistent")
    mocker.patch(
        "investdaytip.deep_dive.fetch_asset",
        return_value=StockData(ticker="AAPL", name="Apple Inc.", market_cap=1e12),
    )
    _patch_statements(mocker)
    research_mock = mocker.patch("investdaytip.deep_dive.fetch_research_summary")

    dd = build_deep_dive("AAPL")
    assert dd.score is not None
    assert dd.piotroski is not None
    assert dd.altman is not None
    assert dd.research is None
    research_mock.assert_not_called()  # no key → never touched


def test_build_deep_dive_with_key_includes_research(mocker, monkeypatch):
    monkeypatch.setenv("STOCKFIT_API_KEY", "k")
    monkeypatch.setenv("STOCKFIT_PIT_SNAPSHOT_DIR", "/nonexistent")
    mocker.patch(
        "investdaytip.deep_dive.fetch_asset",
        return_value=StockData(ticker="AAPL", market_cap=1e12),
    )
    _patch_statements(mocker)
    mocker.patch("investdaytip.deep_dive.detect_plan", return_value="pro")
    mocker.patch(
        "investdaytip.deep_dive.fetch_research_summary",
        return_value=ResearchSummary(ticker="AAPL", snapshot=_RESEARCH_PAYLOAD["snapshot"]),
    )
    dd = build_deep_dive("AAPL")
    assert dd.research is not None
    assert dd.research.snapshot["eps"] == 7.0


def test_build_deep_dive_plan_gated_skips_research(mocker, monkeypatch):
    """Below Starter the summary is skipped — never fetched, never fabricated."""
    monkeypatch.setenv("STOCKFIT_API_KEY", "k")
    mocker.patch(
        "investdaytip.deep_dive.fetch_asset",
        return_value=StockData(ticker="AAPL", market_cap=1e12),
    )
    _patch_statements(mocker)
    mocker.patch("investdaytip.deep_dive.detect_plan", return_value="free")
    research_mock = mocker.patch("investdaytip.deep_dive.fetch_research_summary")

    dd = build_deep_dive("AAPL")
    assert dd.research is None
    research_mock.assert_not_called()


# ── rendering ────────────────────────────────────────────────────────────────


def _sample_dive() -> DeepDive:
    income, balance, cash = _frames()
    from investdaytip.financial_health import annual_facts

    cur = annual_facts(income, balance, cash, datetime.now())
    prev = annual_facts(income, balance, cash, datetime.now(), years_back=1)
    return DeepDive(
        ticker="AAPL",
        data=StockData(ticker="AAPL", name="Apple Inc."),
        research=ResearchSummary(ticker="AAPL", snapshot=dict(_RESEARCH_PAYLOAD["snapshot"])),
        piotroski=piotroski_f_score(cur, prev),
        altman=altman_z_score(cur, 1e12),
    )


def test_render_rich_smoke():
    import io

    buf = io.StringIO()
    render_rich([_sample_dive()], console=Console(file=buf, force_terminal=False))
    out = buf.getvalue()
    assert "AAPL" in out
    assert "Piotroski" in out
    assert "Altman" in out
    assert "Devil's advocate" in out
    assert "no significant risk signals" in out


def test_render_html_plan_gated_reason(mocker, monkeypatch):
    monkeypatch.setenv("STOCKFIT_API_KEY", "k")
    mocker.patch("investdaytip.deep_dive.detect_plan", return_value="free")
    dd = _sample_dive()
    dd.research = None
    html = render_html([dd])
    assert "requires Starter plan (current plan: free)" in html


def test_render_html_smoke():
    html = render_html([_sample_dive()])
    assert "AAPL" in html
    assert "Piotroski F-Score" in html
    assert "not scored" in html
    assert "zone-" in html
    # StockFit percent-form fields are normalized (25.0 = 25%, not 2500%)
    assert "25.0%" in html
    assert "2500" not in html
    # DCF link-out is intentionally NOT rendered while StockFit's valuation
    # platform is in early access (user decision 2026-09-27)
    assert "stockfit.io" not in html
    assert "Devil" in html
    assert "no significant risk signals" in html
    # no-key run renders the omission note instead of the snapshot block
    dd = _sample_dive()
    dd.research = None
    html2 = render_html([dd])
    assert "omitted" in html2


# ── CLI wiring ───────────────────────────────────────────────────────────────


def test_cli_deep_dive_exports_html(mocker, tmp_path, monkeypatch):
    from investdaytip.main import main

    monkeypatch.delenv("STOCKFIT_API_KEY", raising=False)
    mocker.patch("investdaytip.deep_dive.build_deep_dive", return_value=_sample_dive())
    out = tmp_path / "dd.html"
    rc = main(["deep-dive", "-t", "AAPL", "--export-html", str(out)])
    assert rc == 0
    assert out.exists()
    assert "AAPL" in out.read_text(encoding="utf-8")


def test_cli_deep_dive_requires_tickers():
    import pytest

    from investdaytip.main import main

    with pytest.raises(SystemExit):  # argparse: -t is required
        main(["deep-dive"])
