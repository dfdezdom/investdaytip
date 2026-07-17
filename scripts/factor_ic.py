#!/usr/bin/env python3
"""Factor IC analysis — which scoring factors actually predict forward returns?

For every quarterly snapshot, computes the cross-sectional Spearman rank
correlation (Information Coefficient, IC) between each raw metric / factor
score and the forward 6-month return across the universe.  Reports the mean
IC and the share of snapshots with positive IC ("hit rate").

Use this BEFORE tuning scoring weights: a factor with mean IC ~ 0 adds noise,
not signal.  Candidate metrics not yet in the model (return_6m, 12-1 momentum,
52w-high proximity, volatility) are included for evaluation.

Usage:
    python scripts/factor_ic.py -t "AAPL MSFT GOOGL" --period 3y
    python scripts/factor_ic.py -t "..." --period 3y --csv ic.csv
"""
from __future__ import annotations

import argparse
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from investdaytip.backtest import (  # noqa: E402
    _build_historical_stock_data,
    _fetch_all_data,
    _forward_return,
    _generate_snapshot_dates,
    _latest_available_quarter,
    _latest_common_end,
)
from investdaytip.scoring import QuantStockScorer  # noqa: E402


def _spearman(x: list[Optional[float]], y: list[Optional[float]]) -> Optional[float]:
    """Spearman rank correlation over pairs where both values are finite."""
    pairs = [
        (a, b)
        for a, b in zip(x, y, strict=True)
        if a is not None and b is not None and math.isfinite(a) and math.isfinite(b)
    ]
    if len(pairs) < 5:  # too few points for a meaningful rank correlation
        return None
    s = pd.Series([p[0] for p in pairs])
    r = pd.Series([p[1] for p in pairs])
    if s.nunique() < 2 or r.nunique() < 2:
        return None
    return float(s.rank().corr(r.rank()))


def _pct_off_high(close: pd.Series) -> Optional[float]:
    """price / max(close, 252d) - 1  (0 = at 52w high, negative below)."""
    clean = close.dropna()
    if len(clean) < 2:
        return None
    window = clean.iloc[-252:]
    high = float(window.max())
    return (float(clean.iloc[-1]) / high) - 1.0 if high > 0 else None


def _period_ret(close: pd.Series, days: int) -> Optional[float]:
    clean = close.dropna()
    if len(clean) < days + 1:
        return None
    past = float(clean.iloc[-days - 1])
    return (float(clean.iloc[-1]) / past) - 1.0 if past > 0 else None


def _annualized_vol(close: pd.Series) -> Optional[float]:
    clean = close.dropna()
    if len(clean) < 60:
        return None
    ret = clean.iloc[-252:].pct_change().dropna()
    if len(ret) < 30:
        return None
    return float(ret.std() * math.sqrt(252))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-t", "--tickers", required=True, help="Space-separated tickers")
    ap.add_argument("--period", default="3y")
    ap.add_argument("--interval-months", type=int, default=3)
    ap.add_argument("--reporting-lag", type=int, default=60)
    ap.add_argument("--max-workers", type=int, default=10)
    ap.add_argument("--csv", default=None, help="Optional CSV output path")
    ap.add_argument("--no-cache", action="store_true",
                    help="Disable cache (required when changing --period: cached "
                         "history has no notion of period and would poison the run)")
    args = ap.parse_args()

    if args.no_cache:
        from investdaytip.cache import set_enabled
        set_enabled(False)

    tickers = args.tickers.split()
    print(f"Fetching {len(tickers)} tickers (period={args.period}) …")
    data = _fetch_all_data(tickers, args.period, args.max_workers)

    latest_common = _latest_common_end(data)
    if latest_common is None:
        sys.exit("No historical data available")
    end_date = latest_common - timedelta(days=args.reporting_lag)
    import re
    m = re.match(r"(\d+)y", args.period)
    window_years = 20.0 if args.period == "max" else (float(m.group(1)) if m else 5.0)
    start_date = end_date - timedelta(days=int(365.25 * window_years))
    snap_dates = _generate_snapshot_dates(end_date, start_date, args.interval_months)
    snap_dates = [d for d in snap_dates if d + timedelta(days=365) <= latest_common]
    print(f"{len(snap_dates)} snapshots from {snap_dates[0]:%Y-%m-%d} to {snap_dates[-1]:%Y-%m-%d}")

    scorer = QuantStockScorer()

    # metric name -> per-snapshot IC list
    ic_rows: dict[str, list[tuple[datetime, float]]] = {}
    metric_names: list[str] = []

    for sd in snap_dates:
        quarter_date = _latest_available_quarter(sd, args.reporting_lag)
        if quarter_date is None:
            continue

        rows: list[dict[str, Optional[float]]] = []
        for t in tickers:
            td = data.get(t)
            if td is None or "error" in td:
                continue
            hist = td.get("history")
            if hist is None or hist.empty:
                continue
            stock = _build_historical_stock_data(
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
            fwd = _forward_return(hist, sd, 6)
            if fwd is None:
                continue

            value, _ = scorer._value_score(stock)
            growth, _ = scorer._growth_score(stock)
            profitability, _ = scorer._profitability_score(stock)
            momentum, _ = scorer._momentum_score(stock)
            eps_rev, _ = scorer._eps_revisions_score(stock)
            scored = scorer.score(stock, include_technical=True, si_data={})

            close = hist.loc[: pd.Timestamp(sd)]["Close"]
            vol_1y = _annualized_vol(close)
            r12x1 = (
                ((1 + stock.return_12m) / (1 + stock.return_1m) - 1.0)
                if (stock.return_12m is not None and stock.return_1m is not None and stock.return_1m > -0.99)
                else None
            )
            fcf_yield = (
                stock.free_cashflow / stock.market_cap
                if (stock.free_cashflow is not None and stock.market_cap)
                else None
            )

            rows.append({
                # factor scores (current model)
                "F_value": value,
                "F_growth": growth,
                "F_profitability": profitability,
                "F_momentum": momentum,
                "F_eps_revisions": eps_rev,
                "F_total": scored.total,
                # raw fundamentals
                "pe": -stock.trailing_pe if stock.trailing_pe is not None else None,
                "pb": -stock.price_to_book if stock.price_to_book is not None else None,
                "fcf_yield": fcf_yield,
                "roe": stock.return_on_equity,
                "profit_margin": stock.profit_margin,
                "earnings_growth": stock.earnings_growth,
                "revenue_growth": stock.revenue_growth,
                "eps_surprise": stock.eps_surprise,
                # raw trend / candidates
                "return_12m": stock.return_12m,
                "return_12m_ex_1m": r12x1,
                "return_6m": _period_ret(close, 126),
                "return_3m": _period_ret(close, 63),
                "return_1m_REV": (-stock.return_1m) if stock.return_1m is not None else None,
                "price_vs_sma200": stock.price_vs_sma200,
                "sma200_slope": stock.sma200_slope,
                "pct_off_52w_high": _pct_off_high(close),
                "volatility_LOW": -vol_1y if vol_1y is not None else None,
                "rsi_14": stock.rsi_14,
                "fwd_6m": fwd,
            })

        if not rows:
            continue
        if not metric_names:
            metric_names = [k for k in rows[0] if k != "fwd_6m"]

        for name in metric_names:
            ic = _spearman([r[name] for r in rows], [r["fwd_6m"] for r in rows])
            if ic is not None:
                ic_rows.setdefault(name, []).append((sd, ic))

    # ── Report ──
    print(f"\n{'Metric':<22} {'MeanIC':>8} {'HitRate':>8} {'Snaps':>6}")
    print("-" * 48)
    report = []
    for name in metric_names:
        ics = [ic for _, ic in ic_rows.get(name, [])]
        if not ics:
            continue
        mean_ic = sum(ics) / len(ics)
        hit = sum(1 for i in ics if i > 0) / len(ics)
        report.append((name, mean_ic, hit, len(ics)))

    for name, mean_ic, hit, n in sorted(report, key=lambda r: -r[1]):
        print(f"{name:<22} {mean_ic:>8.3f} {hit:>8.0%} {n:>6}")

    if args.csv:
        long_rows = [
            {"metric": name, "date": d.date().isoformat(), "ic": ic}
            for name, vals in ic_rows.items()
            for d, ic in vals
        ]
        pd.DataFrame(long_rows).to_csv(args.csv, index=False)
        print(f"\nPer-snapshot ICs saved to {args.csv}")


if __name__ == "__main__":
    main()
