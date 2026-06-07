"""CLI entry point for InvestDayTip."""

from __future__ import annotations

import argparse
import importlib.metadata
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    import argcomplete
except ModuleNotFoundError:
    argcomplete = None  # type: ignore[assignment]
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from investdaytip.backtest import BacktestResult
from investdaytip.dataroma import fetch_superinvestor_universe, get_superinvestor_data
from investdaytip.html_export import export_backtest_html, export_recommendations_html
from investdaytip.recommender import recommend
from investdaytip.scoring import ScoredAsset

logger = logging.getLogger(__name__)


def get_recommendations(
    tickers: list[str] | None = None,
    top_n: int = 5,
    asset_class: str = "all",
    region: str | list[str] = "all",
    currency: str | list[str] = "all",
    min_market_cap: float = 2_000_000_000,
) -> list[ScoredAsset]:
    """Programmatic API: return the top ``top_n`` long-term buy recommendations."""
    return recommend(
        tickers=tickers, top_n=top_n, asset_class=asset_class,
        region=region, currency=currency, min_market_cap=min_market_cap,
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
    symbol = {"USD": "$", "EUR": "€", "GBP": "£", "GBp": "p", "CHF": "CHF ", "JPY": "¥",
              "DKK": "kr ", "SEK": "kr ", "NOK": "kr "}.get(currency or "", "")
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


def _export_filename_tag_from_tickers_file(tickers_file: str) -> str | None:
    stem = Path(tickers_file).stem.lower()
    candidates = [tok for tok in re.split(r"[^a-z0-9]+", stem) if tok]
    stopwords = {
        "relevant", "relevante", "relevantes", "tickers", "ticker",
        "file", "files", "examples",
    }
    keywords = [tok for tok in candidates if tok not in stopwords]
    if keywords:
        return keywords[0]
    return stem or None


def _default_export_html_filename(now: datetime | None = None, tickers_file: str | None = None) -> str:
    ts = (now or datetime.now()).strftime("%Y%m%d-%H%M")
    if tickers_file:
        tag = _export_filename_tag_from_tickers_file(tickers_file)
        if tag:
            return f"investDayTip-{tag}-{ts}.html"
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


def _parse_min_market_cap(raw: str) -> float:
    """Parse ``1B``, ``500M``, ``2B`` into float. Falls back to plain float."""
    suffixes = {"B": 1_000_000_000, "M": 1_000_000, "K": 1_000}
    s = raw.strip().upper()
    if s and s[-1] in suffixes:
        return float(s[:-1]) * suffixes[s[-1]]
    return float(raw)


def _render(results: list[ScoredAsset], console: Console, include_superinvestor: bool = False, include_technical: bool = False) -> None:
    if not results:
        logger.error("No recommendations could be generated.")
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
    table.add_column("% Today", justify="right")
    table.add_column("P/E", justify="right")
    table.add_column("1M Δ", justify="right")
    table.add_column("1Y Δ", justify="right")
    if include_superinvestor:
        table.add_column("Sup.", justify="right", style="bold")
    if include_technical:
        table.add_column("RSI", justify="right")
        table.add_column("MACD", justify="right")
    table.add_column("Score", justify="right", style="bold green")
    table.add_column("Breakdown", justify="right", style="cyan")
    table.add_column("Why", style="white")

    for i, s in enumerate(results, start=1):
        d = s.data
        why = "; ".join(s.rationale[:3]) if s.rationale else "—"
        row = [
            str(i),
            s.asset_type,
            d.ticker,
            d.name or "—",
            d.sector or "—",
            _fmt_price(d.current_price, getattr(d, "currency", None)),
            _fmt_pct(d.daily_change),
            _fmt_pe(getattr(d, "trailing_pe", None)),
            _fmt_pct(d.return_1m),
            _fmt_pct(d.return_12m),
        ]
        if include_superinvestor:
            row.append(f"{s.superinvestor_count}" if s.superinvestor_count is not None else "—")
        if include_technical:
            rsi_val = getattr(d, "rsi_14", None)
            macd_val = getattr(d, "macd_histogram", None)
            row.append(f"{rsi_val:.1f}" if rsi_val is not None else "—")
            row.append(f"{macd_val*100:.2f}%" if macd_val is not None else "—")
        row.extend([
            f"{s.total:.1f}",
            _format_breakdown(s),
            why,
        ])
        table.add_row(*row)

    console.print(table)
    console.print(f"\n[dim]Breakdown legend — {_breakdown_legend(results)}[/dim]")
    console.print(
        "[dim italic]Disclaimer: This is not financial advice. Do your own research.[/dim italic]"
    )


def _default_backtest_html_filename(now: datetime | None = None) -> str:
    ts = (now or datetime.now()).strftime("%Y%m%d-%H%M")
    return f"backtest-{ts}.html"


def _run_backtest_cli(args: argparse.Namespace) -> int:
    """Execute the ``backtest`` subcommand."""
    from investdaytip.backtest import run_backtest
    from investdaytip.recommender import _build_universe

    console = Console()
    region_str = ", ".join(args.region) if isinstance(args.region, list) else args.region
    console.print(
        f"[bold cyan]InvestDayTip Backtest[/bold cyan] — "
        f"region [italic]{region_str}[/italic], "
        f"top [italic]{args.top}[/italic], "
        f"period [italic]{args.period}[/italic] "
        f"([italic]stocks only[/italic])...\n"
    )

    from investdaytip.cache import clear_cache
    if args.cache_clear:
        clear_cache()
    if args.no_cache:
        from investdaytip.cache import set_enabled
        set_enabled(False)

    # Resolve universe for progress bar count
    region = args.region[0] if isinstance(args.region, list) else args.region
    currency = args.currency[0] if isinstance(args.currency, list) else args.currency
    if args.tickers:
        all_tickers = list(set(args.tickers))
    else:
        universe = _build_universe(None, "stocks", region, currency)
        all_tickers = list(universe)
    benchmark_hint = args.benchmark or (
        {"us": "SPY", "eu": "VGK", "asia": "AAXJ"}.get(region, "SPY")
    )
    total_tickers = len(set(all_tickers + [benchmark_hint]))

    with Progress(
        SpinnerColumn(),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("[cyan]Fetching data...", total=total_tickers)

        def on_progress(ticker: str, done: int, _total: int) -> None:
            progress.update(task, completed=done, description=f"[cyan]{ticker}")

        result = run_backtest(
            tickers=args.tickers,
            top_n=args.top,
            period=args.period,
            interval_months=args.interval_months,
            benchmark=args.benchmark,
            region=region,
            currency=currency,
            min_market_cap=args.min_market_cap,
            reporting_lag_days=args.lag_days,
            max_workers=args.max_workers,
            on_progress=on_progress,
            include_technical=args.include_technical,
        )

    # ── Console summary ──
    _render_backtest_result(console, result)

    # ── HTML export ──
    if args.export_html is not None:
        try:
            destination = args.export_html or _default_backtest_html_filename()
            export_backtest_html(
                result,
                destination,
                tickers=args.tickers,
                top_n=args.top,
                region=region_str,
                interval_months=args.interval_months,
            )
            logger.info("Backtest HTML report exported: %s", destination)
            console.print(f"[green]HTML report exported:[/green] {destination}")
        except Exception as exc:
            logger.error("Failed to export backtest HTML report: %s", exc)
            console.print(f"[red]Failed to export HTML report: {exc}[/red]")
            return 1

    return 0


def _render_backtest_result(console: Console, result: BacktestResult) -> None:
    """Print a compact backtest result summary."""
    from rich.table import Table

    from investdaytip.backtest import _benchmark_label, _interpret_backtest

    if result.errors:
        for e in result.errors:
            logger.warning("Backtest warning: %s", e)
        console.print("[yellow]Warnings:[/yellow]")
        for e in result.errors:
            console.print(f"  [dim]{e}[/dim]")

    if result.total_snapshots == 0:
        logger.warning("No snapshots were generated.")
        console.print("[red]No snapshots were generated.[/red]")
        return

    table = Table(title="Backtest Summary", show_lines=True, title_style="bold cyan")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")
    table.add_column("Explanation", style="dim")

    table.add_row("Snapshots", f"{result.total_snapshots}", "Evaluation periods")
    table.add_row(
        "Cumulative Return",
        f"{result.cumulative_return * 100:.2f}%",
        "Total compounded return of the strategy",
    )
    table.add_row(
        "Benchmark Return",
        f"{result.benchmark_cumulative_return * 100:.2f}%",
        f"Total return of {_benchmark_label(result.benchmark_ticker) if result.benchmark_ticker else 'benchmark'}",
    )
    table.add_row(
        "Alpha",
        f"{result.alpha * 100:.2f}%",
        "Excess return vs benchmark (annualized)",
    )
    table.add_row(
        "Sharpe",
        f"{result.sharpe:.2f}",
        "Risk-adjusted return of the strategy",
    )
    table.add_row(
        "Benchmark Sharpe",
        f"{result.benchmark_sharpe:.2f}",
        "Risk-adjusted return of the benchmark",
    )
    table.add_row(
        "Win Rate 6M",
        f"{result.win_rate_6m * 100:.1f}%",
        "% of periods strategy beat benchmark at 6 months",
    )
    table.add_row(
        "Win Rate 12M",
        f"{result.win_rate_12m * 100:.1f}%",
        "% of periods strategy beat benchmark at 12 months",
    )
    table.add_row(
        "Max Drawdown",
        f"{result.max_drawdown * 100:.2f}%",
        "Largest peak-to-trough decline",
    )
    console.print(table)

    console.print(f"\n[italic]{_interpret_backtest(result)}[/italic]")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    parser = argparse.ArgumentParser(
        prog="investdaytip",
        description="Suggests long-term stock & ETF buy recommendations using multi-factor analysis.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"investdaytip v{importlib.metadata.version('investdaytip')}",
    )
    sub = parser.add_subparsers(dest="command", required=False)

    adv = sub.add_parser("advisor", help="Market analysis and portfolio advice.")
    adv.add_argument(
        "--risk",
        choices=["conservative", "moderate", "aggressive"],
        help="Risk profile (interactive if omitted)",
    )
    adv.add_argument(
        "--portfolio",
        default="portfolios/portfolio.txt",
        help="Portfolio ticker file (default: portfolios/portfolio.txt)",
    )
    adv.add_argument("-a", "--asset-class", metavar="TYPE",
                     choices=["all", "stocks", "etfs"], default=None,
                     help="Asset class: all, stocks, etfs (interactive if omitted).")
    adv.add_argument("-r", "--region", metavar="REG", nargs="+",
                     choices=["all", "us", "eu", "asia", "superinvestor"], default=None,
                     help="Region(s): all, us, eu, asia, superinvestor (interactive if omitted).")
    adv.add_argument(
        "-c", "--currency", metavar="CUR", nargs="+",
        choices=["all", "USD", "EUR", "GBP", "CHF", "JPY", "HKD", "INR",
                 "KRW", "TWD", "SGD", "AUD", "DKK", "SEK", "NOK", "GBp"],
        default=None,
        help="Currency: all, USD, EUR, GBP, CHF, JPY, HKD, INR, KRW, TWD, SGD, AUD, DKK, SEK, NOK, GBp (interactive if omitted).",
    )
    adv.add_argument("--min-market-cap", metavar="CAP", type=_parse_min_market_cap, default=2_000_000_000,
                     help="Minimum market cap (default: 2B).")
    adv.add_argument("-s", "--sector", metavar="SECTOR", default=None,
                     help="Sector/category prefix filter (case-insensitive).")
    adv.add_argument("--superinvestor", action="store_true",
                     help="Include superinvestor ownership data.")
    adv.add_argument("--no-cache", action="store_true",
                     help="Bypass SQLite cache.")
    adv.add_argument("--cache-clear", action="store_true",
                     help="Clear all cached data.")

    bt = sub.add_parser("backtest", help="Historical backtest of the scoring model (stocks only).")
    bt.add_argument("-n", "--top", type=int, default=10,
                    help="Top N picks per snapshot (default: 10).")
    bt.add_argument("-t", "--tickers", nargs="+", default=None,
                    help="Custom ticker list.")
    bt.add_argument("-r", "--region", metavar="REG", nargs="+",
                    choices=["all", "us", "eu", "asia", "superinvestor"], default="us",
                    help="Region (default: us).")
    bt.add_argument("-c", "--currency", metavar="CUR", nargs="+",
                    choices=["all", "USD", "EUR", "GBP", "CHF", "JPY", "HKD", "INR",
                             "KRW", "TWD", "SGD", "AUD", "DKK", "SEK", "NOK", "GBp"],
                    default="USD",
                    help="Currency (default: USD).")
    bt.add_argument("--period", default="5y",
                    help="Yfinance lookback period (default: 5y).")
    bt.add_argument("--interval-months", type=int, default=3,
                    help="Months between snapshots (default: 3).")
    bt.add_argument("--lag-days", type=int, default=60,
                    help="Reporting lag in days (default: 60).")
    bt.add_argument("--min-market-cap", metavar="CAP", type=_parse_min_market_cap, default=2_000_000_000,
                    help="Minimum market cap (default: 2B). See Market Cap Classification in README for reference ranges.")
    bt.add_argument("--benchmark", default=None,
                    help="Benchmark ticker (default: auto from region).")
    bt.add_argument("--export-html", nargs="?", const="", default=None,
                    help="Export to self-contained HTML file.")
    bt.add_argument("--no-cache", action="store_true",
                    help="Bypass SQLite cache.")
    bt.add_argument("--cache-clear", action="store_true",
                    help="Clear all cached data.")
    bt.add_argument("--max-workers", type=int, default=10,
                    help="Parallel fetch workers (default: 10).")
    bt.add_argument("--include-technical", action="store_true",
                    help="Include RSI and MACD in scoring.")

    main_grp = parser.add_argument_group("Main options")
    main_grp.add_argument("-n", "--top", type=int, default=5,
                          help="Number of recommendations (default: 5).")
    main_grp.add_argument("-t", "--tickers", nargs="+", default=None,
                          help="Custom ticker list.")
    main_grp.add_argument(
        "--tickers-file",
        default=None,
        help="Text file with custom tickers (lines/spaces/commas; # comments).",
    )
    main_grp.add_argument(
        "--export-html",
        nargs="?",
        const="",
        default=None,
        help="Export to self-contained HTML file (auto-generated name if PATH omitted).",
    )

    filter_grp = parser.add_argument_group("Filtering")
    filter_grp.add_argument("-a", "--asset-class", metavar="TYPE",
                            choices=["all", "stocks", "etfs"], default="all",
                            help="Asset class: all, stocks, etfs (default: all).")
    filter_grp.add_argument("-r", "--region", metavar="REG", nargs="+",
                            choices=["all", "us", "eu", "asia", "superinvestor"], default="all",
                            help="Region(s): all, us, eu, asia, superinvestor (default: all).")
    filter_grp.add_argument(
        "-c", "--currency", metavar="CUR", nargs="+",
        choices=["all", "USD", "EUR", "GBP", "CHF", "JPY", "HKD", "INR",
                 "KRW", "TWD", "SGD", "AUD", "DKK", "SEK", "NOK", "GBp"],
        default="all",
        help="Currency: all, USD, EUR, GBP, CHF, JPY, HKD, INR, KRW, TWD, SGD, AUD, DKK, SEK, NOK, GBp (default: all).",
    )
    filter_grp.add_argument("--min-market-cap", metavar="CAP", type=_parse_min_market_cap, default=2_000_000_000,
                            help="Minimum market cap (default: 2B). See Market Cap Classification in README for reference ranges.")
    filter_grp.add_argument("-s", "--sector", type=str, default=None,
                            help="Filter by sector prefix (case-insensitive).")

    data_grp = parser.add_argument_group("Data")
    data_grp.add_argument("--superinvestor", action="store_true",
                          help="Include superinvestor ownership data.")
    data_grp.add_argument("--no-cache", action="store_true",
                          help="Bypass SQLite cache.")
    data_grp.add_argument("--cache-clear", action="store_true",
                          help="Clear all cached data.")

    perf_grp = parser.add_argument_group("Performance")
    perf_grp.add_argument("--workers", type=int, default=10,
                          help="Parallel fetch workers (default: 10).")
    perf_grp.add_argument("--include-technical", action="store_true",
                          help="Include RSI and MACD in scoring.")
    if argcomplete is not None:
        argcomplete.autocomplete(parser)
    args = parser.parse_args(argv)

    if args.command == "advisor":
        from investdaytip.advisor import advisor_main
        remaining = argv[1:] if argv else sys.argv[2:]
        return advisor_main(remaining)

    if args.command == "backtest":
        return _run_backtest_cli(args)

    file_tickers: list[str] = []
    if args.tickers_file:
        try:
            file_tickers = _load_tickers_from_file(args.tickers_file)
        except Exception as exc:
            logger.error("Could not read --tickers-file: %s", exc)
            Console().print(f"[red]Could not read --tickers-file: {exc}[/red]")
            return 1

    effective_tickers = _merge_ticker_lists(args.tickers, file_tickers)

    from investdaytip.cache import clear_cache
    from investdaytip.cache import set_enabled as cache_set_enabled
    if args.cache_clear:
        clear_cache()
    if args.no_cache:
        cache_set_enabled(False)

    console = Console()
    region_str = ", ".join(args.region) if isinstance(args.region, list) else args.region
    currency_str = ", ".join(args.currency) if isinstance(args.currency, list) else args.currency
    console.print(
        f"[bold cyan]InvestDayTip[/bold cyan] — analyzing markets "
        f"([italic]{args.asset_class} · {region_str} · {currency_str}[/italic])...\n"
    )

    # Warm-up superinvestor cache before the main scoring loop so that
    # the HTML Superinvestors column gets real data.  Only when the user
    # opted into it with --superinvestor and is requesting stocks (or all)
    # and the cache is not disabled.
    if args.superinvestor and args.asset_class in ("stocks", "all") and not args.no_cache:
        if not get_superinvestor_data():
            with Progress(
                SpinnerColumn(),
                BarColumn(),
                TextColumn("{task.completed}/{task.total}"),
                TimeElapsedColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as si_progress:
                si_task = si_progress.add_task("Fetching superinvestor data", total=None)
                def _si_cb(done: int, total: int, name: str) -> None:
                    si_progress.update(si_task, total=total, completed=done, description=f"Fetching {name}")
                try:
                    fetch_superinvestor_universe(progress_cb=_si_cb)
                except Exception:
                    logger.warning("Could not fetch superinvestor data, continuing without it.")
                    console.print("[yellow]⚠️  Could not fetch superinvestor data, continuing without it.[/yellow]")

    results: list[ScoredAsset] = []
    with Progress(
        SpinnerColumn(),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task_id = progress.add_task("Fetching market data", total=None)

        def cb(done: int, total: int, ticker: str) -> None:
            desc = f"Fetched {ticker}" if ticker else "Starting..."
            progress.update(task_id, total=total, completed=done, description=desc)

        try:
            results = recommend(
                tickers=effective_tickers,
                top_n=args.top,
                max_workers=args.workers,
                min_market_cap=args.min_market_cap,
                asset_class=args.asset_class,
                region=args.region,
                currency=args.currency,
                sector=args.sector,
                progress_cb=cb,
                include_technical=args.include_technical,
            )
        except Exception as exc:
            logger.error("Error during analysis: %s", exc)
            console.print(f"[red]Error during analysis: {exc}[/red]")
            return 1

    _render(results, console, include_superinvestor=args.superinvestor, include_technical=args.include_technical)

    if args.export_html is not None:
        try:
            destination = args.export_html or _default_export_html_filename(
                tickers_file=args.tickers_file,
            )
            # When a custom ticker universe is supplied, recommend() ignores
            # asset_class/region/currency — so report them as "custom" rather
            # than the unused argparse defaults to avoid misleading metadata.
            if effective_tickers:
                meta_asset_class = "custom"
                meta_region = "custom"
                meta_currency = "custom"
            else:
                meta_asset_class = args.asset_class
                meta_region = args.region
                meta_currency = args.currency
            out_path = export_recommendations_html(
                results,
                destination,
                top_n=args.top,
                asset_class=meta_asset_class,
                region=meta_region,
                currency=meta_currency,
                tickers=effective_tickers,
                tickers_file=args.tickers_file,
                include_superinvestor=args.superinvestor,
                include_technical=args.include_technical,
                sector=args.sector,
            )
            logger.info("HTML report exported: %s", out_path)
            console.print(f"[green]HTML report exported:[/green] {out_path}")
        except Exception as exc:
            logger.error("Failed to export HTML report: %s", exc)
            console.print(f"[red]Failed to export HTML report: {exc}[/red]")
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
