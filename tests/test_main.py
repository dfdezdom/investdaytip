"""Tests for CLI helper utilities."""

from datetime import datetime
from pathlib import Path

from investdaytip.data_source import StockData
from investdaytip.main import (
    _default_export_html_filename,
    _load_tickers_from_file,
    _merge_ticker_lists,
    _parse_min_market_cap,
    _session_note,
)
from investdaytip.scoring import ScoredAsset


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


# ── Session labelling of the CLI table ──────────────────────────────────────


def _scored(ticker: str, session_date: str | None) -> ScoredAsset:
    return ScoredAsset(
        data=StockData(ticker=ticker, session_date=session_date),
        asset_type="STOCK",
        total=70.0,
    )


def test_session_note_names_the_session_the_figures_describe():
    # At 13:01 ET on 2026-10-09 SEZL showed $117.22 · +2.96% (the Oct-8 close
    # and its change) while it traded at 126.68, +8.07%: the note has to say
    # which session that day's change belongs to.
    note = _session_note([_scored("SEZL", "2026-10-08")])

    assert note is not None
    assert "2026-10-08" in note
    assert "live quote is not scored" in note


def test_session_note_joins_every_session_present():
    note = _session_note([_scored("A", "2026-10-07"), _scored("B", "2026-10-08")])

    assert note is not None
    assert note.startswith("Sessions: 2026-10-07, 2026-10-08")


def test_session_note_is_silent_without_a_session():
    assert _session_note([_scored("SEZL", None)]) is None
    assert _session_note([]) is None


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
