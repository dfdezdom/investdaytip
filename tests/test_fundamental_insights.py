"""Tests for StockFit fundamental insights (opt-in ``--fundamental-insights``).

Network is never touched: the StockFit ``_get`` is mocked (or not called at
all, e.g. the missing-key path raises before any I/O).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import Console

from investdaytip.data_source import EtfData, StockData
from investdaytip.data_source_stockfit import (
    FundamentalInsights,
    InsightPeriod,
    StockfitError,
    _parse_insights,
    fetch_fundamental_insights,
)
from investdaytip.html_export import export_recommendations_html
from investdaytip.main import _fetch_report_insights, main
from investdaytip.scoring import ScoredAsset

# ── Canned payloads (shape verified live against the StockFit API) ──────────

REVENUE_PAYLOAD = {
    "periods": ["2022-09-24", "2023-09-30", "2024-09-28", "2025-09-27"],
    "series": [
        {"name": "Revenue", "data": [394328000000, 383285000000, 391035000000, 416161000000]},
        {"name": "Net Income", "data": [99803000000, 96995000000, 93736000000, 112010000000]},
    ],
    "rates": [
        {"name": "Gross Margin", "data": [0.4331, 0.4413, 0.4621, 0.4691]},
        {"name": "Operating Margin", "data": [0.3029, 0.2982, 0.3151, 0.3197]},
        {"name": "Net Margin", "data": [0.2531, 0.2531, 0.2397, 0.2692]},
    ],
}

QUALITY_PAYLOAD = {
    "periods": ["2022-09-24", "2023-09-30", "2024-09-28", "2025-09-27"],
    "series": [
        {"name": "Net Income", "data": [99803000000, 96995000000, 93736000000, 112010000000]},
        {"name": "Operating Cash Flow", "data": [122151000000, 110543000000, 118254000000, 111482000000]},
        {"name": "Free Cash Flow", "data": [111443000000, 99584000000, 108807000000, 98767000000]},
    ],
    "rates": [
        {"name": "FCF / Net Income", "data": [1.1166, 1.0267, 1.1608, 0.8818]},
        {"name": "OCF / Net Income", "data": [1.2239, 1.1397, 1.2616, 0.9953]},
    ],
}

BALANCE_PAYLOAD = {
    "periods": ["2022-09-24", "2023-09-30", "2024-09-28", "2025-09-27"],
    "series": [
        {"name": "Total Assets", "data": [352755000000, 352583000000, 364980000000, 359241000000]},
        {"name": "Total Liabilities", "data": [302083000000, 290437000000, 308030000000, 285508000000]},
        {"name": "Total Debt", "data": [111824000000, 106572000000, 97341000000, 91281000000]},
        {"name": "Cash", "data": [23646000000, 29965000000, 29943000000, 35934000000]},
    ],
    "rates": [
        {"name": "Debt to Equity", "data": [2.2068, 1.7149, 1.7092, 1.238]},
        {"name": "Current Ratio", "data": [0.8794, 0.988, 0.8673, 0.8933]},
    ],
}


def _fake_get(payloads: dict):
    """Build a ``_get`` replacement returning per-path payloads (or raising)."""

    def fake(path, params=None):
        result = payloads.get(path, [])
        if isinstance(result, Exception):
            raise result
        return result

    return fake


def _scored(ticker: str, asset_type: str = "STOCK") -> ScoredAsset:
    if asset_type == "ETF":
        data = EtfData(ticker=ticker, currency="USD", category="Large Blend")
    else:
        data = StockData(ticker=ticker, currency="USD")
    return ScoredAsset(data=data, asset_type=asset_type, total=50.0, breakdown={}, rationale=[])


def _insight_fixture() -> FundamentalInsights:
    return FundamentalInsights(
        ticker="AAPL",
        periods=[
            InsightPeriod(
                period_end="2025-09-27",
                revenue=416161000000.0,
                gross_margin=0.4691,
                operating_margin=0.3197,
                net_margin=0.2692,
                fcf_to_ni=0.8818,
                ocf_to_ni=0.9953,
                debt_to_equity=1.238,
                current_ratio=0.8933,
            ),
            InsightPeriod(
                period_end="2022-09-24",
                revenue=394328000000.0,
                gross_margin=0.4331,
                operating_margin=0.3029,
                net_margin=-0.0531,  # negative → rendered red
                fcf_to_ni=1.1166,
                ocf_to_ni=1.2239,
                debt_to_equity=2.2068,
                current_ratio=0.8794,
            ),
        ],
    )


# ── Parsing ────────────────────────────────────────────────────────────────


class TestParseInsights:
    def test_merges_three_charts_newest_first(self):
        insights = _parse_insights("AAPL", REVENUE_PAYLOAD, QUALITY_PAYLOAD, BALANCE_PAYLOAD)
        assert insights is not None
        assert insights.ticker == "AAPL"
        assert len(insights.periods) == 4
        assert insights.periods[0].period_end == "2025-09-27"  # newest first
        latest = insights.periods[0]
        assert latest.gross_margin == pytest.approx(0.4691)
        assert latest.revenue == 416161000000
        assert latest.fcf_to_ni == pytest.approx(0.8818)
        assert latest.debt_to_equity == pytest.approx(1.238)
        oldest = insights.periods[-1]
        assert oldest.gross_margin == pytest.approx(0.4331)
        assert oldest.current_ratio == pytest.approx(0.8794)

    def test_empty_payloads_return_none(self):
        assert _parse_insights("SAP.DE", [], [], []) is None

    def test_partial_chart_keeps_missing_metrics_none(self):
        insights = _parse_insights("AAPL", REVENUE_PAYLOAD, None, BALANCE_PAYLOAD)
        assert insights is not None
        latest = insights.periods[0]
        assert latest.gross_margin is not None          # from revenue chart
        assert latest.debt_to_equity is not None        # from balance chart
        assert latest.fcf_to_ni is None                 # quality chart missing
        assert latest.ocf_to_ni is None

    def test_null_metric_point_becomes_none(self):
        payload = {
            "periods": ["2025-09-27"],
            "series": [{"name": "Revenue", "data": [None]}],
            "rates": [{"name": "Gross Margin", "data": [None]}],
        }
        insights = _parse_insights("AAPL", payload, [], [])
        assert insights is not None
        assert insights.periods[0].revenue is None
        assert insights.periods[0].gross_margin is None


# ── Fetch ──────────────────────────────────────────────────────────────────


class TestFetchInsights:
    def test_happy_path_calls_three_charts_with_annual_params(self, mocker):
        fake = _fake_get({
            "financials/chart/revenue-profitability": REVENUE_PAYLOAD,
            "earnings/chart/quality": QUALITY_PAYLOAD,
            "financials/chart/balance-sheet-health": BALANCE_PAYLOAD,
        })
        get = mocker.patch("investdaytip.data_source_stockfit._get", side_effect=fake)
        insights = fetch_fundamental_insights("AAPL")
        assert insights is not None
        assert len(insights.periods) == 4
        assert get.call_count == 3
        for call in get.call_args_list:
            assert call.args[1] == {"symbol": "AAPL", "period": "annual", "limit": "5"}

    def test_returns_none_without_key(self, monkeypatch):
        # Real ``_get``: raises before any I/O when the key is missing.
        monkeypatch.delenv("STOCKFIT_API_KEY", raising=False)
        assert fetch_fundamental_insights("AAPL") is None

    def test_partial_endpoint_failure_returns_data_without_caching(
        self, mocker, enabled_temp_cache
    ):
        fake = _fake_get({
            "financials/chart/revenue-profitability": REVENUE_PAYLOAD,
            "earnings/chart/quality": StockfitError("boom"),
            "financials/chart/balance-sheet-health": BALANCE_PAYLOAD,
        })
        get = mocker.patch("investdaytip.data_source_stockfit._get", side_effect=fake)
        insights = fetch_fundamental_insights("AAPL")
        assert insights is not None
        assert insights.periods[0].gross_margin is not None
        assert insights.periods[0].fcf_to_ni is None
        # Incomplete results are not cached → a second call refetches.
        fetch_fundamental_insights("AAPL")
        assert get.call_count == 6

    def test_complete_result_served_from_cache(self, mocker, enabled_temp_cache):
        fake = _fake_get({
            "financials/chart/revenue-profitability": REVENUE_PAYLOAD,
            "earnings/chart/quality": QUALITY_PAYLOAD,
            "financials/chart/balance-sheet-health": BALANCE_PAYLOAD,
        })
        get = mocker.patch("investdaytip.data_source_stockfit._get", side_effect=fake)
        first = fetch_fundamental_insights("AAPL")
        second = fetch_fundamental_insights("AAPL")
        assert first is not None and second is not None
        assert get.call_count == 3  # second call hit the cache
        assert first == second

    def test_all_endpoints_failing_returns_none(self, mocker):
        fake = _fake_get({
            "financials/chart/revenue-profitability": StockfitError("down"),
            "earnings/chart/quality": StockfitError("down"),
            "financials/chart/balance-sheet-health": StockfitError("down"),
        })
        mocker.patch("investdaytip.data_source_stockfit._get", side_effect=fake)
        assert fetch_fundamental_insights("AAPL") is None


# ── HTML export ────────────────────────────────────────────────────────────


class TestHtmlExport:
    def test_includes_section_summary_and_detail(self, tmp_path: Path):
        out = tmp_path / "report.html"
        export_recommendations_html(
            [],
            str(out),
            top_n=5,
            asset_class="all",
            tickers=None,
            fundamental_insights={"AAPL": _insight_fixture()},
        )
        html = out.read_text(encoding="utf-8")
        assert "Fundamental insights" in html
        assert "<strong>AAPL</strong>" in html
        assert "FY2025" in html
        assert "416.2B" in html                       # revenue, compact
        assert "46.9%" in html                        # gross margin, latest
        assert '<span class="pos">↗</span>' in html   # margin trend (0.433 → 0.469)
        assert "AAPL — 2 fiscal years" in html        # <details> block
        assert "OCF/NI" in html                       # detail grid columns

    def test_negative_metric_rendered_red(self, tmp_path: Path):
        out = tmp_path / "report.html"
        export_recommendations_html(
            [],
            str(out),
            top_n=5,
            asset_class="all",
            tickers=None,
            fundamental_insights={"AAPL": _insight_fixture()},
        )
        html = out.read_text(encoding="utf-8")
        # Oldest FY has net_margin=-0.0531 → only the detail grid shows it.
        assert '<span class="neg">-5.3%</span>' in html
        # D/E fell (2.21 → 1.24): good when falling → green ↘.
        assert '<span class="pos">↘</span>' in html

    def test_default_export_has_no_section(self, tmp_path: Path):
        out = tmp_path / "report.html"
        export_recommendations_html([], str(out), top_n=5, asset_class="all", tickers=None)
        html = out.read_text(encoding="utf-8")
        assert "Fundamental insights" not in html


# ── CLI wiring ─────────────────────────────────────────────────────────────


class TestCliWiring:
    @pytest.fixture(autouse=True)
    def _no_sentiment_network(self, mocker):
        mocker.patch("investdaytip.sentiment.fear_greed_index", return_value=None)

    def test_flag_fetches_and_passes_insights_to_export(self, mocker, tmp_path: Path):
        scored = _scored("AAPL")
        mocker.patch("investdaytip.main.recommend", return_value=[scored])
        export_mock = mocker.patch(
            "investdaytip.main.export_recommendations_html",
            return_value=str(tmp_path / "r.html"),
        )
        insights = _insight_fixture()
        fetch_mock = mocker.patch(
            "investdaytip.main._fetch_report_insights", return_value={"AAPL": insights}
        )
        rc = main(["-t", "AAPL", "--export-html", str(tmp_path / "r.html"),
                   "--fundamental-insights", "--no-cache"])
        assert rc == 0
        fetch_mock.assert_called_once()
        assert export_mock.call_args.kwargs["fundamental_insights"] == {"AAPL": insights}

    def test_without_flag_no_fetch_and_none_passed(self, mocker, tmp_path: Path):
        mocker.patch("investdaytip.main.recommend", return_value=[_scored("AAPL")])
        export_mock = mocker.patch(
            "investdaytip.main.export_recommendations_html",
            return_value=str(tmp_path / "r.html"),
        )
        fetch_mock = mocker.patch("investdaytip.main._fetch_report_insights")
        rc = main(["-t", "AAPL", "--export-html", str(tmp_path / "r.html"), "--no-cache"])
        assert rc == 0
        fetch_mock.assert_not_called()
        assert export_mock.call_args.kwargs["fundamental_insights"] is None

    def test_flag_without_export_html_warns_and_skips_fetch(self, mocker):
        mocker.patch("investdaytip.main.recommend", return_value=[_scored("AAPL")])
        fetch_mock = mocker.patch("investdaytip.main._fetch_report_insights")
        rc = main(["-t", "AAPL", "--fundamental-insights"])
        assert rc == 0
        fetch_mock.assert_not_called()


# ── _fetch_report_insights (orchestration helper) ──────────────────────────


class TestFetchReportInsights:
    def test_missing_key_returns_empty(self, monkeypatch):
        monkeypatch.delenv("STOCKFIT_API_KEY", raising=False)
        results = [_scored("AAPL")]
        assert _fetch_report_insights(results, Console()) == {}

    def test_fetches_only_us_stocks(self, mocker, monkeypatch):
        monkeypatch.setenv("STOCKFIT_API_KEY", "test-key")
        fetch = mocker.patch(
            "investdaytip.main.fetch_fundamental_insights",
            side_effect=lambda t: FundamentalInsights(ticker=t, periods=[InsightPeriod(period_end="2025-09-27")]),
        )
        results = [_scored("AAPL"), _scored("SAP.DE"), _scored("VOO", asset_type="ETF")]
        out = _fetch_report_insights(results, Console())
        fetch.assert_called_once_with("AAPL")
        assert list(out) == ["AAPL"]

    def test_failed_tickers_skipped_without_raising(self, mocker, monkeypatch):
        monkeypatch.setenv("STOCKFIT_API_KEY", "test-key")
        mocker.patch(
            "investdaytip.main.fetch_fundamental_insights",
            side_effect=[None, RuntimeError("unexpected")],
        )
        results = [_scored("AAPL"), _scored("MSFT")]
        out = _fetch_report_insights(results, Console())
        assert out == {}
