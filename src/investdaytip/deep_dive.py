"""Per-ticker deep-dive report (product Fase 2).

Combines three sources into one report per ticker:

1. the live InvestDayTip score (``fetch_asset`` + ``score_stock``);
2. StockFit's aggregated ``company/research-summary`` (Starter tier) —
   omitted gracefully when no ``STOCKFIT_API_KEY`` is set or the fetch fails;
3. **keyless local diagnostics**: Piotroski F-Score (9 checks) and Altman
   Z-Score + zone, computed from the ticker's own annual statements.

Piotroski/Altman are **informative only — never scored** (validated and
rejected as scoring factors, see AGENTS.md).  The DCF link-out points to
StockFit's public site: as of 2026-09-27 the valuation platform is in early
access (no per-ticker routes exist yet), so a deep-link is deferred until
the app launches — upgrade ``DCF_URL`` then.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from rich.console import Console
from rich.table import Table

from investdaytip.data_source import (
    AssetData,
    StockData,
    fetch_asset,
    fetch_cash_flow_frame,
    fetch_statement_frames,
)
from investdaytip.data_source_stockfit import ResearchSummary, fetch_research_summary
from investdaytip.financial_health import (
    AltmanResult,
    PiotroskiResult,
    altman_z_score,
    annual_facts,
    piotroski_f_score,
)
from investdaytip.scoring import ScoredAsset, resolve_include_technical, score_stock

# StockFit's DCF model lives in their web platform, which is in early access
# (2026-09-27: only the landing page is public — no per-ticker routes).  When
# the app launches this can become a deep link like https://www.stockfit.io/dcf/{ticker}.
DCF_URL = (
    "https://www.stockfit.io"
    "?utm_source=investdaytip&utm_medium=referral&utm_campaign=deep-dive-dcf"
)
DCF_LABEL = "Modelo DCF y workspace en la plataforma StockFit (early access)"


@dataclass
class DeepDive:
    """Everything the report shows for one ticker."""

    ticker: str
    data: Optional[AssetData] = None
    score: Optional[ScoredAsset] = None
    research: Optional[ResearchSummary] = None
    piotroski: Optional[PiotroskiResult] = None
    altman: Optional[AltmanResult] = None
    errors: list[str] = field(default_factory=list)


def build_deep_dive(
    ticker: str,
    scoring_model: str = "quant",
    include_technical: Optional[bool] = None,
) -> DeepDive:
    """Gather score, StockFit research summary and local diagnostics."""
    dd = DeepDive(ticker=ticker)

    dd.data = fetch_asset(ticker)
    if dd.data.errors:
        dd.errors.extend(dd.data.errors)

    if isinstance(dd.data, StockData):
        try:
            include_technical = resolve_include_technical(include_technical, scoring_model)
            dd.score = score_stock(
                dd.data, model=scoring_model,
                include_technical=include_technical, si_data={},
            )
        except Exception as exc:  # pragma: no cover - defensive
            dd.errors.append(f"scoring failed: {exc}")
    else:
        dd.errors.append("deep-dive supports stocks only")

    # Local diagnostics from the ticker's own annual statements (keyless).
    try:
        income, balance = fetch_statement_frames(ticker)
        cash = fetch_cash_flow_frame(ticker)
        if income is not None and balance is not None:
            cur = annual_facts(income, balance, cash, datetime.now())
            prev = annual_facts(income, balance, cash, datetime.now(), years_back=1)
            dd.piotroski = piotroski_f_score(cur, prev)
            dd.altman = altman_z_score(cur, dd.data.market_cap)
    except Exception as exc:  # pragma: no cover - defensive
        dd.errors.append(f"health diagnostics failed: {exc}")

    # StockFit research summary — opt-in enrichment via the API key.
    if os.environ.get("STOCKFIT_API_KEY"):
        try:
            dd.research = fetch_research_summary(ticker)
        except Exception as exc:  # pragma: no cover - defensive
            dd.errors.append(f"research summary failed: {exc}")

    return dd


# ── Rich rendering ───────────────────────────────────────────────────────────


# StockFit reports margins/returns as percent numbers (40.31 = 40.31%) but
# growth rates as decimals (0.1779 = 17.79%).  Normalize to decimals once so
# every renderer can use the same pct formatting.
_SNAP_PERCENT_FORM = (
    "grossMargin", "operatingMargin", "netMargin",
    "roe", "roic", "fcfToNetIncome",
)


def _norm_snap(snapshot: dict[str, Any]) -> dict[str, Any]:
    out = dict(snapshot)
    for key in _SNAP_PERCENT_FORM:
        value = out.get(key)
        if isinstance(value, (int, float)):
            out[key] = value / 100.0
    return out


def _fmt(value: Optional[float], pct: bool = False, money: bool = False) -> str:
    if value is None:
        return "—"
    if money:
        if abs(value) >= 1e9:
            return f"${value / 1e9:.1f}B"
        if abs(value) >= 1e6:
            return f"${value / 1e6:.1f}M"
        return f"${value:,.0f}"
    if pct:
        return f"{value * 100:.1f}%"
    return f"{value:.2f}"


def render_rich(items: list[DeepDive], console: Optional[Console] = None) -> None:
    """Print the deep-dive report to the terminal."""
    console = console or Console()

    for dd in items:
        name = dd.data.name if dd.data and dd.data.name else ""
        header = f"[bold]{dd.ticker}[/bold]" + (f" — {name}" if name else "")
        console.print()
        console.print(header)

        if dd.score is not None:
            parts = " · ".join(f"{k} {v:.0f}" for k, v in dd.score.breakdown.items())
            console.print(f"  Score: [bold]{dd.score.total:.1f}[/bold]  |  {parts}")

        snap = _norm_snap((dd.research.snapshot if dd.research else {}) or {})
        if snap:
            t = Table(show_header=False, box=None, padding=(0, 2))
            t.add_row("EPS", _fmt(snap.get("eps")),
                      "Net margin", _fmt(snap.get("netMargin"), pct=True))
            t.add_row("Revenue", _fmt(snap.get("revenue"), money=True),
                      "Net income", _fmt(snap.get("netIncome"), money=True))
            t.add_row("Gross margin", _fmt(snap.get("grossMargin"), pct=True),
                      "Operating margin", _fmt(snap.get("operatingMargin"), pct=True))
            t.add_row("ROE", _fmt(snap.get("roe"), pct=True),
                      "ROIC", _fmt(snap.get("roic"), pct=True))
            t.add_row("FCF", _fmt(snap.get("freeCashFlow"), money=True),
                      "FCF/NI", _fmt(snap.get("fcfToNetIncome"), pct=True))
            t.add_row("Revenue growth", _fmt(snap.get("revenueGrowth"), pct=True),
                      "EPS growth", _fmt(snap.get("epsGrowth"), pct=True))
            console.print("  [underline]Earnings snapshot (StockFit)[/underline]")
            console.print(t)
            if snap.get("nextEarningsDate"):
                console.print(f"  Next earnings: {snap['nextEarningsDate']}"
                              + (f"  ·  next filing: {snap['nextFilingDate']}"
                                 if snap.get("nextFilingDate") else ""))
        elif not os.environ.get("STOCKFIT_API_KEY"):
            console.print("  [dim]StockFit research summary omitted (no STOCKFIT_API_KEY)[/dim]")
        else:
            console.print("  [dim]StockFit research summary unavailable[/dim]")

        if dd.piotroski is not None:
            checks = " ".join(
                f"[green]✓[/green]{k}" if v else f"[red]✗[/red]{k}"
                for k, v in dd.piotroski.checks.items()
            )
            console.print(f"  [underline]Piotroski F-Score (diagnostic)[/underline] "
                          f"[bold]{dd.piotroski.score}/9[/bold]")
            console.print(f"  {checks}")
        if dd.altman is not None:
            color = {"safe": "green", "grey": "yellow", "distress": "red"}[dd.altman.zone]
            console.print(f"  [underline]Altman Z (diagnostic)[/underline] "
                          f"[bold]{dd.altman.z_score:.2f}[/bold]  "
                          f"[{color}]{dd.altman.zone}[/{color}]")
        if dd.piotroski is None and dd.altman is None:
            console.print("  [dim]Health diagnostics unavailable (no statements)[/dim]")

        console.print(f"  [underline]DCF valuation[/underline] {DCF_LABEL}")
        console.print(f"  [dim]{DCF_URL.split('?')[0]}[/dim]")

        for err in dd.errors:
            console.print(f"  [red]⚠ {err}[/red]")


# ── HTML rendering ───────────────────────────────────────────────────────────


def _h(value: Any) -> str:
    """HTML-escape a value."""
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _hfmt(value: Optional[float], pct: bool = False, money: bool = False) -> str:
    if value is None:
        return '<span class="muted">—</span>'
    return _h(_fmt(value, pct=pct, money=money))


def render_html(items: list[DeepDive], generated_at: Optional[datetime] = None) -> str:
    """Self-contained HTML deep-dive report (one section per ticker)."""
    ts = (generated_at or datetime.now()).strftime("%Y-%m-%d %H:%M")
    sections: list[str] = []
    for dd in items:
        name = dd.data.name if dd.data and dd.data.name else ""
        rows: list[str] = []

        if dd.score is not None:
            factors = "".join(
                f"<li><strong>{_h(k)}</strong> {v:.0f}</li>"
                for k, v in dd.score.breakdown.items()
            )
            rows.append(
                f'<div class="block"><h3>InvestDayTip score</h3>'
                f'<p class="score">{dd.score.total:.1f}</p>'
                f'<ul class="factors">{factors}</ul></div>'
            )

        snap = _norm_snap((dd.research.snapshot if dd.research else {}) or {})
        if snap:
            pairs = [
                ("EPS", _hfmt(snap.get("eps"))),
                ("Revenue", _hfmt(snap.get("revenue"), money=True)),
                ("Net income", _hfmt(snap.get("netIncome"), money=True)),
                ("Gross margin", _hfmt(snap.get("grossMargin"), pct=True)),
                ("Operating margin", _hfmt(snap.get("operatingMargin"), pct=True)),
                ("Net margin", _hfmt(snap.get("netMargin"), pct=True)),
                ("ROE", _hfmt(snap.get("roe"), pct=True)),
                ("ROIC", _hfmt(snap.get("roic"), pct=True)),
                ("FCF", _hfmt(snap.get("freeCashFlow"), money=True)),
                ("FCF / NI", _hfmt(snap.get("fcfToNetIncome"), pct=True)),
                ("Revenue growth", _hfmt(snap.get("revenueGrowth"), pct=True)),
                ("EPS growth", _hfmt(snap.get("epsGrowth"), pct=True)),
            ]
            cells = "".join(
                f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in pairs
            )
            extra = ""
            if snap.get("nextEarningsDate"):
                extra = (f'<p class="muted">Next earnings: {_h(snap["nextEarningsDate"])}'
                         + (f' · next filing: {_h(snap["nextFilingDate"])}'
                            if snap.get("nextFilingDate") else "")
                         + "</p>")
            rows.append(
                f'<div class="block"><h3>Earnings snapshot (StockFit)</h3>'
                f'<table>{cells}</table>{extra}</div>'
            )
        else:
            reason = ("no STOCKFIT_API_KEY" if not os.environ.get("STOCKFIT_API_KEY")
                      else "fetch failed")
            rows.append(
                f'<div class="block"><h3>Earnings snapshot (StockFit)</h3>'
                f'<p class="muted">omitted — {_h(reason)}</p></div>'
            )

        if dd.piotroski is not None:
            checks = "".join(
                f'<li class="{"pass" if v else "fail"}">'
                f'{"✓" if v else "✗"} {_h(k)}</li>'
                for k, v in dd.piotroski.checks.items()
            )
            rows.append(
                f'<div class="block"><h3>Piotroski F-Score '
                f'<span class="muted">(diagnostic — not scored)</span></h3>'
                f'<p class="score">{dd.piotroski.score}/9</p>'
                f'<ul class="checks">{checks}</ul></div>'
            )
        if dd.altman is not None:
            rows.append(
                f'<div class="block"><h3>Altman Z-Score '
                f'<span class="muted">(diagnostic — not scored)</span></h3>'
                f'<p class="score">{dd.altman.z_score:.2f} '
                f'<span class="zone-{_h(dd.altman.zone)}">{_h(dd.altman.zone)}</span></p></div>'
            )
        rows.append(
            f'<div class="block"><h3>DCF valuation</h3>'
            f'<p><a href="{_h(DCF_URL)}" target="_blank" rel="noopener">'
            f'{_h(DCF_LABEL)} ↗</a></p></div>'
        )

        err_html = "".join(f'<p class="err">⚠ {_h(e)}</p>' for e in dd.errors)
        sections.append(
            f'<section><h2>{_h(dd.ticker)}'
            + (f' <span class="muted">— {_h(name)}</span>' if name else "")
            + f"</h2>{''.join(rows)}{err_html}</section>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>InvestDayTip — Deep Dive</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       margin: 2rem auto; max-width: 60rem; padding: 0 1rem; color: #1a1a2e; }}
h1 {{ font-size: 1.5rem; }} h2 {{ border-bottom: 2px solid #e0e0e0; padding-bottom: .3rem; }}
h3 {{ margin-bottom: .3rem; font-size: .95rem; text-transform: uppercase; letter-spacing: .03em; color: #555; }}
section {{ margin: 2rem 0; }}
.block {{ margin: 1rem 0; padding: .8rem 1rem; background: #f7f8fa; border-radius: 8px; }}
table {{ border-collapse: collapse; width: 100%; }}
td {{ padding: .25rem .6rem; border-bottom: 1px solid #e6e8eb; }}
td:first-child {{ color: #555; }}
.score {{ font-size: 1.8rem; font-weight: 700; margin: .2rem 0; }}
.factors, .checks {{ list-style: none; padding: 0; display: flex; flex-wrap: wrap; gap: .5rem; }}
.factors li, .checks li {{ background: #fff; border: 1px solid #e0e0e0; border-radius: 6px; padding: .2rem .6rem; }}
.checks .pass {{ border-color: #2e9e5b; color: #2e9e5b; }}
.checks .fail {{ border-color: #c94a4a; color: #c94a4a; }}
.zone-safe {{ color: #2e9e5b; font-weight: 600; }}
.zone-grey {{ color: #c98a2e; font-weight: 600; }}
.zone-distress {{ color: #c94a4a; font-weight: 600; }}
.muted {{ color: #8a8f98; font-weight: 400; font-size: .85em; }}
.err {{ color: #c94a4a; }}
</style>
</head>
<body>
<h1>InvestDayTip — Deep Dive</h1>
<p class="muted">Generated {ts} · Piotroski/Altman are informative diagnostics, never scored.</p>
{''.join(sections)}
</body>
</html>
"""
