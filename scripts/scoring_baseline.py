#!/usr/bin/env python3
"""Backtest baseline runner — save structured results for before/after comparison.

Usage:
    python scripts/scoring_baseline.py --tag "roic-v1" --region us -n 5
    python scripts/scoring_baseline.py --compare baseline.json roic-v1.json

The JSON output contains all key metrics so you can diff two scoring
iterations quantitatively.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


def _run_backtest(args: argparse.Namespace) -> dict[str, Any]:
    """Execute backtest via CLI and capture the BacktestResult."""
    import tempfile

    # Build backtest command
    cmd = [
        sys.executable, "-m", "investdaytip.main", "backtest",
        "-r", args.region,
        "-n", str(args.top_n),
        "--period", args.period,
        "--interval-months", str(args.interval_months),
        "--min-market-cap", str(args.min_market_cap),
        "--max-workers", str(args.max_workers),
        "--export-html", "",  # auto-generate filename
        "--no-cache",
    ]
    if args.tickers:
        cmd.extend(["-t"] + args.tickers.split())
    if args.dynamic_weights:
        cmd.append("--dynamic-weights")
    if args.regime:
        cmd.extend(["--regime", args.regime])
    if args.include_technical:
        cmd.append("--include-technical")
    if args.scoring_model != "classic":
        cmd.extend(["--scoring-model", args.scoring_model])

    # Run in temp dir so HTML file is captured
    with tempfile.TemporaryDirectory() as tmpdir:
        print(f"Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, cwd=tmpdir, capture_output=True, text=True)
        print(result.stdout)
        if result.returncode != 0:
            print(result.stderr, file=sys.stderr)
            sys.exit(result.returncode)

        # Find generated HTML and extract metrics from stdout
        html_files = list(Path(tmpdir).glob("backtest-*.html"))
        html_file = str(html_files[0]) if html_files else None

        # Parse metrics from stdout lines
        metrics: dict[str, Any] = {
            "tag": args.tag,
            "timestamp": datetime.now().isoformat(),
            "config": {
                "tickers": args.tickers,
                "region": args.region,
                "top_n": args.top_n,
                "period": args.period,
                "interval_months": args.interval_months,
                "min_market_cap": args.min_market_cap,
                "dynamic_weights": args.dynamic_weights,
                "regime": args.regime,
                "scoring_model": args.scoring_model,
            },
            "html_file": html_file,
        }

        for line in result.stdout.splitlines():
            line = line.strip()
            if "Cumulative Return" in line:
                metrics["cumulative_return"] = _parse_pct(line)
            elif "Benchmark Return" in line:
                metrics["benchmark_return"] = _parse_pct(line)
            elif "Alpha" in line and "Excess" in line:
                metrics["alpha"] = _parse_pct(line)
            elif "Sharpe" in line and "Benchmark" not in line:
                metrics["sharpe"] = _parse_float(line)
            elif "Benchmark Sharpe" in line:
                metrics["benchmark_sharpe"] = _parse_float(line)
            elif "Win Rate 6M" in line:
                metrics["win_rate_6m"] = _parse_pct(line)
            elif "Win Rate 12M" in line:
                metrics["win_rate_12m"] = _parse_pct(line)
            elif "Max Drawdown" in line:
                metrics["max_drawdown"] = _parse_pct(line)
            elif "Snapshots" in line:
                metrics["snapshots"] = _parse_int(line)

        return metrics


def _parse_pct(line: str) -> float | None:
    """Extract a percentage value like ' 248.57%' or ' 53.3%'."""
    import re
    m = re.search(r"([+-]?\d+\.?\d*)%", line)
    return float(m.group(1)) / 100.0 if m else None


def _parse_float(line: str) -> float | None:
    """Extract a float like '    0.52'."""
    import re
    m = re.search(r"([+-]?\d+\.?\d*)", line)
    return float(m.group(1)) if m else None


def _parse_int(line: str) -> int | None:
    """Extract an integer."""
    import re
    m = re.search(r"(\d+)", line)
    return int(m.group(1)) if m else None


def _compare(before_path: str, after_path: str) -> None:
    """Print a side-by-side comparison of two baseline JSON files."""
    with open(before_path) as f:
        before = json.load(f)
    with open(after_path) as f:
        after = json.load(f)

    keys = [
        ("cumulative_return", "Cumulative Return", True),
        ("benchmark_return", "Benchmark Return", True),
        ("alpha", "Alpha (annualized)", True),
        ("sharpe", "Sharpe", False),
        ("benchmark_sharpe", "Benchmark Sharpe", False),
        ("win_rate_6m", "Win Rate 6M", True),
        ("win_rate_12m", "Win Rate 12M", True),
        ("max_drawdown", "Max Drawdown", True),
        ("snapshots", "Snapshots", False),
    ]

    print(f"\n{'Metric':<25} {'Before':>12} {'After':>12} {'Δ':>10}")
    print("-" * 65)
    for key, label, is_pct in keys:
        b = before.get(key)
        a = after.get(key)
        if b is None or a is None:
            continue
        delta = a - b
        if is_pct:
            b_s = f"{b*100:.2f}%" if b is not None else "N/A"
            a_s = f"{a*100:.2f}%" if a is not None else "N/A"
            d_s = f"{delta*100:+.2f}%"
        else:
            b_s = f"{b:.2f}" if b is not None else "N/A"
            a_s = f"{a:.2f}" if a is not None else "N/A"
            d_s = f"{delta:+.2f}"
        print(f"{label:<25} {b_s:>12} {a_s:>12} {d_s:>10}")

    # Signal whether the new iteration improved
    alpha_improved = (after.get("alpha") or 0) > (before.get("alpha") or 0)
    sharpe_improved = (after.get("sharpe") or 0) > (before.get("sharpe") or 0)
    win12_improved = (after.get("win_rate_12m") or 0) > (before.get("win_rate_12m") or 0)
    dd_improved = (after.get("max_drawdown") or 0) < (before.get("max_drawdown") or 0)

    print("\n" + "=" * 65)
    if alpha_improved and sharpe_improved and win12_improved:
        print("Result: IMPROVED — higher alpha, sharpe, and 12M win rate")
    elif alpha_improved or sharpe_improved:
        print("Result: MIXED — some metrics improved, review carefully")
    else:
        print("Result: REGRESSED or NEUTRAL — consider revising the change")
    print("=" * 65 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest baseline runner")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run backtest and save baseline JSON")
    run.add_argument("--tag", required=True, help="Baseline tag (e.g. 'v1.2')")
    run.add_argument("-r", "--region", default="us")
    run.add_argument("-n", "--top-n", type=int, default=10)
    run.add_argument("--period", default="5y")
    run.add_argument("--interval-months", type=int, default=3)
    run.add_argument("--min-market-cap", type=float, default=2_000_000_000)
    run.add_argument("--max-workers", type=int, default=10)
    run.add_argument("-t", "--tickers", default=None)
    run.add_argument("--dynamic-weights", action="store_true")
    run.add_argument("--regime", default=None)
    run.add_argument("--include-technical", action="store_true")
    run.add_argument("--scoring-model", choices=["classic", "quant"], default="quant",
                     help="Scoring model to use (default: quant).")
    run.add_argument("-o", "--output", default=".")

    cmp = sub.add_parser("compare", help="Compare two baseline JSON files")
    cmp.add_argument("before", help="Before JSON")
    cmp.add_argument("after", help="After JSON")

    args = parser.parse_args()

    if args.command == "run":
        metrics = _run_backtest(args)
        out_path = Path(args.output) / f"baseline-{args.tag}.json"
        with open(out_path, "w") as f:
            json.dump(metrics, f, indent=2)
        print(f"\nBaseline saved to: {out_path}")
    elif args.command == "compare":
        _compare(args.before, args.after)


if __name__ == "__main__":
    main()
