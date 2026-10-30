#!/usr/bin/env python3
"""Portfolio-construction sweep — how many positions, and does an entry/exit band help?

Two experiments, both built on the engine's own backtest machinery so the
numbers are comparable to ``investdaytip backtest``:

1. ``n-sweep``
   Evaluates a simple "hold the top-N by score, re-pick every snapshot"
   portfolio for several ``N`` values (default 15 / 20 / 25) and prints the
   metrics side by side. Answers "how many positions?".

2. ``hysteresis``
   A portfolio *simulation* that ``run_backtest`` does not model: a name
   **enters** when it is in the top-``enter`` and **exits** only after it has
   been outside the top-``exit`` for ``confirm`` consecutive reviews. With
   ``--enter 20 --exit 30 --confirm 2`` this is the 20->30 band with two
   reviews of confirmation. Reduces churn versus re-picking the top-N every
   period.

Both share one fetch of the universe and one per-snapshot ranking, so sweeping
several configurations costs a single download.

What this does NOT model (read before trusting the absolute numbers)
---------------------------------------------------------------
* Transaction costs, slippage and taxes.
* Weight drift between snapshots — every period is evaluated equal-weighted
  over the names held at that snapshot.
* Cash drag / minimum cash.
* A tradable track record: the engine (and this script) chain a **6-month
  forward return** at every snapshot even though snapshots are
  ``--interval-months`` apart, so consecutive windows overlap. Use these
  figures for *relative* comparison between configurations, not as the return
  you would have earned.

Usage
-----
    # Which N? (defaults to 15 20 25)
    python scripts/portfolio_sweep.py n-sweep -r us --period 5y --top-ns 15 20 25

    # Does the 20->30 band help vs re-picking every period?
    python scripts/portfolio_sweep.py hysteresis -r us --period 5y --pairs 20:30:2

    # Compare several bands against the plain top-20 in one go
    python scripts/portfolio_sweep.py hysteresis -r us --period 5y \
        --pairs 15:25:2 20:30:2 20:30:1

    # Explicit tickers, StockFit point-in-time fundamentals, JSON out
    python scripts/portfolio_sweep.py n-sweep -t "AAPL MSFT GOOGL ..." \
        --period 5y --pit-source stockfit --json sweep.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from investdaytip.backtest import (  # noqa: E402
    _DEFAULT_BENCHMARKS,
    BacktestSnapshot,
    _build_historical_stock_data,
    _build_pit_stock_data,
    _compute_metrics,
    _fetch_all_data,
    _fetch_pit_statements,
    _forward_return,
    _generate_snapshot_dates,
    _latest_available_quarter,
    _latest_common_end,
)
from investdaytip.recommender import _build_universe  # noqa: E402
from investdaytip.scoring import (  # noqa: E402
    ScoredAsset,
    resolve_include_technical,
    score_stock,
)

# (snapshot date, assets ranked by score desc) for every review date.
Ranking = list[tuple[datetime, list[ScoredAsset]]]
# A strategy maps a review date + the full ranking to the tickers held that period.
Strategy = Callable[[datetime, list[ScoredAsset]], list[str]]


# ── Data preparation ──────────────────────────────────────────────────────────


def _prepare(
    args: argparse.Namespace,
) -> tuple[
    list[str],
    dict[str, Any],
    dict[str, Any],
    pd.DataFrame,
    list[datetime],
    str,
]:
    """Fetch the universe once and resolve the snapshot dates.

    Mirrors ``run_backtest``'s windowing (period -> start date, 12-month
    forward-return room at the end) so absolute figures line up with the CLI.
    """
    universe = (
        list(args.tickers.split())
        if args.tickers
        else _build_universe(None, args.asset_class, args.region, args.currency)
    )
    if not universe:
        sys.exit("Empty universe")

    if args.min_market_cap is None:
        args.min_market_cap = 0.0 if args.tickers else 2_000_000_000.0

    region = args.region if isinstance(args.region, str) else (args.region[0] if args.region else "us")
    benchmark = args.benchmark or _DEFAULT_BENCHMARKS.get(region, "SPY")

    all_tickers = list(set(universe + [benchmark]))
    print(f"Fetching {len(all_tickers)} tickers (period={args.period}) …")
    data = _fetch_all_data(all_tickers, args.period, args.max_workers)

    pit_data: dict[str, Any] = {}
    if args.pit_source == "stockfit":
        from investdaytip.data_source_stockfit import check_pit_access

        check_pit_access()
        pit_data = _fetch_pit_statements(universe, args.max_workers)

    bench_history = data.get(benchmark, {}).get("history")
    if bench_history is None or bench_history.empty:
        sys.exit(f"Could not fetch benchmark {benchmark}")

    latest_common = _latest_common_end(data)
    if latest_common is None:
        sys.exit("No historical data available")

    end_date = latest_common - timedelta(days=args.reporting_lag)
    parsed = re.match(r"(\d+)y", args.period)
    window_years = 20.0 if args.period == "max" else (float(parsed.group(1)) if parsed else 5.0)
    start_date = end_date - timedelta(days=int(365.25 * window_years))
    snap_dates = _generate_snapshot_dates(end_date, start_date, args.interval_months)
    snap_dates = [
        d for d in snap_dates if d + timedelta(days=int(30.5 * 12)) <= latest_common
    ]
    if not snap_dates:
        sys.exit("Not enough history for any snapshot")

    print(
        f"{len(snap_dates)} snapshots: {snap_dates[0]:%Y-%m-%d} -> {snap_dates[-1]:%Y-%m-%d} "
        f"| benchmark {benchmark}"
    )
    return universe, data, pit_data, bench_history, snap_dates, benchmark


def _build_rankings(
    universe: list[str],
    data: dict[str, Any],
    pit_data: dict[str, Any],
    snap_dates: list[datetime],
    *,
    min_market_cap: float,
    scoring_model: str,
    include_technical: bool,
    reporting_lag_days: int,
    quiet: bool,
) -> Ranking:
    """Score the universe at every snapshot date and cache the ranking.

    Identical selection logic to ``run_backtest`` (build stock data as of the
    snapshot, apply the market-cap filter, score, sort desc), but the result is
    kept so several strategies can be evaluated without re-scoring.
    """
    rankings: Ranking = []
    for i, sd in enumerate(snap_dates, 1):
        quarter_date = _latest_available_quarter(sd, reporting_lag_days)
        if quarter_date is None:
            continue

        scored: list[ScoredAsset] = []
        for t in universe:
            td = data.get(t)
            if td is None or "error" in td:
                continue
            hist = td.get("history")
            if hist is None or hist.empty:
                continue

            if t in pit_data:
                stock_data = _build_pit_stock_data(
                    ticker=t,
                    info=td.get("info", {}),
                    price_history=hist,
                    snapshot_date=sd,
                    pit=pit_data[t],
                    dividends=td.get("dividends", pd.Series(dtype=float)),
                    earnings_dates=td.get("earnings_dates"),
                    reporting_lag_days=reporting_lag_days,
                )
            else:
                stock_data = _build_historical_stock_data(
                    ticker=t,
                    info=td.get("info", {}),
                    price_history=hist,
                    snapshot_date=sd,
                    balance_sheet=td.get("balance_sheet", pd.DataFrame()),
                    income_stmt=td.get("income_stmt", pd.DataFrame()),
                    cash_flow=td.get("cash_flow", pd.DataFrame()),
                    dividends=td.get("dividends", pd.Series(dtype=float)),
                    quarter_date=quarter_date,
                    earnings_dates=td.get("earnings_dates"),
                    reporting_lag_days=reporting_lag_days,
                )

            if min_market_cap > 0 and (
                stock_data.market_cap is None or stock_data.market_cap < min_market_cap
            ):
                continue

            scored.append(
                score_stock(
                    stock_data,
                    model=scoring_model,
                    include_technical=include_technical,
                )
            )

        scored.sort(key=lambda s: s.total, reverse=True)
        if scored:
            rankings.append((sd, scored))
        if not quiet:
            print(
                f"  scored {i}/{len(snap_dates)} ({sd:%Y-%m-%d}): {len(scored)} assets",
                file=sys.stderr,
            )

    return rankings


# ── Strategies ────────────────────────────────────────────────────────────────


def _top_n_strategy(n: int) -> Strategy:
    """Hold the top-N scoring names at every review (no memory)."""

    def step(_: datetime, ranked: list[ScoredAsset]) -> list[str]:
        return [s.data.ticker for s in ranked[:n]]

    return step


def _hysteresis_strategy(enter: int, exit_: int, confirm: int) -> Strategy:
    """Enter in top-``enter``, exit after ``confirm`` reviews outside top-``exit``.

    Stateful closure: the caller drives it one snapshot at a time in date order.
    A held name resets its miss counter whenever it is still inside the exit
    band, so a single bad review does not drop it.
    """
    if enter > exit_:
        raise ValueError(f"enter ({enter}) must be <= exit ({exit_})")
    misses: dict[str, int] = {}

    def step(_: datetime, ranked: list[ScoredAsset]) -> list[str]:
        ranking = [s.data.ticker for s in ranked]
        top_exit = set(ranking[:exit_])
        top_enter = ranking[:enter]

        for name in list(misses):
            if name in top_exit:
                misses[name] = 0
            else:
                misses[name] += 1
                if misses[name] >= confirm:
                    del misses[name]

        for name in top_enter:
            misses.setdefault(name, 0)

        return sorted(misses)

    return step


# ── Evaluation ────────────────────────────────────────────────────────────────


def _mean_returns(
    data: dict[str, Any], tickers: list[str], sd: datetime
) -> tuple[Optional[float], Optional[float]]:
    """Equal-weight average 6m / 12m forward return for *tickers* at *sd*."""
    r6: list[float] = []
    r12: list[float] = []
    for t in tickers:
        td = data.get(t)
        hist = td.get("history") if td else None
        if hist is None or hist.empty:
            continue
        six = _forward_return(hist, sd, 6)
        twelve = _forward_return(hist, sd, 12)
        if six is not None:
            r6.append(six)
        if twelve is not None:
            r12.append(twelve)
    return (
        (sum(r6) / len(r6)) if r6 else None,
        (sum(r12) / len(r12)) if r12 else None,
    )


def _evaluate(
    rankings: Ranking,
    data: dict[str, Any],
    bench_history: pd.DataFrame,
    strategy: Strategy,
) -> dict[str, Any]:
    """Drive *strategy* over the cached rankings and aggregate the metrics."""
    snapshots: list[BacktestSnapshot] = []
    holdings: list[int] = []

    for sd, ranked in rankings:
        held = strategy(sd, ranked)
        if not held:
            # All out of the market -> cash for the period. Skip it: _compute_metrics
            # already treats a missing snapshot return as no contribution (0%).
            continue

        avg6, avg12 = _mean_returns(data, held, sd)
        snapshots.append(
            BacktestSnapshot(
                date=sd,
                picks=[],
                avg_return_6m=avg6,
                avg_return_12m=avg12,
                benchmark_return_6m=_forward_return(bench_history, sd, 6),
                benchmark_return_12m=_forward_return(bench_history, sd, 12),
            )
        )
        holdings.append(len(held))

    metrics = _compute_metrics(snapshots)
    metrics["snapshots"] = len(snapshots)
    metrics["avg_holdings"] = (
        (sum(holdings) / len(holdings)) if holdings else 0.0
    )
    return metrics


# ── Reporting ─────────────────────────────────────────────────────────────────

_METRIC_COLUMNS: list[tuple[str, str, str]] = [
    ("cumulative_return", "Cum", "pct"),
    ("benchmark_cumulative_return", "Bench", "pct"),
    ("alpha", "Alpha", "pct"),
    ("sharpe", "Sharpe", "num"),
    ("benchmark_sharpe", "BSharpe", "num"),
    ("win_rate_12m", "Win12M", "pct"),
    ("max_drawdown", "MaxDD", "pct"),
    ("snapshots", "Snaps", "int"),
    ("avg_holdings", "AvgHold", "num"),
]


def _fmt(value: Any, kind: str) -> str:
    if value is None:
        return "-"
    if kind == "pct":
        return f"{value * 100:.2f}%"
    if kind == "int":
        return f"{int(value)}"
    return f"{value:.2f}"


def _print_table(rows: list[dict[str, Any]], label: str) -> None:
    header = f"{label:<14}" + "".join(f"{col[1]:>10}" for col in _METRIC_COLUMNS)
    print("\n" + header)
    print("-" * len(header))
    for row in rows:
        line = f"{row['config']:<14}"
        for key, _, kind in _METRIC_COLUMNS:
            line += f"{_fmt(row.get(key), kind):>10}"
        print(line)
    print()


# ── Commands ──────────────────────────────────────────────────────────────────


def _cmd_n_sweep(args: argparse.Namespace) -> None:
    universe, data, pit_data, bench_history, snap_dates, _ = _prepare(args)
    include_technical = resolve_include_technical(
        None if args.technical == "auto" else args.technical == "yes",
        args.scoring_model,
    )
    rankings = _build_rankings(
        universe,
        data,
        pit_data,
        snap_dates,
        min_market_cap=args.min_market_cap,
        scoring_model=args.scoring_model,
        include_technical=include_technical,
        reporting_lag_days=args.reporting_lag,
        quiet=args.quiet,
    )

    rows: list[dict[str, Any]] = []
    for n in args.top_ns:
        metrics = _evaluate(rankings, data, bench_history, _top_n_strategy(n))
        metrics["config"] = f"top-{n}"
        rows.append(metrics)

    _print_table(rows, "N sweep")
    if args.json:
        _write_json(args.json, "n-sweep", args, rows)


def _cmd_hysteresis(args: argparse.Namespace) -> None:
    universe, data, pit_data, bench_history, snap_dates, _ = _prepare(args)
    include_technical = resolve_include_technical(
        None if args.technical == "auto" else args.technical == "yes",
        args.scoring_model,
    )
    rankings = _build_rankings(
        universe,
        data,
        pit_data,
        snap_dates,
        min_market_cap=args.min_market_cap,
        scoring_model=args.scoring_model,
        include_technical=include_technical,
        reporting_lag_days=args.reporting_lag,
        quiet=args.quiet,
    )

    rows: list[dict[str, Any]] = []
    for pair in args.pairs:
        try:
            enter_s, exit_s, confirm_s = pair.split(":")
            enter, exit_, confirm = int(enter_s), int(exit_s), int(confirm_s)
        except ValueError:
            sys.exit(f"Bad --pairs value {pair!r}; expected ENTER:EXIT:CONFIRM (e.g. 20:30:2)")
        if enter > exit_:
            sys.exit(f"Bad --pairs value {pair!r}; ENTER must be <= EXIT")
        if confirm < 1:
            sys.exit(f"Bad --pairs value {pair!r}; CONFIRM must be >= 1")

        metrics = _evaluate(
            rankings,
            data,
            bench_history,
            _hysteresis_strategy(enter, exit_, confirm),
        )
        metrics["config"] = f"{enter}->{exit_}x{confirm}"
        rows.append(metrics)

    _print_table(rows, "Hysteresis")
    if args.json:
        _write_json(args.json, "hysteresis", args, rows)


def _write_json(path: str, command: str, args: argparse.Namespace, rows: list[dict[str, Any]]) -> None:
    payload = {
        "command": command,
        "timestamp": datetime.now().isoformat(),
        "config": {
            "region": args.region,
            "currency": args.currency,
            "asset_class": args.asset_class,
            "tickers": args.tickers,
            "period": args.period,
            "interval_months": args.interval_months,
            "min_market_cap": args.min_market_cap,
            "reporting_lag": args.reporting_lag,
            "scoring_model": args.scoring_model,
            "technical": args.technical,
            "pit_source": args.pit_source,
            "benchmark": args.benchmark,
        },
        "rows": rows,
    }
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2)
    print(f"Results saved to: {path}")


# ── CLI ───────────────────────────────────────────────────────────────────────


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-r", "--region", default="us", help="Region filter (default: us)")
    parser.add_argument("-c", "--currency", default="USD", help="Currency filter (default: USD)")
    parser.add_argument(
        "-a", "--asset-class", default="stocks", help="Asset class (default: stocks)"
    )
    parser.add_argument("-t", "--tickers", default=None, help="Space-separated tickers (overrides universe)")
    parser.add_argument("--period", default="5y", help="yfinance lookback (default: 5y)")
    parser.add_argument("--interval-months", type=int, default=3, help="Months between snapshots (default: 3)")
    parser.add_argument(
        "--min-market-cap",
        type=float,
        default=None,
        help="Minimum market cap (default: 0 with -t, else 2B)",
    )
    parser.add_argument("--max-workers", type=int, default=10)
    parser.add_argument("--reporting-lag", type=int, default=60, help="Days after quarter-end (default: 60)")
    parser.add_argument(
        "--scoring-model", choices=["quant", "classic"], default="quant", help="Scoring model (default: quant)"
    )
    parser.add_argument(
        "--technical",
        choices=["auto", "yes", "no"],
        default="auto",
        help="Blend technicals into the score (default: auto, per model)",
    )
    parser.add_argument(
        "--pit-source",
        choices=["none", "stockfit"],
        default="none",
        help="Point-in-time fundamentals via StockFit (default: none)",
    )
    parser.add_argument("--benchmark", default=None, help="Benchmark ticker (default: per region)")
    parser.add_argument("--no-cache", action="store_true", help="Disable the cache for this run")
    parser.add_argument("-o", "--json", default=None, help="Write results to a JSON file")
    parser.add_argument("--quiet", action="store_true", help="Suppress per-snapshot progress")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Portfolio-construction sweep for the InvestDayTip scoring model."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    n_sweep = sub.add_parser("n-sweep", help="Evaluate several top-N portfolio sizes")
    _add_common_args(n_sweep)
    n_sweep.add_argument(
        "--top-ns",
        nargs="+",
        type=int,
        default=[15, 20, 25],
        help="Portfolio sizes to evaluate (default: 15 20 25)",
    )
    n_sweep.set_defaults(func=_cmd_n_sweep)

    hysteresis = sub.add_parser(
        "hysteresis", help="Evaluate entry/exit bands (enter top-N, exit below top-M)"
    )
    _add_common_args(hysteresis)
    hysteresis.add_argument(
        "--pairs",
        nargs="+",
        default=["20:30:2"],
        help="ENTER:EXIT:CONFIRM triples, e.g. '20:30:2' (default: 20:30:2)",
    )
    hysteresis.set_defaults(func=_cmd_hysteresis)

    args = parser.parse_args()

    if args.no_cache:
        from investdaytip.cache import set_enabled

        set_enabled(False)

    args.func(args)


if __name__ == "__main__":
    main()
