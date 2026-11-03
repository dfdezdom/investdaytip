#!/usr/bin/env python3
"""PIT accounting-factor sweep — re-test the accounting factors on StockFit PIT.

The 2026-11-01 sweep (`new_signal_check.py`) found only `asset_growth` among the
accounting candidates, but its coverage was 7/16 snapshots on the classic path
(yfinance annual statements do not reach the early fiscal years). The local PIT
snapshot carries exact `dateFiled` statements from FY2021 on, so the 3y window
is fully covered and 5y improves — this re-runs the IC + before/after with that
better coverage, on top of the current six-factor model (which already includes
`asset_growth` at 10%).

Candidates (raw → 0-100 via ``_linear``; lower-is-better passes best < worst):
- accruals        = (NetIncome − OperatingCashFlow) / TotalAssets   [lower better]
- gross_prof      = GrossProfit / TotalAssets                       [higher better]
- net_issuance    = (shares − shares_prev) / shares_prev            [lower better]
- ocf_ni          = OperatingCashFlow / NetIncome (NI > 0)          [higher better]
- interest_cov    = EBIT / interestExpense (interest > 0)           [higher better]
- capex_int       = |capitalExpenditure| / TotalAssets              [lower better]

Each candidate is a convex tilt ``new = (1-w)·base + w·candidate`` where base is
the production six-factor score; the disqualifying-grade cap is re-applied.
Full metrics (Alpha, Sharpe, Win12M, MaxDD) per N and window.

Uso:
    python scripts/pit_factor_sweep.py -r us --min-market-cap 0 \
        -n 5 10 25 --windows 5y,3y --extra-weights 0.10
"""
from __future__ import annotations

import argparse
import math
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))

from investdaytip.backtest import (  # noqa: E402
    BacktestSnapshot,
    _build_pit_stock_data,
    _compute_metrics,
    _fetch_all_data,
    _fetch_pit_statements,
    _forward_return,
    _generate_snapshot_dates,
    _latest_common_end,
)
from investdaytip.data_source_stockfit import (  # noqa: E402
    check_pit_access,
    pit_fact_asof,
)
from investdaytip.recommender import _build_universe  # noqa: E402
from investdaytip.scoring import (  # noqa: E402
    STOCK_WEIGHTS_QUANT,
    QuantStockScorer,
    _linear,
    _technical_score,
)

_CAND: dict[str, tuple[float, float]] = {
    "accruals": (-0.10, 0.10),
    "gross_prof": (0.50, 0.05),
    "net_issuance": (-0.02, 0.10),
    "ocf_ni": (2.0, 0.5),
    "interest_cov": (10.0, 1.5),
    "capex_int": (0.02, 0.15),
    # New quality/efficiency signals (StockFit key metrics, computed from the
    # PIT statements so they stay strictly point-in-time).
    "roic": (0.25, 0.02),
    "sbc_rev": (0.01, 0.15),
    "net_debt_ebitda": (0.0, 4.0),
    "fcf_ni": (1.0, 0.3),
    "asset_turn": (2.0, 0.2),
}

_DISQ_SOFT, _DISQ_HARD = 20.0, 15.0


def _raw_candidates(pit, sd: datetime) -> dict[str, Optional[float]]:
    def cur(series, key):
        return pit_fact_asof(series, key, sd)[0]

    ni = cur(pit.income, "netIncome")
    ocf = cur(pit.cash_flow, "operatingCashFlow")
    ta = cur(pit.balance, "assets")
    gp = cur(pit.income, "grossProfit")
    ebit = cur(pit.income, "ebit")
    ie = cur(pit.income, "interestExpense")
    capex = cur(pit.cash_flow, "capitalExpenditure")
    sh, sh_prev = pit_fact_asof(pit.balance, "sharesOutstanding", sd)

    # new quality/efficiency inputs
    ebitda = cur(pit.income, "ebitda")
    rev = cur(pit.income, "revenue")
    tax = cur(pit.income, "incomeTaxExpense")
    pretax = cur(pit.income, "incomeBeforeTax")
    eq = cur(pit.balance, "stockholdersEquity")
    td2 = cur(pit.balance, "totalDebt")
    cash = cur(pit.balance, "cash")
    sbc = cur(pit.cash_flow, "stockBasedCompensation")
    fcf = cur(pit.cash_flow, "freeCashFlow")

    tax_rate = (tax / pretax) if (tax is not None and pretax and 0 < pretax and 0 <= tax / pretax <= 0.5) else 0.21

    return {
        "accruals": (ni - ocf) / ta if (ni is not None and ocf is not None and ta and ta > 0) else None,
        "gross_prof": gp / ta if (gp is not None and ta and ta > 0) else None,
        "net_issuance": (sh - sh_prev) / sh_prev if (sh is not None and sh_prev and sh_prev > 0) else None,
        "ocf_ni": ocf / ni if (ocf is not None and ni is not None and ni > 0) else None,
        "interest_cov": ebit / ie if (ebit is not None and ie is not None and ie > 0) else None,
        "capex_int": abs(capex) / ta if (capex is not None and ta and ta > 0) else None,
        "roic": (ebit * (1 - tax_rate) / (eq + td2)) if (ebit is not None and eq is not None and td2 is not None and (eq + td2) > 0) else None,
        "sbc_rev": sbc / rev if (sbc is not None and rev and rev > 0) else None,
        "net_debt_ebitda": (td2 - cash) / ebitda if (td2 is not None and cash is not None and ebitda and ebitda > 0) else None,
        "fcf_ni": fcf / ni if (fcf is not None and ni is not None and ni > 0) else None,
        "asset_turn": rev / ta if (rev is not None and ta and ta > 0) else None,
    }


def _cand_score(name: str, raw: dict[str, Optional[float]]) -> float:
    best, worst = _CAND[name]
    v = raw.get(name)
    return _linear(v, best, worst, default=50.0) if v is not None else 50.0


def _factor_scores(scorer: QuantStockScorer, d) -> tuple[float, ...]:
    value, _ = scorer._value_score(d)
    growth, _ = scorer._growth_score(d)
    profitability, _ = scorer._profitability_score(d)
    momentum, _ = scorer._momentum_score(d)
    eps_rev, _ = scorer._eps_revisions_score(d)
    tech, _ = _technical_score(d)
    momentum = momentum * 0.70 + tech * 0.30
    asset_growth, _ = scorer._asset_growth_score(d)
    return value, growth, profitability, momentum, eps_rev, asset_growth


def _combine(fs, weights, extra: Optional[float], w: float) -> float:
    value, growth, profitability, momentum, eps_rev, asset_growth = fs
    disq = (
        growth < _DISQ_SOFT or momentum < _DISQ_SOFT or eps_rev < _DISQ_SOFT
        or value < _DISQ_HARD or profitability < _DISQ_HARD
    )
    k = {key: val * (1.0 - w) for key, val in weights.items()}
    total = (
        value * k["value"] + growth * k["growth"] + profitability * k["profitability"]
        + momentum * k["momentum"] + eps_rev * k["eps_revisions"]
        + asset_growth * k["asset_growth"]
    )
    if extra is not None:
        total += extra * w
    return min(total, 50.0) if disq else total


def _spearman(x, y) -> Optional[float]:
    pairs = [(a, b) for a, b in zip(x, y, strict=True) if a is not None and b is not None and math.isfinite(a) and math.isfinite(b)]
    if len(pairs) < 5:
        return None
    s = pd.Series([p[0] for p in pairs])
    r = pd.Series([p[1] for p in pairs])
    if s.nunique() < 2 or r.nunique() < 2:
        return None
    return float(s.rank().corr(r.rank()))


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
    ap.add_argument("-t", "--tickers", default=None)
    ap.add_argument("-n", "--top-ns", nargs="+", type=int, default=[5, 10, 25])
    ap.add_argument("--candidates", nargs="+", default=list(_CAND))
    ap.add_argument("--extra-weights", nargs="+", type=float, default=[0.10])
    ap.add_argument("--min-market-cap", type=float, default=None)
    ap.add_argument("--max-workers", type=int, default=10)
    ap.add_argument("--reporting-lag", type=int, default=60)
    ap.add_argument("--windows", default="5y,3y")
    ap.add_argument("--benchmark", default="SPY")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    from investdaytip.cache import set_enabled
    set_enabled(False)
    check_pit_access()

    universe = list(args.tickers.split()) if args.tickers else _build_universe(None, "stocks", args.region, "all")
    if args.min_market_cap is None:
        args.min_market_cap = 0.0 if args.tickers else 2_000_000_000.0

    print(f"Loading PIT statements for {len(universe)} tickers …")
    pit_data = _fetch_pit_statements(universe, args.max_workers)
    if not pit_data:
        sys.exit("No PIT statements available")
    universe = list(pit_data.keys())
    print(f"PIT pool: {len(universe)} tickers")

    all_tickers = list(dict.fromkeys(universe + [args.benchmark]))
    print(f"Fetching prices for {len(all_tickers)} tickers …")
    data = _fetch_all_data(all_tickers, "5y", args.max_workers)
    bench = data.get(args.benchmark, {}).get("history")
    if bench is None or bench.empty:
        sys.exit("no benchmark")

    scorer = QuantStockScorer()
    weights = STOCK_WEIGHTS_QUANT
    variants: list[tuple[str, float]] = [("baseline", 0.0)]
    for c in args.candidates:
        for w in args.extra_weights:
            variants.append((c, w))

    for window in [w.strip() for w in args.windows.split(",") if w.strip()]:
        snaps = _snapshot_dates(data, window, 3, args.reporting_lag)
        if not snaps:
            continue
        print(f"\n=== window {window} ({len(snaps)} snapshots) ===")
        snaps_by: dict[tuple[str, float, int], list[BacktestSnapshot]] = {
            (c, w, n): [] for (c, w) in variants for n in args.top_ns
        }
        ic: dict[str, list[float]] = {c: [] for c in args.candidates}
        used = 0

        for sd in snaps:
            rows: list[tuple[str, object, tuple, dict, Optional[float]]] = []
            for t in universe:
                td = data.get(t)
                if td is None or "error" in td:
                    continue
                hist = td.get("history")
                if hist is None or hist.empty:
                    continue
                # No PIT fundamentals yet (filing after the snapshot): skip,
                # so an early snapshot cannot be filled with neutral scores.
                if pit_fact_asof(pit_data[t].income, "netIncome", sd)[0] is None:
                    continue
                sd_obj = _build_pit_stock_data(
                    ticker=t, info=td.get("info", {}), price_history=hist, snapshot_date=sd,
                    pit=pit_data[t], dividends=td.get("dividends", pd.Series(dtype=float)),
                    earnings_dates=td.get("earnings_dates"), reporting_lag_days=args.reporting_lag,
                )
                if args.min_market_cap > 0 and (sd_obj.market_cap is None or sd_obj.market_cap < args.min_market_cap):
                    continue
                fs = _factor_scores(scorer, sd_obj)
                raw = _raw_candidates(pit_data[t], sd)
                fwd = _forward_return(hist, sd, 6)
                rows.append((t, sd_obj, fs, raw, fwd))

            if len(rows) < 2:
                continue
            used += 1

            if used == 1 and not args.quiet:
                for t, sd_obj, fs, _, _ in rows[:20]:
                    real = scorer.score(sd_obj, include_technical=True, si_data={}).total
                    rep = _combine(fs, weights, None, 0.0)
                    if abs(real - rep) > 1e-9:
                        sys.exit(f"baseline mismatch {t}: real={real:.6f} rep={rep:.6f}")
                print("    baseline replica cross-check OK", file=sys.stderr)

            for c in args.candidates:
                s = [_cand_score(c, raw) if raw.get(c) is not None else None for _, _, _, raw, _ in rows]
                fwds = [fwd for *_, fwd in rows]
                v = _spearman(s, fwds)
                if v is not None:
                    ic[c].append(v)

            for (cname, w) in variants:
                scored = []
                for t, _, fs, raw, _ in rows:
                    extra = _cand_score(cname, raw) if (w > 0 and cname != "baseline") else None
                    scored.append((_combine(fs, weights, extra, w), t))
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
                print(f"    scored {used} ({sd:%Y-%m-%d}): {len(rows)} assets", file=sys.stderr)

        print(f"(snapshots with fundamentals: {used})")
        print("candidate IC (mean / hit / snaps):")
        for c in args.candidates:
            vals = ic[c]
            if vals:
                print(f"  {c:<14} {sum(vals)/len(vals):>+7.3f}  {sum(1 for x in vals if x>0)/len(vals):>5.0%}  {len(vals):>3}")
            else:
                print(f"  {c:<14}      n/a")

        for c in args.candidates:
            header = f"{'N':>4} {'baseline':>10}" + "".join(f"{c + '@' + f'{int(w*100)}%':>16}" for w in args.extra_weights)
            print(f"\n{c}\n{header}")
            print("-" * len(header))
            for n in args.top_ns:
                b = _compute_metrics(snaps_by[("baseline", 0.0, n)])
                line = f"{n:>4} {b['alpha']*100:>9.2f}%"
                for w in args.extra_weights:
                    line += f"{_compute_metrics(snaps_by[(c, w, n)])['alpha']*100:>15.2f}%"
                print(line)
                for w in args.extra_weights:
                    m = _compute_metrics(snaps_by[(c, w, n)])
                    print(f"      @{int(w*100)}% Sharpe {m['sharpe']:.2f} Win12M {m['win_rate_12m']*100:.1f}% MaxDD {m['max_drawdown']*100:.1f}%")


if __name__ == "__main__":
    main()
