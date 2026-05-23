"""Tests for self-contained HTML export."""

from pathlib import Path

from investdaytip.data_source import EtfData, StockData
from investdaytip.html_export import export_recommendations_html, infer_region_from_ticker
from investdaytip.scoring import ScoredAsset


def test_infer_region_from_ticker_suffixes():
    assert infer_region_from_ticker("AAPL") == "us"
    assert infer_region_from_ticker("SAP.DE") == "eu"
    assert infer_region_from_ticker("7203.T") == "asia"


def test_export_html_contains_filters_and_rows(tmp_path: Path):
    stock = ScoredAsset(
        data=StockData(
            ticker="AAPL",
            name="Apple",
            sector="Technology",
            current_price=190.5,
            return_1m=0.03,
            return_12m=0.21,
            currency="USD",
        ),
        asset_type="STOCK",
        total=88.2,
        breakdown={"Quality": 90, "Value": 75, "Health": 85, "Trend": 88},
        rationale=["strong ROE", "solid growth"],
    )
    etf = ScoredAsset(
        data=EtfData(
            ticker="CSPX.L",
            name="iShares Core S&P 500 UCITS ETF",
            category="Large Blend",
            current_price=500.1,
            return_1m=0.01,
            return_12m=0.17,
            currency="USD",
        ),
        asset_type="ETF",
        total=79.4,
        breakdown={"Returns": 82, "RiskAdj": 76, "Size": 89, "Cost/Yield": 70},
        rationale=["low expense ratio"],
    )

    out = tmp_path / "report.html"
    export_recommendations_html(
        [stock, etf],
        str(out),
        top_n=10,
        asset_class="all",
        region="all",
        tickers=None,
    )

    html = out.read_text(encoding="utf-8")
    assert "<title>InvestDayTip Report</title>" in html
    assert "id=\"assetClass\"" in html
    assert "id=\"region\"" in html
    assert "AAPL" in html
    assert "CSPX.L" in html
    assert '"asset_class": "all"' in html
    assert '"top_n": 10' in html
