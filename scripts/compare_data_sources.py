#!/usr/bin/env python3
"""Compare data source quality: yfinance vs yahooquery vs fmp.

Runs ``get_recommendations()`` on the same ticker list with each data source
and produces a side-by-side comparison of scores, rankings, and field coverage.

Usage:
    python scripts/compare_data_sources.py -t "AAPL MSFT GOOGL NVDA TSM"
    python scripts/compare_data_sources.py -r us -n 20
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from investdaytip import get_recommendations
from investdaytip.cache import clear_cache

# Fields we care about for stock comparison
_STOCK_FIELDS = [
    "trailing_pe", "forward_pe", "price_to_book", "peg_ratio",
    "return_on_equity", "return_on_assets", "profit_margin",
    "earnings_growth", "revenue_growth",
    "debt_to_equity", "current_ratio", "free_cashflow",
    "dividend_yield", "payout_ratio", "eps_surprise",
    "market_cap", "current_price",
    "return_1m", "return_12m",
]

_ETF_FIELDS = [
    "total_assets", "expense_ratio", "three_year_return",
    "five_year_return", "beta_3y", "yield_",
    "return_1m", "return_12m",
]


def _run(source: str, args: argparse.Namespace) -> list:
    """Run recommendations with a given data source."""
    print(f"\n▶ Running with {source} …")
    try:
        results = get_recommendations(
            tickers=args.tickers.split() if args.tickers else None,
            region=args.region,
            currency=args.currency,
            top_n=args.top_n,
            data_source=source,
            scoring_model=args.scoring_model,
        )
        return results
    except Exception as exc:
        print(f"  ❌ {source} failed: {exc}")
        return []


def _field_coverage(results: list, fields: list[str], asset_type: str | None = None) -> dict[str, float]:
    """Return % of non-None values per field across all results.
    
    If asset_type is provided, only count results of that type.
    """
    coverage: dict[str, float] = {}
    for field in fields:
        present = 0
        total = 0
        for r in results:
            data = r.data
            if asset_type is not None and data.asset_type != asset_type:
                continue
            if hasattr(data, field):
                total += 1
                if getattr(data, field) is not None:
                    present += 1
        coverage[field] = (present / total * 100.0) if total else 0.0
    return coverage


def _compare_scores(yf_results: list, yq_results: list) -> dict[str, Any]:
    """Compare scores and rankings between two data sources."""
    yf_by_ticker = {r.data.ticker: r for r in yf_results}
    yq_by_ticker = {r.data.ticker: r for r in yq_results}

    common = sorted(set(yf_by_ticker.keys()) & set(yq_by_ticker.keys()))
    if not common:
        return {}

    diffs = []
    score_diffs = []
    for tk in common:
        yf_r = yf_by_ticker[tk]
        yq_r = yq_by_ticker[tk]
        d = abs(yf_r.total - yq_r.total)
        score_diffs.append(d)
        diffs.append({
            "ticker": tk,
            "yf_score": round(yf_r.total, 2),
            "yq_score": round(yq_r.total, 2),
            "diff": round(d, 2),
            "yf_rank": yf_results.index(yf_r) + 1,
            "yq_rank": yq_results.index(yq_r) + 1,
        })

    diffs.sort(key=lambda x: x["diff"], reverse=True)

    return {
        "common_tickers": len(common),
        "yf_only": len(yf_by_ticker) - len(common),
        "yq_only": len(yq_by_ticker) - len(common),
        "avg_score_diff": round(sum(score_diffs) / len(score_diffs), 2) if score_diffs else 0,
        "max_score_diff": round(max(score_diffs), 2) if score_diffs else 0,
        "top_diffs": diffs[:10],
    }


def _compare_fields(yf_results: list, yq_results: list) -> dict[str, Any]:
    """Compare individual field values between two data sources."""
    yf_by_ticker = {r.data.ticker: r for r in yf_results}
    yq_by_ticker = {r.data.ticker: r for r in yq_results}
    common = sorted(set(yf_by_ticker.keys()) & set(yq_by_ticker.keys()))

    fields = _STOCK_FIELDS + _ETF_FIELDS
    field_diffs = defaultdict(list)

    for tk in common:
        yf_data = yf_by_ticker[tk].data
        yq_data = yq_by_ticker[tk].data
        for field in fields:
            if hasattr(yf_data, field) and hasattr(yq_data, field):
                yf_val = getattr(yf_data, field)
                yq_val = getattr(yq_data, field)
                if yf_val is not None and yq_val is not None:
                    # Relative difference
                    if abs(yf_val) > 0.01:
                        rel_diff = abs(yf_val - yq_val) / abs(yf_val)
                        field_diffs[field].append(rel_diff)
                    else:
                        field_diffs[field].append(abs(yf_val - yq_val))
                elif (yf_val is None) != (yq_val is None):
                    # One is missing — treat as 100% diff
                    field_diffs[field].append(1.0)

    summary = {}
    for field, diffs in field_diffs.items():
        if diffs:
            summary[field] = {
                "avg_rel_diff": round(sum(diffs) / len(diffs) * 100, 1),
                "max_rel_diff": round(max(diffs) * 100, 1),
                "missing_count": sum(1 for d in diffs if d >= 1.0),
            }
    return summary


def _print_report(
    yf_results: list,
    yq_results: list,
    score_cmp: dict[str, Any],
    field_cmp: dict[str, Any],
    yf_stock_cov: dict[str, float],
    yq_stock_cov: dict[str, float],
    yf_etf_cov: dict[str, float],
    yq_etf_cov: dict[str, float],
) -> None:
    """Print a human-readable comparison report."""
    print("\n" + "=" * 70)
    print("📊 DATA SOURCE COMPARISON REPORT")
    print("=" * 70)
    print(f"Date: {datetime.now().isoformat()}")
    print(f"yfinance tickers: {len(yf_results)}")
    print(f"yahooquery tickers: {len(yq_results)}")
    print(f"Common tickers: {score_cmp.get('common_tickers', 0)}")
    print(f"yfinance only: {score_cmp.get('yf_only', 0)}")
    print(f"yahooquery only: {score_cmp.get('yq_only', 0)}")

    print("\n" + "-" * 70)
    print("📈 SCORE COMPARISON")
    print("-" * 70)
    print(f"Average score difference: {score_cmp.get('avg_score_diff', 0)}")
    print(f"Max score difference: {score_cmp.get('max_score_diff', 0)}")
    print("\nTop 10 biggest score differences:")
    print(f"{'Ticker':<8} {'YF Score':>10} {'YQ Score':>10} {'Diff':>8} {'YF Rank':>8} {'YQ Rank':>8}")
    for d in score_cmp.get("top_diffs", []):
        print(f"{d['ticker']:<8} {d['yf_score']:>10.1f} {d['yq_score']:>10.1f} {d['diff']:>8.1f} {d['yf_rank']:>8} {d['yq_rank']:>8}")

    def _print_coverage_table(name: str, yf_cov: dict[str, float], yq_cov: dict[str, float]) -> None:
        print(f"\n  {name}")
        all_fields = sorted(set(yf_cov.keys()) | set(yq_cov.keys()))
        print(f"  {'Field':<23} {'yfinance':>10} {'yahooquery':>10} {'Δ':>8}")
        for field in all_fields:
            yf_p = yf_cov.get(field, 0)
            yq_p = yq_cov.get(field, 0)
            delta = yq_p - yf_p
            marker = "↑" if delta > 5 else "↓" if delta < -5 else "="
            print(f"  {field:<23} {yf_p:>9.1f}% {yq_p:>9.1f}% {delta:>7.1f}% {marker}")

    print("\n" + "-" * 70)
    print("📋 FIELD COVERAGE (% non-null)")
    print("-" * 70)
    _print_coverage_table("Stocks", yf_stock_cov, yq_stock_cov)
    _print_coverage_table("ETFs", yf_etf_cov, yq_etf_cov)

    print("\n" + "-" * 70)
    print("🔍 FIELD ACCURACY (relative diff %)")
    print("-" * 70)
    sorted_fields = sorted(field_cmp.items(), key=lambda x: x[1]["avg_rel_diff"], reverse=True)
    print(f"{'Field':<25} {'Avg Diff':>10} {'Max Diff':>10} {'Missing':>10}")
    for field, stats in sorted_fields[:15]:
        print(f"{field:<25} {stats['avg_rel_diff']:>9.1f}% {stats['max_rel_diff']:>9.1f}% {stats['missing_count']:>9}")

    print("\n" + "=" * 70)


def _main() -> None:
    parser = argparse.ArgumentParser(description="Compare data source quality")
    parser.add_argument("-t", "--tickers", help="Space-separated tickers")
    parser.add_argument("-r", "--region", default="us")
    parser.add_argument("-c", "--currency", default="USD")
    parser.add_argument("-n", "--top-n", type=int, default=20)
    parser.add_argument("--scoring-model", default="quant")
    parser.add_argument("-o", "--output", help="JSON output file")
    args = parser.parse_args()

    # Clear cache for fair comparison
    clear_cache()

    yf_results = _run("yfinance", args)
    clear_cache()
    yq_results = _run("yahooquery", args)

    if not yf_results or not yq_results:
        print("❌ One or both data sources failed. Cannot compare.")
        sys.exit(1)

    score_cmp = _compare_scores(yf_results, yq_results)
    field_cmp = _compare_fields(yf_results, yq_results)

    yf_stock_cov = _field_coverage(yf_results, _STOCK_FIELDS, asset_type="STOCK")
    yq_stock_cov = _field_coverage(yq_results, _STOCK_FIELDS, asset_type="STOCK")
    yf_etf_cov = _field_coverage(yf_results, _ETF_FIELDS, asset_type="ETF")
    yq_etf_cov = _field_coverage(yq_results, _ETF_FIELDS, asset_type="ETF")

    _print_report(yf_results, yq_results, score_cmp, field_cmp,
                  yf_stock_cov, yq_stock_cov, yf_etf_cov, yq_etf_cov)

    if args.output:
        report = {
            "timestamp": datetime.now().isoformat(),
            "config": vars(args),
            "score_comparison": score_cmp,
            "field_comparison": field_cmp,
            "coverage": {
                "yfinance": {"stocks": yf_stock_cov, "etfs": yf_etf_cov},
                "yahooquery": {"stocks": yq_stock_cov, "etfs": yq_etf_cov},
            },
        }
        with open(args.output, "w") as f:
            json.dump(report, f, indent=2)
        print(f"\n💾 Report saved to {args.output}")


if __name__ == "__main__":
    _main()
