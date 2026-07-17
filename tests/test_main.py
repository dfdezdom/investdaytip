"""Tests for CLI helper utilities."""

from datetime import datetime
from pathlib import Path

from investdaytip.main import (
    _default_export_html_filename,
    _load_tickers_from_file,
    _merge_ticker_lists,
    _parse_min_market_cap,
)


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


def test_parse_min_market_cap_billion():
    assert _parse_min_market_cap("1B") == 1_000_000_000


def test_parse_min_market_cap_billion_lowercase():
    assert _parse_min_market_cap("2.5b") == 2_500_000_000


def test_parse_min_market_cap_million():
    assert _parse_min_market_cap("500M") == 500_000_000


def test_parse_min_market_cap_thousand():
    assert _parse_min_market_cap("100K") == 100_000


def test_parse_min_market_cap_plain_float():
    assert _parse_min_market_cap("1000000") == 1_000_000


def test_parse_min_market_cap_zero():
    assert _parse_min_market_cap("0") == 0.0


# ── Advisor subcommand flags ────────────────────────────────────────────────


def test_advisor_subcommand_accepts_data_source_flags(mocker):
    """`investdaytip advisor --data-source fmp -n 5` must reach advisor_main.

    main() calls parser.parse_args() before dispatching, so the adv subparser
    must accept every flag advisor_main's own parser defines (AGENTS.md
    documents these invocations).
    """
    import investdaytip.advisor as adv
    from investdaytip.main import main

    fake = mocker.patch.object(adv, "advisor_main", return_value=0)
    rc = main(["advisor", "--data-source", "fmp", "-n", "5", "--include-technical"])
    assert rc == 0
    fake.assert_called_once_with(["--data-source", "fmp", "-n", "5", "--include-technical"])


def test_advisor_subcommand_accepts_no_include_technical(mocker):
    import investdaytip.advisor as adv
    from investdaytip.main import main

    fake = mocker.patch.object(adv, "advisor_main", return_value=0)
    rc = main(["advisor", "--data-source", "yahooquery", "--no-include-technical"])
    assert rc == 0
    fake.assert_called_once_with(["--data-source", "yahooquery", "--no-include-technical"])
