"""Tests for CLI helper utilities."""

from datetime import datetime
from pathlib import Path

from investdaytip.main import _default_export_html_filename, _load_tickers_from_file, _merge_ticker_lists


def test_default_export_html_filename_format():
    now = datetime(2026, 5, 23, 9, 7)
    assert _default_export_html_filename(now) == "investDayTip-20260523-0907.html"


def test_default_export_html_filename_includes_tickers_file_tag():
    now = datetime(2026, 5, 23, 9, 7)
    assert _default_export_html_filename(
        now,
        "tickers-files-examples/semiconductors_relevant_tickers.txt",
    ) == "investDayTip-semiconductors-20260523-0907.html"


def test_load_tickers_from_file_supports_comments_and_separators(tmp_path: Path):
    f = tmp_path / "tickers.txt"
    f.write_text("AAPL, MSFT\n# comment\nVOO TSLA\nSAP.DE   BMW.DE  # inline\n", encoding="utf-8")
    assert _load_tickers_from_file(str(f)) == ["AAPL", "MSFT", "VOO", "TSLA", "SAP.DE", "BMW.DE"]


def test_merge_ticker_lists_dedupes_preserving_order():
    merged = _merge_ticker_lists(["AAPL", "msft"], ["MSFT", "VOO", "aapl", "TSLA"])
    assert merged == ["AAPL", "msft", "VOO", "TSLA"]
