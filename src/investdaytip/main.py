"""CLI entry point for InvestDayTip."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from rich.table import Table

from investdaytip.html_export import export_recommendations_html
from investdaytip.recommender import recommend
from investdaytip.scoring import ScoredAsset


def get_recommendations(
    tickers: list[str] | None = None,
    top_n: int = 5,
    asset_class: str = "all",
    region: str = "all",
) -> list[ScoredAsset]:
    """Programmatic API: return the top ``top_n`` long-term buy recommendations."""
    return recommend(  # type: ignore[arg-type]
        tickers=tickers, top_n=top_n, asset_class=asset_class, region=region,
    )


def _format_breakdown(s: ScoredAsset) -> str:
    return " / ".join(f"{int(round(v))}" for v in s.breakdown.values())


def _breakdown_legend(results: list[ScoredAsset]) -> str:
    seen: dict[str, list[str]] = {}
    for s in results:
        if s.asset_type not in seen:
            seen[s.asset_type] = list(s.breakdown.keys())
    return " · ".join(f"{at}: " + " / ".join(p) for at, p in seen.items())


def _fmt_price(price, currency) -> str:
    if price is None:
        return "—"
    symbol = {"USD": "$", "EUR": "€", "GBP": "£", "GBp": "p", "CHF": "CHF ", "DKK": "kr ",
              "SEK": "kr ", "NOK": "kr "}.get(currency or "", "")
    return f"{symbol}{price:,.2f}" if symbol else f"{price:,.2f} {currency or ''}".strip()


def _fmt_pct(value) -> str:
    if value is None:
        return "—"
    pct = value * 100
    color = "green" if pct >= 0 else "red"
    sign = "+" if pct >= 0 else ""
    return f"[{color}]{sign}{pct:.2f}%[/{color}]"


def _fmt_pe(value) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "—"


def _default_export_html_filename(now: datetime | None = None) -> str:
    ts = (now or datetime.now()).strftime("%Y%m%d-%H%M")
    return f"investDayTip-{ts}.html"


def _load_tickers_from_file(file_path: str) -> list[str]:
    """Load tickers from a text file.

    Accepted separators: newlines, spaces, tabs, or commas.
    Lines may include comments after '#'.
    """
    text = Path(file_path).read_text(encoding="utf-8")
    out: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        for tok in line.replace(",", " ").split():
            t = tok.strip()
            if t:
                out.append(t)
    return out


def _merge_ticker_lists(cli_tickers: list[str] | None, file_tickers: list[str]) -> list[str] | None:
    merged: list[str] = []
    seen: set[str] = set()
    for ticker in (cli_tickers or []) + file_tickers:
        key = ticker.upper()
        if key in seen:
            continue
        seen.add(key)
        merged.append(ticker)
    return merged or None


def _render(results: list[ScoredAsset], console: Console) -> None:
    if not results:
        console.print("[red]No recommendations could be generated.[/red]")
        return

    table = Table(
        title="📈 InvestDayTip — Long-Term Buy Recommendations",
        show_lines=True,
        title_style="bold cyan",
    )
    table.add_column("#", style="bold")
    table.add_column("Type", style="magenta")
    table.add_column("Ticker", style="bold yellow")
    table.add_column("Name")
    table.add_column("Sector/Category", style="dim")
    table.add_column("Price", justify="right")
    table.add_column("P/E", justify="right")
    table.add_column("1M Δ", justify="right")
    table.add_column("1Y Δ", justify="right")
    table.add_column("Score", justify="right", style="bold green")
    table.add_column("Breakdown", justify="right", style="cyan")
    table.add_column("Why", style="white")

    for i, s in enumerate(results, start=1):
        d = s.data
        why = "; ".join(s.rationale[:3]) if s.rationale else "—"
        table.add_row(
            str(i),
            s.asset_type,
            d.ticker,
            d.name or "—",
            d.sector or "—",
            _fmt_price(d.current_price, getattr(d, "currency", None)),
            _fmt_pe(getattr(d, "trailing_pe", None)),
            _fmt_pct(d.return_1m),
            _fmt_pct(d.return_12m),
            f"{s.total:.1f}",
            _format_breakdown(s),
            why,
        )

    console.print(table)
    console.print(f"\n[dim]Breakdown legend — {_breakdown_legend(results)}[/dim]")
    console.print(
        "[dim italic]Disclaimer: This is not financial advice. Do your own research.[/dim italic]"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="investdaytip",
        description="Suggests long-term stock & ETF buy recommendations using multi-factor analysis.",
    )
    parser.add_argument("-n", "--top", type=int, default=5,
                        help="Number of recommendations to return (default: 5).")
    parser.add_argument("-t", "--tickers", nargs="+", default=None,
                        help="Custom ticker list. Defaults to a curated universe.")
    parser.add_argument(
        "--tickers-file",
        default=None,
        help=(
            "Path to a text file with custom tickers (supports lines, spaces, commas; "
            "'#' for comments). Merged with --tickers if both are provided."
        ),
    )
    parser.add_argument("-a", "--asset-class", choices=["all", "stocks", "etfs"], default="all",
                        help="Which asset class to analyze when no -t is given (default: all).")
    parser.add_argument("-r", "--region", choices=["all", "us", "eu", "asia"], default="all",
                        help="Which region to analyze when no -t is given (default: all).")
    parser.add_argument("--workers", type=int, default=10,
                        help="Parallel fetch workers (default: 10).")
    parser.add_argument(
        "--export-html",
        nargs="?",
        const="",
        default=None,
        help=(
            "Export recommendations to a self-contained HTML file with client-side filters. "
            "If PATH is omitted, defaults to investDayTip-aaaammdd-hhmm.html."
        ),
    )
    args = parser.parse_args(argv)

    file_tickers: list[str] = []
    if args.tickers_file:
        try:
            file_tickers = _load_tickers_from_file(args.tickers_file)
        except Exception as exc:
            Console().print(f"[red]Could not read --tickers-file: {exc}[/red]")
            return 1

    effective_tickers = _merge_ticker_lists(args.tickers, file_tickers)

    console = Console()
    console.print(
        f"[bold cyan]InvestDayTip[/bold cyan] — analyzing markets "
        f"([italic]{args.asset_class} · {args.region}[/italic])...\n"
    )

    results: list[ScoredAsset] = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        task_id = progress.add_task("Fetching market data", total=None)

        def cb(done: int, total: int, ticker: str) -> None:
            progress.update(task_id, total=total, completed=done, description=f"Analyzed {ticker}")

        try:
            results = recommend(
                tickers=effective_tickers,
                top_n=args.top,
                max_workers=args.workers,
                asset_class=args.asset_class,
                region=args.region,
                progress_cb=cb,
            )
        except Exception as exc:
            console.print(f"[red]Error during analysis: {exc}[/red]")
            return 1

    _render(results, console)

    if args.export_html is not None:
        try:
            destination = args.export_html or _default_export_html_filename()
            out_path = export_recommendations_html(
                results,
                destination,
                top_n=args.top,
                asset_class=args.asset_class,
                region=args.region,
                tickers=effective_tickers,
                tickers_file=args.tickers_file,
            )
            console.print(f"[green]HTML report exported:[/green] {out_path}")
        except Exception as exc:
            console.print(f"[red]Failed to export HTML report: {exc}[/red]")
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
