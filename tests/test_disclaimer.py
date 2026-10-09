"""Tests for the tool's legal disclaimer and its decision-support wording.

The disclaimer is one string in one module and every surface must ship it:
a disclaimer that only some outputs carry is worth little, and the HTML
reports are the ones that travel (shared, printed, screenshotted) rather
than a terminal scrollback.

The wording guard at the bottom pins the 0.17.x decision-support framing —
the CLI describes scores and postures, never instructions to buy or sell.
"""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

from rich.console import Console

from investdaytip.backtest import BacktestResult, BacktestSnapshot
from investdaytip.data_source import StockData
from investdaytip.deep_dive import DeepDive, render_rich
from investdaytip.deep_dive import render_html as render_deep_dive_html
from investdaytip.disclaimer import DISCLAIMER_CLI, disclaimer_html
from investdaytip.html_export import export_backtest_html, export_recommendations_html
from investdaytip.main import _render
from investdaytip.scoring import ScoredAsset

_SRC = Path(__file__).resolve().parents[1] / "src" / "investdaytip"

# Phrases that turn a score into an instruction. They may not appear in the
# user-facing CLI modules (disclaimer.py owns the "not advice" wording).
_BANNED_PHRASES = (
    "Buy Recommendations",
    "consider selling",
    "good time to buy",
    "Good entry",
    "Macro signal",
    "🔴 SELL",
    "🟡 HOLD",
    "Selective buying",
    "Prioritize defense",
    "Reduce risk",
    "raise cash",
    "top-scoring picks",
    "portfolio advice",
    "Consistent 12-month",
    "Near-random",
)


def _scored(ticker: str = "AAPL") -> ScoredAsset:
    return ScoredAsset(
        data=StockData(
            ticker=ticker,
            name="Apple Inc.",
            sector="Technology",
            current_price=190.5,
            return_1m=0.03,
            return_12m=0.21,
            currency="USD",
            session_date="2026-10-08",
        ),
        asset_type="STOCK",
        total=88.2,
        breakdown={"Quality": 90, "Value": 75, "Health": 85, "Trend": 88},
        rationale=["ROE of 150%", "P/E of 30.1"],
    )


def _backtest_result() -> BacktestResult:
    return BacktestResult(
        benchmark_ticker="SPY",
        total_snapshots=1,
        cumulative_return=0.1,
        benchmark_cumulative_return=0.05,
        alpha=0.04,
        sharpe=0.6,
        benchmark_sharpe=0.5,
        win_rate_6m=0.5,
        win_rate_12m=0.5,
        max_drawdown=-0.2,
        snapshots=[
            BacktestSnapshot(
                date=datetime(2024, 6, 1),
                picks=[_scored()],
                avg_return_6m=0.08, avg_return_12m=0.22,
                benchmark_return_6m=0.05, benchmark_return_12m=0.18,
            )
        ],
    )


def test_cli_disclaimer_states_it_is_not_advice():
    assert "not investment advice" in DISCLAIMER_CLI
    assert "personalised" in DISCLAIMER_CLI
    assert "Past performance is not indicative of future results" in DISCLAIMER_CLI


def test_report_disclaimer_is_escaped_and_complete():
    footer = disclaimer_html()
    assert footer.startswith('<footer class="disclaimer">')
    assert footer.endswith("</footer>")
    assert "not investment advice" in footer
    assert "personalised" in footer
    assert "&lt;" not in footer  # nothing in the text needs escaping


def test_rankings_table_drops_the_buy_recommendations_wording():
    buf = io.StringIO()
    _render([_scored()], Console(file=buf, force_terminal=False, width=200))
    out = buf.getvalue()
    assert "Long-Term Ratings" in out
    assert "Buy Recommendations" not in out
    # The table carries the disclaimer itself, not only the closing line.
    assert "Disclaimer" in out
    assert "not investment advice" in out


def test_recommendations_report_carries_the_disclaimer(tmp_path: Path):
    out = tmp_path / "report.html"
    export_recommendations_html(
        [_scored()], str(out), top_n=5, asset_class="stocks", tickers=None,
    )
    html = out.read_text(encoding="utf-8")
    assert '<footer class="disclaimer">' in html
    assert "not investment advice" in html
    assert "personalised or tailored recommendation" in html


def test_backtest_report_carries_the_disclaimer(tmp_path: Path):
    out = tmp_path / "backtest.html"
    export_backtest_html(_backtest_result(), str(out))
    html = out.read_text(encoding="utf-8")
    assert '<footer class="disclaimer">' in html
    assert "not investment advice" in html
    # The backtest meta line describes candidates, not picks to act on.
    assert "candidates per snapshot" in html


def test_deep_dive_report_carries_the_disclaimer(tmp_path: Path):
    html = render_deep_dive_html([DeepDive(ticker="AAPL", data=_scored().data)])
    assert '<footer class="disclaimer">' in html
    assert "not investment advice" in html


def test_deep_dive_terminal_report_carries_the_disclaimer():
    buf = io.StringIO()
    render_rich(
        [DeepDive(ticker="AAPL", data=_scored().data)],
        console=Console(file=buf, force_terminal=False, width=200),
    )
    out = buf.getvalue()
    assert "Disclaimer" in out
    assert "not investment advice" in out


def test_deep_dive_terminal_report_omits_the_disclaimer_when_empty():
    buf = io.StringIO()
    render_rich([], console=Console(file=buf, force_terminal=False))
    assert buf.getvalue() == ""


def test_cli_source_never_instructs_a_trade():
    """The CLI describes scores and postures; it never says buy or sell."""
    for name in ("main.py", "advisor.py", "deep_dive.py", "html_export.py", "backtest.py"):
        source = (_SRC / name).read_text(encoding="utf-8")
        for phrase in _BANNED_PHRASES:
            assert phrase not in source, f"{phrase!r} found in {name}"


def _advisor_agent_source() -> str:
    return (_SRC.parents[1] / ".opencode" / "agents" / "advisor.md").read_text(encoding="utf-8")


def _advisor_agent_outside_wording_rules() -> str:
    """The agent with its wording-rules section removed.

    That one section *names* the banned phrases in order to forbid them, so
    scanning it would only ever find its own instructions. Everything else —
    the option menu, the interpretation tables, the presentation format — is
    output the agent can produce verbatim, and is what the scan guards.
    """
    source = _advisor_agent_source()
    start = source.index("## ⚠️ CRITICAL: Report what the model sees")
    end = source.index("\n## ", start)
    return source[:start] + source[end:]


def test_advisor_agent_never_instructs_a_trade():
    """The agent writes prose to one person about their own money — the
    highest-exposure surface of all, so it is held to the same wording."""
    body = _advisor_agent_outside_wording_rules()
    for phrase in _BANNED_PHRASES:
        assert phrase not in body, f"{phrase!r} found outside the wording rules in advisor.md"


def test_advisor_agent_wording_rules_stay_in_place():
    """The rules section is what the exclusion above trades on: it must keep
    naming the banned phrases, or a deleted section would silence the scan."""
    rules = _advisor_agent_source()
    start = rules.index("## ⚠️ CRITICAL: Report what the model sees")
    end = rules.index("\n## ", start)
    section = rules[start:end]
    for phrase in ("consider selling", "good time to buy", "raise cash"):
        assert phrase in section, f"the wording rules no longer name {phrase!r}"


def test_advisor_agent_cites_the_package_disclaimer():
    """It must pull the wording from the package, never write its own."""
    source = _advisor_agent_source()
    assert "from investdaytip.disclaimer import DISCLAIMER_TEXT" in source
    # The old one-liner is gone: it said less than the framing now required.
    assert "quantitative model output only" not in source


def test_disclaimer_text_is_the_cli_wording_without_markup():
    """Both constants must say the same thing — one surface cannot drift."""
    from investdaytip.disclaimer import DISCLAIMER_TEXT

    assert DISCLAIMER_TEXT == DISCLAIMER_CLI.replace("[dim italic]", "").replace("[/dim italic]", "")


def test_advisor_posture_wording_is_descriptive_only():
    """The stored macro action stays an API value; only its wording changes."""
    from investdaytip.advisor import _fmt_action

    assert _fmt_action("buy") == "risk-on"
    assert _fmt_action("hold") == "neutral"
    assert _fmt_action("sell") == "defensive"
    assert _fmt_action("something-else") == "something-else"
