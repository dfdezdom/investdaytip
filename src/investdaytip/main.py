"""CLI entry point for InvestDayTip."""

from __future__ import annotations

import argparse
import sys

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from rich.table import Table

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
    parser.add_argument("-a", "--asset-class", choices=["all", "stocks", "etfs"], default="all",
                        help="Which asset class to analyze when no -t is given (default: all).")
    parser.add_argument("-r", "--region", choices=["all", "us", "eu", "asia"], default="all",
                        help="Which region to analyze when no -t is given (default: all).")
    parser.add_argument("--workers", type=int, default=10,
                        help="Parallel fetch workers (default: 10).")
    args = parser.parse_args(argv)

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
                tickers=args.tickers,
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
