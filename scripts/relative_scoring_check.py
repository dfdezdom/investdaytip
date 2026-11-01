#!/usr/bin/env python3
"""Relative-vs-absolute scoring check — do cross-sectional ranks beat thresholds?

Hypothesis: the quant model loses top-5 alpha on the broad universe because its
absolute ``_linear(best, worst)`` thresholds rank badly *across sectors* (a
cheap bank with low growth out-ranks an expensive tech). Replacing each
continuous sub-metric's absolute normalization with a **cross-sectional
percentile rank** (computed per snapshot over the whole universe) makes the
model sector-relative while keeping the exact same weights and sub-weights.

Two scorers, one download:
- **absolute** = the real ``QuantStockScorer.score`` (include_technical=True).
- **relative** = same weights/sub-weights/disqualification, but each continuous
  sub-metric is its percentile rank within the snapshot's cross-section
  (lower-is-better metrics inverted; missing → neutral 50, exactly as ``_linear``).

Both evaluated at several N (5/10/15/20/25) over the full 277-ticker pool.

Uso:
    python scripts/relative_scoring_check.py -n 5 10 15 20 25 --min-market-cap 0
"""
from __future__ import annotations

import argparse
import math
import re
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
from investdaytip.recommender import _build_universe  # noqa: E402
from investdaytip.scoring import (  # noqa: E402
    QuantStockScorer,
    _non_negative,
    _technical_score,
)

_WEIGHTS = {"value": 0.25, "growth": 0.20, "profitability": 0.25, "momentum": 0.15, "eps_revisions": 0.15}
_DISQUALIFY_SOFT = 20.0
_DISQUALIFY_HARD = 15.0


def _pct_scores(values: list[float | None], higher_better: bool) -> list[float]:
    """Cross-sectional percentile rank (0-100) of *values*; None → neutral 50."""
    n = len(values)
    out = [50.0] * n
    idx = [(i, v) for i, v in enumerate(values) if v is not None and math.isfinite(v)]
    if len(idx) < 2:
        return out
    idx.sort(key=lambda x: x[1])
    m = len(idx)
    for rank, (i, _) in enumerate(idx):
        pct = rank / (m - 1) * 100.0
        out[i] = pct if higher_better else 100.0 - pct
    return out


def _improvement(s) -> float:
    return QuantStockScorer._improvement_score(s)


def _relative_totals(stocks: list) -> list[float]:
    """Relative (percentile-based) totals for one cross-section, aligned to *stocks*."""
    # Raw metrics per stock (None = missing → neutral in _pct_scores).
    pe_v = [_non_negative(s.trailing_pe) for s in stocks]
    pb_v = [_non_negative(s.price_to_book) for s in stocks]
    peg_v = [_non_negative(s.peg_ratio) for s in stocks]
    fcf_v = [
        (s.free_cashflow / s.market_cap)
        if (s.free_cashflow is not None and s.market_cap and s.market_cap > 0)
        else None
        for s in stocks
    ]
    eg_v = [s.earnings_growth for s in stocks]
    rg_v = [s.revenue_growth for s in stocks]
    roe_v = [s.return_on_equity for s in stocks]
    roa_v = [s.return_on_assets for s in stocks]
    mg_v = [s.profit_margin for s in stocks]
    pvs_v = [s.price_vs_sma200 for s in stocks]
    r12_v = []
    for s in stocks:
        r = s.return_12m
        if r is not None and s.return_1m is not None and s.return_1m > -0.99:
            r = (1.0 + r) / (1.0 + s.return_1m) - 1.0
        r12_v.append(r)
    sl_v = [s.sma200_slope for s in stocks]
    es_v = [s.eps_surprise for s in stocks]

    pe = _pct_scores(pe_v, False)
    pb = _pct_scores(pb_v, False)
    peg = _pct_scores(peg_v, False)
    fcf = _pct_scores(fcf_v, True)
    eg = _pct_scores(eg_v, True)
    rg = _pct_scores(rg_v, True)
    roe = _pct_scores(roe_v, True)
    roa = _pct_scores(roa_v, True)
    mg = _pct_scores(mg_v, True)
    pvs = _pct_scores(pvs_v, True)
    r12 = _pct_scores(r12_v, True)
    sl = _pct_scores(sl_v, True)
    es = _pct_scores(es_v, True)

    totals: list[float] = []
    for i, s in enumerate(stocks):
        value = pe[i] * 0.35 + pb[i] * 0.20 + peg[i] * 0.25 + fcf[i] * 0.20
        growth = eg[i] * 0.55 + rg[i] * 0.45
        improving = _improvement(s)
        profitability = roe[i] * 0.30 + mg[i] * 0.25 + roa[i] * 0.15 + improving * 0.30
        momentum = pvs[i] * 0.35 + r12[i] * 0.40 + sl[i] * 0.25
        tech, _ = _technical_score(s)
        momentum = momentum * 0.70 + tech * 0.30
        eps_rev = es[i]

        disqualified = (
            growth < _DISQUALIFY_SOFT
            or momentum < _DISQUALIFY_SOFT
            or eps_rev < _DISQUALIFY_SOFT
            or value < _DISQUALIFY_HARD
            or profitability < _DISQUALIFY_HARD
        )
        total = (
            value * _WEIGHTS["value"]
            + growth * _WEIGHTS["growth"]
            + profitability * _WEIGHTS["profitability"]
            + momentum * _WEIGHTS["momentum"]
            + eps_rev * _WEIGHTS["eps_revisions"]
        )
        totals.append(min(total, 50.0) if disqualified else total)
    return totals


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

    universe = _build_universe(None, "stocks", "us", "USD")
    print(f"universe {len(universe)} tickers")
    all_tickers = list(dict.fromkeys(universe + [args.benchmark]))
    print(f"Fetching {len(all_tickers)} tickers (period=5y) …")
    data = _fetch_all_data(all_tickers, "5y", args.max_workers)

    bench_history = data.get(args.benchmark, {}).get("history")
    if bench_history is None or bench_history.empty:
        sys.exit(f"Could not fetch benchmark {args.benchmark}")

    scorer = QuantStockScorer()

    for window in [w.strip() for w in args.windows.split(",") if w.strip()]:
        snap_dates = _snapshot_dates(data, window, 3, args.reporting_lag)
        if not snap_dates:
            continue
        print(f"  window {window}: {len(snap_dates)} snapshots")

        snaps_abs: dict[int, list[BacktestSnapshot]] = {n: [] for n in args.top_ns}
        snaps_rel: dict[int, list[BacktestSnapshot]] = {n: [] for n in args.top_ns}

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

            abs_scores = [scorer.score(s, include_technical=True, si_data={}).total for s in stocks]
            rel_scores = _relative_totals(stocks)

            abs_rank = sorted(
                zip(abs_scores, (s.ticker for s in stocks), strict=True), key=lambda x: x[0], reverse=True
            )
            rel_rank = sorted(
                zip(rel_scores, (s.ticker for s in stocks), strict=True), key=lambda x: x[0], reverse=True
            )

            for n in args.top_ns:
                for snaps, ranked in ((snaps_abs, abs_rank), (snaps_rel, rel_rank)):
                    picks = [t for _, t in ranked[:n]]
                    r6 = [_forward_return(data[t].get("history"), sd, 6) for t in picks if t in data]
                    r12 = [_forward_return(data[t].get("history"), sd, 12) for t in picks if t in data]
                    r6v = [r for r in r6 if r is not None]
                    r12v = [r for r in r12 if r is not None]
                    snaps[n].append(
                        BacktestSnapshot(
                            date=sd, picks=[],
                            avg_return_6m=float(np.mean(r6v)) if r6v else None,
                            avg_return_12m=float(np.mean(r12v)) if r12v else None,
                            benchmark_return_6m=_forward_return(bench_history, sd, 6),
                            benchmark_return_12m=_forward_return(bench_history, sd, 12),
                        )
                    )

            if not args.quiet:
                print(f"    scored {i}/{len(snap_dates)} ({sd:%Y-%m-%d})", file=sys.stderr)

        print(f"\n=== window {window} ===")
        print(f"{'N':>4} {'abs Alpha':>10} {'rel Alpha':>10} {'Δ':>8} | {'abs Sharpe':>10} {'rel Sharpe':>10} | {'abs Win12M':>10} {'rel Win12M':>10}")
        print("-" * 82)
        for n in args.top_ns:
            a = _compute_metrics(snaps_abs[n])
            r = _compute_metrics(snaps_rel[n])
            da = r["alpha"] - a["alpha"]
            print(
                f"{n:>4} {a['alpha']*100:>9.2f}% {r['alpha']*100:>9.2f}% {da*100:>+7.2f}% | "
                f"{a['sharpe']:>9.2f} {r['sharpe']:>9.2f} | "
                f"{a['win_rate_12m']*100:>9.1f}% {r['win_rate_12m']*100:>9.1f}%"
            )


if __name__ == "__main__":
    main()
