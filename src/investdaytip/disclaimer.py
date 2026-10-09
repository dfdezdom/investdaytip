"""Single source of truth for the tool's legal disclaimer.

Every surface — the rankings table, the advisor, ``deep-dive`` and every
exported HTML report — pulls its wording from here.  A disclaimer that only
some outputs carry is worth little, and the HTML reports are the ones that
travel: they get shared, printed and screenshotted far more often than a
terminal scrollback.

Nothing in this module may be shortened per surface.  If a sentence does not
fit, it is the surface that changes (a wrapped footer), not the text.
"""

from __future__ import annotations

from html import escape

# Terminal: Rich markup, two sentences that still read on an 80-column console.
DISCLAIMER_CLI = (
    "[dim italic]Disclaimer: InvestDayTip is a research and decision-support "
    "tool — not investment advice, and not a personalised recommendation. "
    "Scores and backtests are model output over public market data; they know "
    "nothing about your objectives, circumstances or risk tolerance. Past "
    "performance is not indicative of future results. Capital at risk — do "
    "your own research.[/dim italic]"
)

# The same framing without Rich markup, for surfaces that cannot strip it —
# the advisor agent's markdown, a README quote, a log line.
DISCLAIMER_TEXT = (
    "Disclaimer: InvestDayTip is a research and decision-support tool — not "
    "investment advice, and not a personalised recommendation. Scores and "
    "backtests are model output over public market data; they know nothing "
    "about your objectives, circumstances or risk tolerance. Past performance "
    "is not indicative of future results. Capital at risk — do your own "
    "research."
)

# Reports: the same framing, complete enough to stand on its own in a file
# that outlives the session that produced it.
_DISCLAIMER_TEXT = (
    "Disclaimer — InvestDayTip is a research and decision-support tool. It is "
    "not investment advice and does not constitute a personalised or tailored "
    "recommendation: the scores, labels and backtests in this report are model "
    "output derived from public market data, and they take no account of your "
    "objectives, financial situation or risk tolerance. Backtests are "
    "hypothetical simulations over historical data — past performance is not "
    "indicative of future results. All investing carries risk, including the "
    "loss of capital. Do your own research, and consult a licensed advisor "
    "before acting on any figure here. Market data by Yahoo Finance (yfinance), "
    "Financial Modeling Prep and StockFit; third-party names are used for "
    "identification only and imply no affiliation."
)


def disclaimer_html() -> str:
    """The report disclaimer as a ready-to-inline ``<footer>`` block.

    Escaped here rather than at each call site so no export can ship the text
    half-escaped.
    """
    return f'<footer class="disclaimer">{escape(_DISCLAIMER_TEXT)}</footer>'
