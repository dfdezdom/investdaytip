#!/usr/bin/env python3
"""Asset-growth validation — full before/after (Alpha + Sharpe + Win12M).

Validates the investment anomaly (Titman-Wei / Cooper-Gulen-Schill) added as a
small tilt to the quant model: ``new = (1-w)·baseline + w·asset_growth_score``,
where ``asset_growth_score = _linear(asset_growth, best=-0.05, worst=0.30)``
(lower growth = better). Uses the ``asset_growth`` field now populated on
``StockData`` by every derivation path.

Two data paths (``--pit-source``):
- ``none``     → yfinance annual statements (classic; ~5-year coverage gap in
  early snapshots).
- ``stockfit`` → local PIT snapshot (12 fiscal years, full coverage; no key).

Reports the real decision metrics per N (alpha, Sharpe, Win12M, MaxDD) so the
AGENTS Ship/Consider/Reject rules can be applied directly.

Uso:
    python scripts/asset_growth_validate.py -r us --min-market-cap 0
    python scripts/asset_growth_validate.py --pit-source stockfit --min-market-cap 0
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))

from investdaytip.backtest import (  # noqa: E402
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
    QuantStockScorer,
    _linear,
    _technical_score,
)

_BASE_W = {"value": 0.25, "growth": 0.20, "profitability": 0.25, "momentum": 0.15, "eps_revisions": 0.15}
_DISQ_SOFT, _DISQ_HARD = 20.0, 15.0
_WEIGHTS = [0.10, 0.15, 0.20]


def _ag_score(d) -> float:
    return _linear(d.asset_growth, best=-0.05, worst=0.30, default=50.0)


def _factor_scores(scorer: QuantStockScorer, d):
    value, _ = scorer._value_score(d)
    growth, _ = scorer._growth_score(d)
    profitability, _ = scorer._profitability_score(d)
    momentum, _ = scorer._momentum_score(d)
    eps_rev, _ = scorer._eps_revisions_score(d)
    tech, _ = _technical_score(d)
    momentum = momentum * 0.70 + tech * 0.30
    return value, growth, profitability, momentum, eps_rev


def _combine(fs, weights, extra, extra_w) -> float:
    value, growth, profitability, momentum, eps_rev = fs
    disq = (
        growth < _DISQ_SOFT or momentum < _DISQ_SOFT or eps_rev < _DISQ_SOFT
        or value < _DISQ_HARD or profitability < _DISQ_HARD
    )
    total = (
        value * weights["value"] + growth * weights["growth"]
        + profitability * weights["profitability"] + momentum * weights["momentum"]
        + eps_rev * weights["eps_revisions"]
    )
    if extra is not None:
        total += extra * extra_w
    return min(total, 50.0) if disq else total


def _snapshot_dates(data, period, interval_months, reporting_lag):
    latest_common = _latest_common_end(data)
    if latest_common is None:
        sys.exit("No historical data available")
    end_date = latest_common - timedelta(days=reporting_lag)
    m = re.match(r"(\d+)y", period)
    wy = 20.0 if period == "max" else (float(m.group(1)) if m else 5.0)
    start = end_date - timedelta(days=int(365.25 * wy))
    snaps = _generate_snapshot_dates(end_date, start, interval_months)
    return [d for d in snaps if d + timedelta(days=int(30.5 * 12)) <= latest_common]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-r", "--region", default="us")
    ap.add_argument("-c", "--currency", default="USD")
    ap.add_argument("-t", "--tickers", default=None)
    ap.add_argument("-n", "--top-ns", nargs="+", type=int, default=[5, 10, 25])
    ap.add_argument("--pit-source", choices=["none", "stockfit"], default="none")
    ap.add_argument("--min-market-cap", type=float, default=None)
    ap.add_argument("--max-workers", type=int, default=10)
    ap.add_argument("--reporting-lag", type=int, default=60)
    ap.add_argument("--windows", default="5y,3y")
    ap.add_argument("--benchmark", default="SPY")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    from investdaytip.cache import set_enabled
    set_enabled(False)

    universe = list(args.tickers.split()) if args.tickers else _build_universe(None, "stocks", args.region, args.currency)
    if args.min_market_cap is None:
        args.min_market_cap = 0.0 if args.tickers else 2_000_000_000.0

    pit_data = {}
    if args.pit_source == "stockfit":
        from investdaytip.data_source_stockfit import check_pit_access
        check_pit_access()
        pit_data = _fetch_pit_statements(universe, args.max_workers)
        if not pit_data:
            sys.exit("No PIT statements available")
        universe = list(pit_data.keys())  # clean 100%-PIT pool
        print(f"PIT pool: {len(universe)} tickers with point-in-time statements")
    else:
        print(f"universe: {len(universe)} tickers")

    all_tickers = list(dict.fromkeys(universe + [args.benchmark]))
    print(f"Fetching {len(all_tickers)} tickers …")
    data = _fetch_all_data(all_tickers, "5y", args.max_workers)

    bench = data.get(args.benchmark, {}).get("history")
    if bench is None or bench.empty:
        sys.exit("no benchmark")

    scorer = QuantStockScorer()
    weights = _WEIGHTS
    variants: list[tuple[str, float]] = [("baseline", 0.0)] + [("asset_growth", w) for w in weights]

    for window in [w.strip() for w in args.windows.split(",") if w.strip()]:
        snaps = _snapshot_dates(data, window, 3, args.reporting_lag)
        if not snaps:
            continue
        print(f"\n  window {window}: {len(snaps)} snapshots")

        snaps_by: dict[tuple[str, float, int], list[BacktestSnapshot]] = {
            (c, w, n): [] for (c, w) in variants for n in args.top_ns
        }

        for i, sd in enumerate(snaps, 1):
            qd = _latest_available_quarter(sd, args.reporting_lag)
            if qd is None:
                continue

            rows = []
            for t in universe:
                td = data.get(t)
                if td is None or "error" in td:
                    continue
                hist = td.get("history")
                if hist is None or hist.empty:
                    continue
                if t in pit_data:
                    sd_obj = _build_pit_stock_data(
                        ticker=t, info=td.get("info", {}), price_history=hist, snapshot_date=sd,
                        pit=pit_data[t], dividends=td.get("dividends", pd.Series(dtype=float)),
                        earnings_dates=td.get("earnings_dates"), reporting_lag_days=args.reporting_lag,
                    )
                else:
                    sd_obj = _build_historical_stock_data(
                        ticker=t, info=td.get("info", {}), price_history=hist, snapshot_date=sd,
                        balance_sheet=td.get("balance_sheet", pd.DataFrame()),
                        income_stmt=td.get("income_stmt", pd.DataFrame()),
                        cash_flow=td.get("cash_flow", pd.DataFrame()),
                        dividends=td.get("dividends", pd.Series(dtype=float)),
                        quarter_date=qd, earnings_dates=td.get("earnings_dates"),
                        reporting_lag_days=args.reporting_lag,
                    )
                if args.min_market_cap > 0 and (sd_obj.market_cap is None or sd_obj.market_cap < args.min_market_cap):
                    continue
                rows.append((t, sd_obj))

            if not rows:
                continue

            if i == 1 and not args.quiet:
                # asset_growth coverage diagnostic on the first snapshot
                n_ag = sum(1 for _, s in rows if s.asset_growth is not None)
                print(f"    snapshot 1: asset_growth available for {n_ag}/{len(rows)} assets", file=sys.stderr)

            for (cname, w) in variants:
                ws = {k: v * (1.0 - w) for k, v in _BASE_W.items()} if w > 0 else _BASE_W
                scored = []
                for t, s in rows:
                    fs = _factor_scores(scorer, s)
                    extra = _ag_score(s) if (w > 0 and cname == "asset_growth") else None
                    scored.append((_combine(fs, ws, extra, w), t))
                scored.sort(key=lambda x: x[0], reverse=True)
                ranked = [t for _, t in scored]
                for n in args.top_ns:
                    picks = ranked[:n]
                    r6 = [_forward_return(data[t].get("history"), sd, 6) for t in picks if t in data]
                    r12 = [_forward_return(data[t].get("history"), sd, 12) for t in picks if t in data]
                    r6v = [r for r in r6 if r is not None]
                    r12v = [r for r in r12 if r is not None]
                    snaps_by[(cname, w, n)].append(
                        BacktestSnapshot(
                            date=sd, picks=[],
                            avg_return_6m=float(np.mean(r6v)) if r6v else None,
                            avg_return_12m=float(np.mean(r12v)) if r12v else None,
                            benchmark_return_6m=_forward_return(bench, sd, 6),
                            benchmark_return_12m=_forward_return(bench, sd, 12),
                        )
                    )

            if not args.quiet:
                print(f"    scored {i}/{len(snaps)} ({sd:%Y-%m-%d})", file=sys.stderr)

        print(f"\n=== window {window} ({args.pit_source}) ===")
        header = f"{'N':>4} {'variant':>14} {'Alpha':>9} {'Sharpe':>8} {'Win12M':>8} {'MaxDD':>8}"
        for n in args.top_ns:
            print(f"\nN={n}")
            print(header)
            print("-" * len(header))
            base_m = _compute_metrics(snaps_by[("baseline", 0.0, n)])
            print(
                f"{n:>4} {'baseline':>14} {base_m['alpha']*100:>8.2f}% {base_m['sharpe']:>8.2f} "
                f"{base_m['win_rate_12m']*100:>7.1f}% {base_m['max_drawdown']*100:>7.1f}%"
            )
            for w in weights:
                m = _compute_metrics(snaps_by[("asset_growth", w, n)])
                print(
                    f"{n:>4} {f'ag@{int(w*100)}%':>14} {m['alpha']*100:>8.2f}% {m['sharpe']:>8.2f} "
                    f"{m['win_rate_12m']*100:>7.1f}% {m['max_drawdown']*100:>7.1f}%"
                )


if __name__ == "__main__":
    main()
