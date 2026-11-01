#!/usr/bin/env python3
"""Scoring-variant sweep — one universe download, several weight/value/cap configs.

Experimento de mejora de alpha (2026-11-01), tanda 2: **cap de descalificación
+ momentum suave**. El primer barrido (A+B) demostró que podar Value es
destructivo, así que aquí Value queda intacto y se exploran dos dimensiones:

1. **Cap de descalificación** — el scorer capa el total a 50 cuando un factor
   cae en zona roja (growth/momentum/eps_rev < 20, value/profit < 15). Con un
   universo ampliado y value-heavy, muchos value baratos sacan Growth bajo y
   quedan capados. Se prueban: sin cap, cap suavizado (10/5) y quitar el
   disparador de growth o de value.
2. **Momentum suave** — subir momentum 15→20/25 financiado solo de EPS Revisions
   (Value intacto en 25), la versión contenida del "Consider" del primer barrido.

Mismas métricas que ``investdaytip backtest``. La variante ``baseline`` se
puntúa con el scorer real; las demás con una réplica parametrizada que se
cross-chequea contra el scorer real al arrancar (mismo total, error < 1e-9).

Uso:
    python scripts/scoring_variant_sweep.py -r us -n 5 --period 5y
    python scripts/scoring_variant_sweep.py -t "AAPL MSFT GOOGL" -n 2  # smoke
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from investdaytip.backtest import (  # noqa: E402
    _DEFAULT_BENCHMARKS,
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
from investdaytip.scoring import (  # noqa: E402
    QuantStockScorer,
    _linear,
    _non_negative,
    _technical_score,
)

_BASE_WEIGHTS: dict[str, float] = {
    "value": 0.25,
    "growth": 0.20,
    "profitability": 0.25,
    "momentum": 0.15,
    "eps_revisions": 0.15,
}

# name -> (weights, cap_mode)
_VARIANTS: dict[str, tuple[dict[str, float], str]] = {
    "baseline": (dict(_BASE_WEIGHTS), "full"),
    "cap-none": (dict(_BASE_WEIGHTS), "none"),
    "cap-soft": (dict(_BASE_WEIGHTS), "soft"),
    "cap-no-growth": (dict(_BASE_WEIGHTS), "no_growth"),
    "cap-no-value": (dict(_BASE_WEIGHTS), "no_value"),
    "A-soft": ({"value": 0.25, "growth": 0.20, "profitability": 0.25, "momentum": 0.20, "eps_revisions": 0.10}, "full"),
    "A-soft2": ({"value": 0.25, "growth": 0.20, "profitability": 0.25, "momentum": 0.25, "eps_revisions": 0.05}, "full"),
    "A-soft+capsoft": ({"value": 0.25, "growth": 0.20, "profitability": 0.25, "momentum": 0.20, "eps_revisions": 0.10}, "soft"),
}


def _value_score_variant(d) -> float:
    """Value sub-score with the full P/E + P/B + PEG + FCF set (unchanged)."""
    pe = _linear(_non_negative(d.trailing_pe), best=10, worst=40)
    pb = _linear(_non_negative(d.price_to_book), best=1.0, worst=6.0)
    peg = _linear(_non_negative(d.peg_ratio), best=0.8, worst=3.0)
    fcf_yield = 50.0
    if d.free_cashflow is not None and d.market_cap is not None and d.market_cap > 0:
        fcf_yield = _linear(d.free_cashflow / d.market_cap, best=0.10, worst=0.00, default=50.0)
    return (pe * 0.35) + (pb * 0.20) + (peg * 0.25) + (fcf_yield * 0.20)


def _is_disqualified(
    growth: float, momentum: float, eps_rev: float,
    value: float, profitability: float, cap_mode: str,
) -> bool:
    """Replicate QuantStockScorer's disqualifying-grade gate, per mode.

    ``full``   = growth/momentum/eps_rev < 20, value/profitability < 15 (real)
    ``soft``   = same but 10 / 5 (relaxed)
    ``none``   = never
    ``no_growth`` = full minus the growth trigger
    ``no_value``  = full minus the value trigger
    """
    if cap_mode == "none":
        return False
    soft_thr = 10.0 if cap_mode == "soft" else 20.0
    hard_thr = 5.0 if cap_mode == "soft" else 15.0
    if cap_mode == "no_growth":
        return momentum < soft_thr or eps_rev < soft_thr or value < hard_thr or profitability < hard_thr
    if cap_mode == "no_value":
        return growth < soft_thr or momentum < soft_thr or eps_rev < soft_thr or profitability < hard_thr
    return (
        growth < soft_thr or momentum < soft_thr or eps_rev < soft_thr
        or value < hard_thr or profitability < hard_thr
    )


def _score_variant(
    scorer: QuantStockScorer,
    d,
    weights: dict[str, float],
    include_technical: bool,
    cap_mode: str,
) -> float:
    """Replica parametrizada de ``QuantStockScorer.score`` → total 0-100."""
    value = _value_score_variant(d)
    growth, _ = scorer._growth_score(d)
    profitability, _ = scorer._profitability_score(d)
    momentum, _ = scorer._momentum_score(d)
    eps_rev, _ = scorer._eps_revisions_score(d)
    if include_technical:
        tech, _ = _technical_score(d)
        momentum = momentum * 0.70 + tech * 0.30

    disqualified = _is_disqualified(growth, momentum, eps_rev, value, profitability, cap_mode)
    total = (
        value * weights["value"]
        + growth * weights["growth"]
        + profitability * weights["profitability"]
        + momentum * weights["momentum"]
        + eps_rev * weights["eps_revisions"]
    )
    return min(total, 50.0) if disqualified else total


def _cross_check(scorer: QuantStockScorer, sample) -> None:
    for d in sample:
        real = scorer.score(d, include_technical=True, si_data={}).total
        replica = _score_variant(scorer, d, _BASE_WEIGHTS, True, "full")
        if abs(real - replica) > 1e-9:
            sys.exit(f"Cross-check failed for {d.ticker}: real={real:.6f} replica={replica:.6f}")
    print(f"  cross-check OK ({len(sample)} assets: replica == scorer)")


def _window_snapshot_dates(
    data, benchmark: str, period: str, interval_months: int, reporting_lag: int
) -> list[datetime]:
    latest_common = _latest_common_end(data)
    if latest_common is None:
        sys.exit("No historical data available")
    end_date = latest_common - timedelta(days=reporting_lag)
    m = re.match(r"(\d+)y", period)
    window_years = 20.0 if period == "max" else (float(m.group(1)) if m else 5.0)
    start_date = end_date - timedelta(days=int(365.25 * window_years))
    snap_dates = _generate_snapshot_dates(end_date, start_date, interval_months)
    return [d for d in snap_dates if d + timedelta(days=int(30.5 * 12)) <= latest_common]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-r", "--region", default="us")
    ap.add_argument("-c", "--currency", default="USD")
    ap.add_argument("-a", "--asset-class", default="stocks")
    ap.add_argument("-t", "--tickers", default=None)
    ap.add_argument("-n", "--top-n", type=int, default=5)
    ap.add_argument("--period", default="5y")
    ap.add_argument("--interval-months", type=int, default=3)
    ap.add_argument("--min-market-cap", type=float, default=None)
    ap.add_argument("--max-workers", type=int, default=10)
    ap.add_argument("--reporting-lag", type=int, default=60)
    ap.add_argument("--benchmark", default=None)
    ap.add_argument("--windows", default="5y,3y")
    ap.add_argument("--no-cross-check", action="store_true")
    ap.add_argument("-o", "--json", default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    from investdaytip.cache import set_enabled
    set_enabled(False)

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

    all_tickers = list(dict.fromkeys(universe + [benchmark]))
    print(f"Fetching {len(all_tickers)} tickers (period=5y) …")
    data = _fetch_all_data(all_tickers, "5y", args.max_workers)

    bench_history = data.get(benchmark, {}).get("history")
    if bench_history is None or bench_history.empty:
        sys.exit(f"Could not fetch benchmark {benchmark}")

    scorer = QuantStockScorer()
    windows = [w.strip() for w in args.windows.split(",") if w.strip()]

    all_results: dict[str, dict[str, dict[str, float]]] = {}
    first_snapshot_assets = None

    for window in windows:
        snap_dates = _window_snapshot_dates(
            data, benchmark, window, args.interval_months, args.reporting_lag
        )
        if not snap_dates:
            print(f"  window {window}: no snapshots")
            continue
        print(
            f"  window {window}: {len(snap_dates)} snapshots "
            f"({snap_dates[0]:%Y-%m-%d} -> {snap_dates[-1]:%Y-%m-%d}, benchmark {benchmark})"
        )

        snapshots_by_variant: dict[str, list[BacktestSnapshot]] = {name: [] for name in _VARIANTS}

        for i, sd in enumerate(snap_dates, 1):
            quarter_date = _latest_available_quarter(sd, args.reporting_lag)
            if quarter_date is None:
                continue

            stocks = []
            for t in universe:
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
                stocks.append(sd_obj)

            if first_snapshot_assets is None and stocks:
                first_snapshot_assets = stocks[: min(20, len(stocks))]

            for name, (weights, cap_mode) in _VARIANTS.items():
                if name == "baseline":
                    scored = [
                        (scorer.score(d, include_technical=True, si_data={}).total, d.ticker)
                        for d in stocks
                    ]
                else:
                    scored = [
                        (_score_variant(scorer, d, weights, True, cap_mode), d.ticker)
                        for d in stocks
                    ]
                scored.sort(key=lambda x: x[0], reverse=True)
                picks = [t for _, t in scored[: args.top_n]]

                r6 = [_forward_return(data[t].get("history"), sd, 6) for t in picks if t in data]
                r12 = [_forward_return(data[t].get("history"), sd, 12) for t in picks if t in data]
                r6_valid = [r for r in r6 if r is not None]
                r12_valid = [r for r in r12 if r is not None]

                snapshots_by_variant[name].append(
                    BacktestSnapshot(
                        date=sd,
                        picks=[],
                        avg_return_6m=float(np.mean(r6_valid)) if r6_valid else None,
                        avg_return_12m=float(np.mean(r12_valid)) if r12_valid else None,
                        benchmark_return_6m=_forward_return(bench_history, sd, 6),
                        benchmark_return_12m=_forward_return(bench_history, sd, 12),
                    )
                )

            if not args.quiet:
                print(f"    scored {i}/{len(snap_dates)} ({sd:%Y-%m-%d})", file=sys.stderr)

        if not args.no_cross_check and first_snapshot_assets is not None:
            _cross_check(scorer, first_snapshot_assets)
            first_snapshot_assets = None

        window_results: dict[str, dict[str, float]] = {}
        for name in _VARIANTS:
            metrics = _compute_metrics(snapshots_by_variant[name])
            metrics["snapshots"] = len(snapshots_by_variant[name])
            window_results[name] = metrics
        all_results[window] = window_results

    columns = [
        ("cumulative_return", "Cum", "pct"),
        ("benchmark_cumulative_return", "Bench", "pct"),
        ("alpha", "Alpha", "pct"),
        ("sharpe", "Sharpe", "num"),
        ("benchmark_sharpe", "BSharpe", "num"),
        ("win_rate_6m", "Win6M", "pct"),
        ("win_rate_12m", "Win12M", "pct"),
        ("max_drawdown", "MaxDD", "pct"),
        ("snapshots", "Snaps", "int"),
    ]

    def _fmt(v, kind):
        if v is None:
            return "-"
        if kind == "pct":
            return f"{v * 100:.2f}%"
        if kind == "int":
            return f"{int(v)}"
        return f"{v:.2f}"

    for window, window_results in all_results.items():
        print(f"\n=== window {window} ===")
        header = f"{'variant':<16}" + "".join(f"{c[1]:>9}" for c in columns)
        print(header)
        print("-" * len(header))
        for name in _VARIANTS:
            m = window_results[name]
            line = f"{name:<16}"
            for key, _, kind in columns:
                line += f"{_fmt(m.get(key), kind):>9}"
            print(line)

    if args.json:
        payload = {
            "timestamp": datetime.now().isoformat(),
            "config": {
                "region": args.region,
                "currency": args.currency,
                "asset_class": args.asset_class,
                "tickers": args.tickers,
                "top_n": args.top_n,
                "interval_months": args.interval_months,
                "min_market_cap": args.min_market_cap,
                "reporting_lag": args.reporting_lag,
                "benchmark": benchmark,
                "variants": {k: {"weights": w, "cap": c} for k, (w, c) in _VARIANTS.items()},
            },
            "results": all_results,
        }
        with open(args.json, "w") as fh:
            json.dump(payload, fh, indent=2)
        print(f"\nResults saved to: {args.json}")


if __name__ == "__main__":
    main()
