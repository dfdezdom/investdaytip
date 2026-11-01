#!/usr/bin/env python3
"""Weight sensitivity — is the shipped 6-factor vector a flat optimum or a knife-edge?

NOT an optimizer. Tests the production scorer with each factor's weight moved
±5 percentage points **one at a time** (the other five rescaled proportionally
to keep the sum at 1). If the current weights sit on a flat plateau, the
neighbours score similarly; if a small nudge moves alpha a lot, the setting is
fragile and the weight was arbitrary.

Uses the real ``QuantStockScorer`` by swapping ``scoring.STOCK_WEIGHTS_QUANT``
per variant (faithful to production). Full metrics per N.

Uso:
    python scripts/weight_sensitivity.py -r us --min-market-cap 0 -n 5 10 25
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

import investdaytip.scoring as sc  # noqa: E402
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
from investdaytip.recommender import _build_universe  # noqa: E402


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


def _shift(base: dict[str, float], factor: str, delta: float) -> dict[str, float]:
    """Move *factor* by *delta* absolute, rescale the others proportionally."""
    w = dict(base)
    new_f = w[factor] + delta
    if new_f <= 0.01 or new_f >= 0.99:
        raise ValueError(f"invalid weight for {factor}: {new_f}")
    k = (1.0 - new_f) / (1.0 - w[factor])
    out = {key: val * k for key, val in w.items() if key != factor}
    out[factor] = new_f
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-r", "--region", default="us")
    ap.add_argument("-c", "--currency", default="USD")
    ap.add_argument("-t", "--tickers", default=None)
    ap.add_argument("-n", "--top-ns", nargs="+", type=int, default=[5, 10, 25])
    ap.add_argument("--min-market-cap", type=float, default=None)
    ap.add_argument("--max-workers", type=int, default=10)
    ap.add_argument("--reporting-lag", type=int, default=60)
    ap.add_argument("--windows", default="5y")
    ap.add_argument("--benchmark", default="SPY")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    from investdaytip.cache import set_enabled
    set_enabled(False)

    base = dict(sc.STOCK_WEIGHTS_QUANT)
    variants: list[tuple[str, dict[str, float]]] = [("baseline", dict(base))]
    for factor in base:
        for delta in (-0.05, 0.05):
            variants.append((f"{factor} {delta:+.0%}", _shift(base, factor, delta)))
    # a couple of coarse "what if" alternatives
    variants.append(("momentum +10pp", _shift(base, "momentum", 0.10)))
    variants.append(("value +10pp", _shift(base, "value", 0.10)))
    variants.append(("profitability +10pp", _shift(base, "profitability", 0.10)))

    universe = list(args.tickers.split()) if args.tickers else _build_universe(None, "stocks", args.region, args.currency)
    if args.min_market_cap is None:
        args.min_market_cap = 0.0 if args.tickers else 2_000_000_000.0
    all_tickers = list(dict.fromkeys(universe + [args.benchmark]))
    print(f"Fetching {len(all_tickers)} tickers …")
    data = _fetch_all_data(all_tickers, "5y", args.max_workers)
    bench = data.get(args.benchmark, {}).get("history")
    if bench is None or bench.empty:
        sys.exit("no benchmark")

    scorer = sc.QuantStockScorer()

    for window in [w.strip() for w in args.windows.split(",") if w.strip()]:
        snaps = _snapshot_dates(data, window, 3, args.reporting_lag)
        if not snaps:
            continue
        print(f"\n=== window {window} ({len(snaps)} snapshots) ===")
        snaps_by: dict[tuple[str, int], list[BacktestSnapshot]] = {
            (name, n): [] for (name, _) in variants for n in args.top_ns
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

            for name, weights in variants:
                sc.STOCK_WEIGHTS_QUANT = weights
                scored = [(scorer.score(s, include_technical=True, si_data={}).total, t) for t, s in rows]
                scored.sort(key=lambda x: x[0], reverse=True)
                ranked = [t for _, t in scored]
                for n in args.top_ns:
                    picks = ranked[:n]
                    r6 = [_forward_return(data[t].get("history"), sd, 6) for t in picks if t in data]
                    r12 = [_forward_return(data[t].get("history"), sd, 12) for t in picks if t in data]
                    r6v = [r for r in r6 if r is not None]
                    r12v = [r for r in r12 if r is not None]
                    snaps_by[(name, n)].append(
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

        sc.STOCK_WEIGHTS_QUANT = base  # restore

        for n in args.top_ns:
            print(f"\nN={n}  (baseline weights: {base})")
            print(f"{'variant':<22} {'Alpha':>9} {'Sharpe':>8} {'Win12M':>8} {'dAlpha':>9}")
            print("-" * 58)
            base_m = _compute_metrics(snaps_by[("baseline", n)])
            print(f"{'baseline':<22} {base_m['alpha']*100:>8.2f}% {base_m['sharpe']:>8.2f} {base_m['win_rate_12m']*100:>7.1f}% {'—':>9}")
            for name, _ in variants[1:]:
                m = _compute_metrics(snaps_by[(name, n)])
                print(
                    f"{name:<22} {m['alpha']*100:>8.2f}% {m['sharpe']:>8.2f} "
                    f"{m['win_rate_12m']*100:>7.1f}% {(m['alpha']-base_m['alpha'])*100:>+8.2f}%"
                )


if __name__ == "__main__":
    main()
