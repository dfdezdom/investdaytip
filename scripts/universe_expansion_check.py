#!/usr/bin/env python3
"""Universe-expansion check — does the 122→202 expansion hurt top-N alpha?

Answers the question "does the broader universe dilute the top-N alpha?" by
evaluating the **baseline** quant scorer at several N (5/10/15/20/25) on BOTH
pools from a single download:

- **old pool** = DEFAULT_UNIVERSE as of the parent of the expansion commit
  (a62ab18^, 122 tickers) + SUPERINVESTOR_UNIVERSE → 197 deduped.
- **new pool** = current DEFAULT_UNIVERSE (202) + SUPERINVESTOR_UNIVERSE → 277.

The old pool is a subset of the new download, so one ``_fetch_all_data`` of the
new pool + benchmark serves both — no re-download, no config drift between the
two pools (same snapshot dates, same benchmark, same forward returns).

Uso:
    python scripts/universe_expansion_check.py -n 5 10 15 20 25 --min-market-cap 0
"""
from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))

from investdaytip.backtest import (  # noqa: E402
    BacktestSnapshot,
    _build_historical_stock_data,
    _compute_metrics,
    _fetch_all_data,
    _forward_return,
    _generate_snapshot_dates,
    _latest_available_quarter,
    _latest_common_end,
)
from investdaytip.scoring import QuantStockScorer  # noqa: E402
from investdaytip.superinvestor_universe import SUPERINVESTOR_UNIVERSE  # noqa: E402
from investdaytip.universe import DEFAULT_UNIVERSE as _NEW_DEFAULT  # noqa: E402


def _dedup(tickers: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for t in tickers:
        k = t.upper()
        if k not in seen:
            seen.add(k)
            out.append(t)
    return out


def _old_default_universe() -> list[str]:
    src = subprocess.check_output(
        ["git", "show", "a62ab18^:src/investdaytip/universe.py"],
        cwd=_REPO, text=True,
    )
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "DEFAULT_UNIVERSE":
            return ast.literal_eval(node.value)
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "DEFAULT_UNIVERSE":
                    return ast.literal_eval(node.value)
    raise RuntimeError("DEFAULT_UNIVERSE not found in old universe.py")


def _snapshot_dates(data, period: str, interval_months: int, reporting_lag: int) -> list[datetime]:
    latest_common = _latest_common_end(data)
    if latest_common is None:
        sys.exit("No historical data available")
    end_date = latest_common - timedelta(days=reporting_lag)
    m = re.match(r"(\d+)y", period)
    window_years = 20.0 if period == "max" else (float(m.group(1)) if m else 5.0)
    start_date = end_date - timedelta(days=int(365.25 * window_years))
    snaps = _generate_snapshot_dates(end_date, start_date, interval_months)
    return [d for d in snaps if d + timedelta(days=int(30.5 * 12)) <= latest_common]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-n", "--top-ns", nargs="+", type=int, default=[5, 10, 15, 20, 25])
    ap.add_argument("--min-market-cap", type=float, default=0.0)
    ap.add_argument("--max-workers", type=int, default=10)
    ap.add_argument("--reporting-lag", type=int, default=60)
    ap.add_argument("--windows", default="5y,3y")
    ap.add_argument("--benchmark", default="SPY")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    from investdaytip.cache import set_enabled
    set_enabled(False)

    old_pool = _dedup(_old_default_universe() + list(SUPERINVESTOR_UNIVERSE))
    new_pool = _dedup(list(_NEW_DEFAULT) + list(SUPERINVESTOR_UNIVERSE))
    print(f"old pool {len(old_pool)} | new pool {len(new_pool)}")

    all_tickers = list(dict.fromkeys(new_pool + [args.benchmark]))
    print(f"Fetching {len(all_tickers)} tickers (period=5y) …")
    data = _fetch_all_data(all_tickers, "5y", args.max_workers)

    bench_history = data.get(args.benchmark, {}).get("history")
    if bench_history is None or bench_history.empty:
        sys.exit(f"Could not fetch benchmark {args.benchmark}")

    scorer = QuantStockScorer()
    old_set = {t.upper() for t in old_pool}
    new_set = {t.upper() for t in new_pool}

    results: dict[str, dict[tuple[str, int], dict[str, float]]] = {}

    for window in [w.strip() for w in args.windows.split(",") if w.strip()]:
        snap_dates = _snapshot_dates(data, window, 3, args.reporting_lag)
        if not snap_dates:
            continue
        print(f"  window {window}: {len(snap_dates)} snapshots")

        snaps_by: dict[tuple[str, int], list[BacktestSnapshot]] = {
            (pool, n): [] for pool in ("old", "new") for n in args.top_ns
        }

        for i, sd in enumerate(snap_dates, 1):
            quarter_date = _latest_available_quarter(sd, args.reporting_lag)
            if quarter_date is None:
                continue

            # Build StockData once for every ticker in the full download.
            ranked: dict[str, list[tuple[float, str]]] = {"old": [], "new": []}
            for t in new_pool:
                td = data.get(t)
                if td is None or "error" in td:
                    continue
                hist = td.get("history")
                if hist is None or hist.empty:
                    continue
                sd_obj = _build_historical_stock_data(
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
                    reporting_lag_days=args.reporting_lag,
                )
                if args.min_market_cap > 0 and (
                    sd_obj.market_cap is None or sd_obj.market_cap < args.min_market_cap
                ):
                    continue
                total = scorer.score(sd_obj, include_technical=True, si_data={}).total
                k = t.upper()
                if k in new_set:
                    ranked["new"].append((total, t))
                if k in old_set:
                    ranked["old"].append((total, t))

            for pool in ("old", "new"):
                ranked[pool].sort(key=lambda x: x[0], reverse=True)
                for n in args.top_ns:
                    picks = [t for _, t in ranked[pool][:n]]
                    r6 = [_forward_return(data[t].get("history"), sd, 6) for t in picks if t in data]
                    r12 = [_forward_return(data[t].get("history"), sd, 12) for t in picks if t in data]
                    r6v = [r for r in r6 if r is not None]
                    r12v = [r for r in r12 if r is not None]
                    snaps_by[(pool, n)].append(
                        BacktestSnapshot(
                            date=sd,
                            picks=[],
                            avg_return_6m=float(np.mean(r6v)) if r6v else None,
                            avg_return_12m=float(np.mean(r12v)) if r12v else None,
                            benchmark_return_6m=_forward_return(bench_history, sd, 6),
                            benchmark_return_12m=_forward_return(bench_history, sd, 12),
                        )
                    )

            if not args.quiet:
                print(f"    scored {i}/{len(snap_dates)} ({sd:%Y-%m-%d})", file=sys.stderr)

        results[window] = {
            (pool, n): _compute_metrics(snaps_by[(pool, n)])
            for pool in ("old", "new") for n in args.top_ns
        }

    for window, res in results.items():
        print(f"\n=== window {window} ===")
        print(f"{'N':>4} {'old Alpha':>10} {'new Alpha':>10} {'Δ':>8} | {'old Sharpe':>11} {'new Sharpe':>11} | {'old Win12M':>10} {'new Win12M':>10}")
        print("-" * 80)
        for n in args.top_ns:
            o = res[("old", n)]
            nw = res[("new", n)]
            da = nw["alpha"] - o["alpha"]
            print(
                f"{n:>4} {o['alpha']*100:>9.2f}% {nw['alpha']*100:>9.2f}% {da*100:>+7.2f}% | "
                f"{o['sharpe']:>10.2f} {nw['sharpe']:>10.2f} | "
                f"{o['win_rate_12m']*100:>9.1f}% {nw['win_rate_12m']*100:>9.1f}%"
            )


if __name__ == "__main__":
    main()
