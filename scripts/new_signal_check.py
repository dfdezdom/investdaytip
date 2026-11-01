#!/usr/bin/env python3
"""New-signal check — do orthogonal accounting factors add alpha?

Computed entirely from the annual statements the backtest already downloads
(no extra API calls). Each candidate is (a) measured with a univariate factor
IC (Spearman vs forward 6M) and (b) tested as an extra factor at one or more
weights that rescales the existing 5 weights by (1 - w) — i.e. a convex tilt
``new = (1-w)·baseline + w·candidate`` (the baseline ranking is preserved by
the scaling, so the delta is purely the candidate's tilt).

Candidates (raw → 0-100 via the model's own ``_linear``):
- accruals        = (NetIncome − OperatingCashFlow) / TotalAssets   [lower better]
- gross_prof      = GrossProfit / TotalAssets                       [higher better]
- asset_growth    = (TA − TA_prev) / TA_prev                        [lower better]
- net_issuance    = (shares − shares_prev) / shares_prev            [lower better]
- ocf_ni          = OperatingCashFlow / NetIncome (NI > 0)          [higher better]

Uso:
    python scripts/new_signal_check.py -n 5 10 25 --windows 5y
    python scripts/new_signal_check.py --candidates asset_growth \
        --extra-weights 0.05 0.10 0.15 0.20 -n 5 10 25 --windows 5y
"""
from __future__ import annotations

import argparse
import math
import re
import sys
from datetime import timedelta
from pathlib import Path
from typing import Optional

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
from investdaytip.financial_health import annual_facts  # noqa: E402
from investdaytip.recommender import _build_universe  # noqa: E402
from investdaytip.scoring import (  # noqa: E402
    QuantStockScorer,
    _linear,
    _technical_score,
)

_BASE_W = {"value": 0.25, "growth": 0.20, "profitability": 0.25, "momentum": 0.15, "eps_revisions": 0.15}
_DISQ_SOFT, _DISQ_HARD = 20.0, 15.0

_CAND_LINEAR: dict[str, tuple[float, float]] = {
    "accruals": (-0.10, 0.10),
    "gross_prof": (0.50, 0.05),
    "asset_growth": (-0.05, 0.30),
    "net_issuance": (-0.02, 0.10),
    "ocf_ni": (2.0, 0.5),
}


def _raw_candidates(inc, bs, cf, quarter_date) -> dict[str, Optional[float]]:
    cur = annual_facts(inc, bs, cf, quarter_date)
    prev = annual_facts(inc, bs, cf, quarter_date, years_back=1)
    ni, ocf, ta = cur.get("NetIncome"), cur.get("OperatingCashFlow"), cur.get("TotalAssets")
    gp = cur.get("GrossProfit")
    ta_p = prev.get("TotalAssets")
    sh, sh_p = cur.get("OrdinarySharesNumber"), prev.get("OrdinarySharesNumber")
    return {
        "accruals": ((ni - ocf) / ta) if (ni is not None and ocf is not None and ta and ta > 0) else None,
        "gross_prof": (gp / ta) if (gp is not None and ta and ta > 0) else None,
        "asset_growth": ((ta - ta_p) / ta_p) if (ta is not None and ta_p and ta_p > 0) else None,
        "net_issuance": ((sh - sh_p) / sh_p) if (sh is not None and sh_p and sh_p > 0) else None,
        "ocf_ni": (ocf / ni) if (ocf is not None and ni is not None and ni > 0) else None,
    }


def _cand_score(name: str, raw: dict[str, Optional[float]]) -> float:
    best, worst = _CAND_LINEAR[name]
    v = raw.get(name)
    return _linear(v, best, worst, default=50.0) if v is not None else 50.0


def _factor_scores(scorer: QuantStockScorer, d, include_technical: bool = True):
    value, _ = scorer._value_score(d)
    growth, _ = scorer._growth_score(d)
    profitability, _ = scorer._profitability_score(d)
    momentum, _ = scorer._momentum_score(d)
    eps_rev, _ = scorer._eps_revisions_score(d)
    if include_technical:
        tech, _ = _technical_score(d)
        momentum = momentum * 0.70 + tech * 0.30
    return value, growth, profitability, momentum, eps_rev


def _combine(value, growth, profitability, momentum, eps_rev, weights, extra, extra_w) -> float:
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
    ap.add_argument("-t", "--tickers", default=None)
    ap.add_argument("-n", "--top-ns", nargs="+", type=int, default=[5, 10, 25])
    ap.add_argument("--candidates", nargs="+", default=list(_CAND_LINEAR))
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

    universe = list(args.tickers.split()) if args.tickers else _build_universe(None, "stocks", "us", "USD")
    if args.min_market_cap is None:
        args.min_market_cap = 0.0 if args.tickers else 2_000_000_000.0
    all_tickers = list(dict.fromkeys(universe + [args.benchmark]))
    print(f"Fetching {len(all_tickers)} tickers …")
    data = _fetch_all_data(all_tickers, "5y", args.max_workers)

    bench = data.get(args.benchmark, {}).get("history")
    if bench is None or bench.empty:
        sys.exit("no benchmark")

    scorer = QuantStockScorer()
    candidates = args.candidates
    weights = args.extra_weights

    # variant key = (candidate or "baseline", weight)
    variants: list[tuple[str, float]] = [("baseline", 0.0)]
    for c in candidates:
        for w in weights:
            variants.append((c, w))

    for window in [w.strip() for w in args.windows.split(",") if w.strip()]:
        snaps = _snapshot_dates(data, window, 3, args.reporting_lag)
        if not snaps:
            continue
        print(f"  window {window}: {len(snaps)} snapshots")

        snaps_by: dict[tuple[str, float, int], list[BacktestSnapshot]] = {
            (c, w, n): [] for (c, w) in variants for n in args.top_ns
        }
        window_ic: dict[str, list[float]] = {c: [] for c in candidates}

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
                raw = _raw_candidates(
                    td.get("income_stmt", pd.DataFrame()), td.get("balance_sheet", pd.DataFrame()),
                    td.get("cash_flow", pd.DataFrame()), qd,
                )
                fs = _factor_scores(scorer, sd_obj, include_technical=True)
                fwd = _forward_return(hist, sd, 6)
                rows.append((t, sd_obj, fs, raw, fwd))

            if not rows:
                continue

            if i == 1 and not args.quiet:
                for t, sd_obj, fs, _, _ in rows[:20]:
                    real = scorer.score(sd_obj, include_technical=True, si_data={}).total
                    rep = _combine(*fs, _BASE_W, None, 0.0)
                    if abs(real - rep) > 1e-9:
                        sys.exit(f"baseline mismatch {t}: real={real:.6f} rep={rep:.6f}")
                print("    baseline replica cross-check OK", file=sys.stderr)

            for c in candidates:
                scores = [_cand_score(c, raw) if raw.get(c) is not None else None for _, _, _, raw, _ in rows]
                fwds = [fwd for *_, fwd in rows]
                ic = _spearman(scores, fwds)
                if ic is not None:
                    window_ic[c].append(ic)

            for (cname, w) in variants:
                ws = {k: v * (1.0 - w) for k, v in _BASE_W.items()} if w > 0 else _BASE_W
                scored = []
                for t, _, fs, raw, _ in rows:
                    extra = _cand_score(cname, raw) if (w > 0 and cname != "baseline") else None
                    scored.append((_combine(*fs, ws, extra, w), t))
                scored.sort(key=lambda x: x[0], reverse=True)
                ranked = [t for _, t in scored]
                for n in args.top_ns:
                    picks = ranked[:n]
                    r6 = [_forward_return(data[t].get("history"), sd, 6) for t in picks if t in data]
                    r6v = [r for r in r6 if r is not None]
                    snaps_by[(cname, w, n)].append(
                        BacktestSnapshot(
                            date=sd, picks=[], avg_return_6m=float(np.mean(r6v)) if r6v else None,
                            avg_return_12m=None, benchmark_return_6m=_forward_return(bench, sd, 6),
                            benchmark_return_12m=None,
                        )
                    )

            if not args.quiet:
                print(f"    scored {i}/{len(snaps)} ({sd:%Y-%m-%d})", file=sys.stderr)

        print(f"\n=== window {window} ===")
        print("candidate IC (mean / hit / snaps):")
        for c in candidates:
            ics = window_ic[c]
            if ics:
                mean_ic = sum(ics) / len(ics)
                hit = sum(1 for x in ics if x > 0) / len(ics)
                print(f"  {c:<14} {mean_ic:>+7.3f}  {hit:>5.0%}  {len(ics):>3}")
            else:
                print(f"  {c:<14}      n/a")

        base = {n: _compute_metrics(snaps_by[("baseline", 0.0, n)])["alpha"] for n in args.top_ns}
        for c in candidates:
            header = f"{'N':>4} {'baseline':>10}" + "".join(f"{c + '@' + f'{int(w*100)}%':>16}" for w in weights)
            print(f"\n{c}\n{header}")
            print("-" * len(header))
            for n in args.top_ns:
                line = f"{n:>4} {base[n]*100:>9.2f}%"
                for w in weights:
                    a = _compute_metrics(snaps_by[(c, w, n)])["alpha"]
                    line += f"{a*100:>15.2f}%"
                print(line)


if __name__ == "__main__":
    main()
