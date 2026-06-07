"""Tests for self-contained HTML export."""

import re
from pathlib import Path

from investdaytip.backtest import BacktestResult, BacktestSnapshot
from investdaytip.data_source import EtfData, StockData
from investdaytip.html_export import (
    _TABLE_BASE_COLUMN_COUNT,
    _google_finance_url,
    _tradingview_url,
    export_backtest_html,
    export_recommendations_html,
    infer_region_from_ticker,
)
from investdaytip.scoring import ScoredAsset


def test_infer_region_from_ticker_suffixes():
    assert infer_region_from_ticker("AAPL") == "us"
    assert infer_region_from_ticker("SAP.DE") == "eu"
    assert infer_region_from_ticker("7203.T") == "asia"


def test_colspan_matches_header_column_count(tmp_path):
    out = tmp_path / "empty.html"
    export_recommendations_html([], str(out), top_n=5, asset_class="all", tickers=None)
    html = out.read_text(encoding="utf-8")
    # <th matches both real <th> cells and the <thead> tag; subtract that one.
    th_count = html.count("<th") - html.count("<thead")
    assert th_count == _TABLE_BASE_COLUMN_COUNT
    colspans = {int(c) for c in re.findall(r'colspan="(\d+)"', html)}
    assert colspans == {_TABLE_BASE_COLUMN_COUNT}


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
        tickers_file="tickers-files-examples/semiconductors_relevant_tickers.txt",
    )

    html = out.read_text(encoding="utf-8")
    assert "<title>InvestDayTip Report</title>" in html
    assert "id=\"assetClass\"" in html
    assert "id=\"region\"" in html
    assert "AAPL" in html
    assert "CSPX.L" in html
    assert '"asset_class": "all"' in html
    assert '"top_n": 10' in html
    assert '"tickers_file": "tickers-files-examples/semiconductors_relevant_tickers.txt"' in html


def test_google_finance_url_uses_exact_exchange_when_mapped():
    assert _google_finance_url("NVDA") == "https://www.google.com/finance/quote/NVDA:NASDAQ?hl=en"
    assert _google_finance_url("HWM") == "https://www.google.com/finance/quote/HWM:NYSE?hl=en"
    assert _google_finance_url("VWS.CO") == "https://www.google.com/finance/quote/VWS:CPH?hl=en"
    assert _google_finance_url("600519.SS") == "https://www.google.com/finance/quote/600519:SHA?hl=en"


def test_google_finance_url_uses_exchange_hint_for_unsuffixed_us_tickers():
    assert _google_finance_url("TSM", exchange_hint="NYQ") == "https://www.google.com/finance/quote/TSM:NYSE?hl=en"
    assert _google_finance_url("TXT", exchange_hint="NYQ") == "https://www.google.com/finance/quote/TXT:NYSE?hl=en"


def test_tradingview_url_uses_exchange_override_when_mapped():
    assert _tradingview_url("HWM") == "https://www.tradingview.com/symbols/NYSE:HWM"


def test_tradingview_url_uses_exchange_hint_for_unsuffixed_us_tickers():
    assert _tradingview_url("TSM", exchange_hint="NYQ") == "https://www.tradingview.com/symbols/NYSE:TSM"
    assert _tradingview_url("TXT", exchange_hint="NYQ") == "https://www.tradingview.com/symbols/NYSE:TXT"


def test_google_finance_url_falls_back_to_search_when_unmapped():
    assert _google_finance_url("FOO.XY") == "https://www.google.com/finance?hl=en&q=FOO.XY"


# ── Backtest HTML export ──────────────────────────────────────────────────────


def _make_backtest_sample() -> BacktestResult:
    from investdaytip.scoring import ScoredAsset

    s1 = ScoredAsset(
        data=StockData(ticker="AAPL", name="Apple", sector="Technology",
                       current_price=190.5, return_1m=0.03, return_12m=0.21,
                       currency="USD"),
        asset_type="STOCK", total=88.2,
        breakdown={"Q": 90, "V": 75, "H": 85, "T": 88},
        rationale=["strong ROE"],
    )
    s2 = ScoredAsset(
        data=StockData(ticker="MSFT", name="Microsoft", sector="Technology",
                       current_price=350.0, return_1m=0.02, return_12m=0.18,
                       currency="USD"),
        asset_type="STOCK", total=85.0,
        breakdown={"Q": 88, "V": 70, "H": 80, "T": 86},
        rationale=["solid growth"],
    )
    snap1 = BacktestSnapshot(
        date=__import__("datetime").datetime(2024, 6, 1),
        picks=[s1, s2],
        avg_return_6m=0.08, avg_return_12m=0.22,
        benchmark_return_6m=0.05, benchmark_return_12m=0.18,
    )
    snap2 = BacktestSnapshot(
        date=__import__("datetime").datetime(2024, 9, 1),
        picks=[s2, s1],
        avg_return_6m=0.06, avg_return_12m=0.20,
        benchmark_return_6m=0.04, benchmark_return_12m=0.16,
    )
    return BacktestResult(
        snapshots=[snap1, snap2], total_snapshots=2,
        cumulative_return=0.15, benchmark_cumulative_return=0.10,
        sharpe=1.5, benchmark_sharpe=0.9,
        win_rate_6m=1.0, win_rate_12m=1.0,
        max_drawdown=-0.05, alpha=0.04,
    )


def test_export_backtest_html_contains_metrics(tmp_path):
    result = _make_backtest_sample()
    out = tmp_path / "backtest.html"
    export_backtest_html(result, str(out), tickers=["AAPL", "MSFT"])
    html = out.read_text(encoding="utf-8")
    assert "Backtest Report" in html
    assert "Cumulative Return" in html
    assert "15.00%" in html or "15%" in html
    assert "10.00%" in html
    assert "1.50" in html
    assert "AAPL" in html
    assert "MSFT" in html
    assert "2024-06-01" in html
    assert "2024-09-01" in html


def test_export_backtest_html_empty_result(tmp_path):
    result = BacktestResult(snapshots=[])
    out = tmp_path / "empty.html"
    export_backtest_html(result, str(out))
    html = out.read_text(encoding="utf-8")
    assert "No snapshots were generated" in html


def test_export_backtest_html_shows_errors(tmp_path):
    result = BacktestResult(snapshots=[], errors=["Could not fetch SPY", "Rate limit hit"])
    out = tmp_path / "errors.html"
    export_backtest_html(result, str(out))
    html = out.read_text(encoding="utf-8")
    assert "Could not fetch SPY" in html
    assert "Rate limit hit" in html


# ── Superinvestor & Technical columns ────────────────────────────────────────


def test_export_html_includes_superinvestor_column(tmp_path):
    stock = ScoredAsset(
        data=StockData(
            ticker="AAPL", name="Apple", sector="Technology",
            current_price=190.5, return_1m=0.03, return_12m=0.21,
            currency="USD",
        ),
        asset_type="STOCK", total=88.2,
        breakdown={"Quality": 90, "Value": 75, "Health": 85, "Trend": 88},
        rationale=["strong ROE"],
        superinvestor_count=12,
    )
    out = tmp_path / "si.html"
    export_recommendations_html(
        [stock], str(out), top_n=5, asset_class="all", tickers=None,
        include_superinvestor=True,
    )
    html = out.read_text(encoding="utf-8")
    assert "Superinvestors" in html
    assert "12" in html


def test_export_html_includes_technical_columns(tmp_path):
    stock = ScoredAsset(
        data=StockData(
            ticker="AAPL", name="Apple", sector="Technology",
            current_price=190.5, return_1m=0.03, return_12m=0.21,
            currency="USD", rsi_14=28.5, macd_histogram=0.02,
        ),
        asset_type="STOCK", total=88.2,
        breakdown={"Quality": 90, "Value": 75, "Health": 85, "Trend": 88},
        rationale=["RSI 28.5 suggests oversold", "MACD histogram positive"],
    )
    out = tmp_path / "tech.html"
    export_recommendations_html(
        [stock], str(out), top_n=5, asset_class="all", tickers=None,
        include_technical=True,
    )
    html = out.read_text(encoding="utf-8")
    assert "RSI" in html
    assert "MACD" in html
    assert "28.5" in html
