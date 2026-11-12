"""Re-audit ``SUPERINVESTOR_UNIVERSE`` against the live DataRoma aggregate.

The universe is a hand-curated list, so it drifts as 13F filings come in: names
enter the consensus and others leave it, and nothing in CI notices. This script
answers the two questions the curation asks — *is every curated ticker still
held by enough managers?* and *which qualifying tickers are missing?* — against
the filings DataRoma shows today.

    python scripts/superinvestor_audit.py                     # fetch + report
    python scripts/superinvestor_audit.py --json out.json     # keep the aggregate
    python scripts/superinvestor_audit.py --from-json out.json  # re-run offline
    python scripts/superinvestor_audit.py --min-market-cap 10  # apply the size rule

The market-cap pass is opt-in: it costs one yfinance call per candidate, so it
runs only for tickers that already clear ``--min-overlap``. ETFs are listed
separately rather than added — ``SGOV`` was removed from the universe for
exactly this reason, so a fund that clears the count never belongs here.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from investdaytip.dataroma import (
    MANAGERS_URL,
    _fetch,
    fetch_manager_holdings,
    fetch_manager_list,
)
from investdaytip.superinvestor_universe import SUPERINVESTOR_UNIVERSE
from investdaytip.universe import DEFAULT_UNIVERSE

# Share classes and deliberate exclusions documented in the universe module.
_NOT_UNIVERSE_BY_DESIGN = {
    "BRK-A": "share price makes a position unscorable (deliberate exclusion)",
    "GOOG": "merged into GOOGL",
    "LEN-B": "second class of LEN",
    "UHAL-B": "second class of UHAL",
    "BF-A": "second class of BF-B",
    "FOX": "second class of FOXA",
    "NWSA": "second class of NWS",
}


def build_aggregate(
    refresh: bool = True,
) -> dict[str, Any]:
    """Collect every manager's holdings and count how many hold each ticker.

    Mirrors ``dataroma.fetch_superinvestor_universe()`` — same symbol
    normalization (``BRK.B`` → ``BRK-B``) and the same GOOG → GOOGL merge — but
    bypasses the 7-day cache so an audit always sees the current filings.
    """
    managers = fetch_manager_list()
    if not managers:
        raise SystemExit("DataRoma returned no managers — page structure changed?")

    aggregate: dict[str, int] = {}
    weights: dict[str, float] = {}
    failed: list[str] = []

    for i, (code, name) in enumerate(managers, start=1):
        try:
            holdings = fetch_manager_holdings(code)
        except Exception as exc:  # noqa: BLE001 — best effort, reported below
            failed.append(f"{code}: {exc}")
            print(f"[{i}/{len(managers)}] FAIL {code} {name}: {exc}", file=sys.stderr)
            time.sleep(0.5)
            continue
        for ticker, pct in holdings.items():
            sym = ticker.replace(".", "-").upper()
            aggregate[sym] = aggregate.get(sym, 0) + 1
            weights[sym] = weights.get(sym, 0.0) + pct
        print(
            f"[{i}/{len(managers)}] {code:5} {name[:38]:38} {len(holdings):4} tickers",
            file=sys.stderr,
        )
        time.sleep(0.5)

    if "GOOG" in aggregate:
        aggregate["GOOGL"] = aggregate.get("GOOGL", 0) + aggregate.pop("GOOG")
        weights["GOOGL"] = weights.get("GOOGL", 0.0) + weights.pop("GOOG", 0.0)

    return {
        "manager_count": len(managers),
        "failed": failed,
        "aggregate": aggregate,
        "weights": weights,
    }


def _report_quarter() -> str:
    """The most recent reportable quarter on the managers page, if it says."""
    try:
        html = _fetch(MANAGERS_URL)
    except Exception:  # noqa: BLE001 — a label is a nicety, never a failure
        return "unknown"
    quarters = sorted(set(re.findall(r"Q[1-4]\s+20\d\d", html)))
    return quarters[-1] if quarters else "unknown"


def market_cap(ticker: str) -> float | None:
    """USD market cap via yfinance, or ``None`` (never a guessed figure)."""
    import warnings

    import yfinance as yf

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            info = yf.Ticker(ticker).info or {}
        except Exception:  # noqa: BLE001
            return None
    cap = info.get("marketCap")
    return float(cap) if cap else None


def is_fund(ticker: str) -> bool:
    """True for ETFs/funds — a fund that clears the count still does not belong."""
    import warnings

    import yfinance as yf

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            info = yf.Ticker(ticker).info or {}
        except Exception:  # noqa: BLE001
            return False
    return str(info.get("quoteType", "")).upper() in {"ETF", "MUTUALFUND"}


def audit(
    data: dict[str, Any],
    min_overlap: int,
    min_market_cap: float | None,
) -> dict[str, Any]:
    """Compare the curated universe with one aggregate."""
    agg: dict[str, int] = data["aggregate"]
    curated = [t.upper() for t in SUPERINVESTOR_UNIVERSE]
    curated_set = set(curated)
    us_pool = set(t.upper() for t in DEFAULT_UNIVERSE)

    dropped = [t for t in curated if t not in agg]
    thin = sorted((agg[t], t) for t in curated if t in agg and agg[t] < min_overlap)

    candidates = [
        (count, ticker)
        for ticker, count in agg.items()
        if count >= min_overlap
        and ticker not in curated_set
        and ticker not in _NOT_UNIVERSE_BY_DESIGN
    ]
    candidates.sort(reverse=True)

    rows: list[dict[str, Any]] = []
    for count, ticker in candidates:
        row: dict[str, Any] = {"ticker": ticker, "managers": count,
                               "in_us_pool": ticker in us_pool}
        if min_market_cap is not None:
            if is_fund(ticker):
                row["excluded"] = "fund"
            else:
                cap = market_cap(ticker)
                row["market_cap"] = cap
                if cap is None:
                    row["excluded"] = "no market cap"
                elif cap < min_market_cap * 1e9:
                    row["excluded"] = f"below ${min_market_cap:g}B"
        rows.append(row)

    return {
        "curated": len(curated),
        "min_overlap": min_overlap,
        "dropped": dropped,
        "thin": thin,
        "missing": [r for r in rows if not r.get("excluded")],
        "excluded": [r for r in rows if r.get("excluded")],
    }


def _print(result: dict[str, Any], quarter: str) -> None:
    cap_floor = result.get("min_market_cap")
    rule = f">= {result['min_overlap']} managers"
    if cap_floor is not None:
        rule += f", >= ${cap_floor:g}B market cap"

    print(f"\nDataRoma report: {quarter} — curated universe: {result['curated']} tickers")
    print(f"Rule applied: {rule}")

    print(f"\nCurated tickers absent from every live portfolio: "
          f"{len(result['dropped'])}")
    for ticker in result["dropped"]:
        print(f"  {ticker}")

    print(f"\nCurated tickers below {result['min_overlap']} managers: {len(result['thin'])}")
    for count, ticker in result["thin"]:
        print(f"  {ticker:7} {count} managers")

    missing = result["missing"]
    print(f"\nQualifying tickers missing from the universe: {len(missing)}")
    absent = [r for r in missing if not r["in_us_pool"]]
    for row in missing:
        cap = row.get("market_cap")
        cap_txt = f"{cap / 1e9:8.1f}B" if cap else "     n/a "
        pool = "us-pool" if row["in_us_pool"] else "ABSENT  "
        print(f"  {row['ticker']:7}{row['managers']:3} mgr {cap_txt}  {pool}")
    print(f"  … of which absent from every pool: {len(absent)}")

    if result["excluded"]:
        print(f"\nFiltered out by the size/fund rule: {len(result['excluded'])}")
        for row in result["excluded"]:
            print(f"  {row['ticker']:7}{row['managers']:3} mgr — {row['excluded']}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit SUPERINVESTOR_UNIVERSE against live DataRoma 13F data.",
    )
    parser.add_argument("--min-overlap", type=int, default=2,
                        help="minimum number of managers holding a ticker (default: 2)")
    parser.add_argument("--min-market-cap", type=float, default=None,
                        help="size floor in billions, e.g. 10 (default: no cap pass)")
    parser.add_argument("--json", dest="json_out", type=Path, default=None,
                        help="write the fetched aggregate here")
    parser.add_argument("--from-json", dest="json_in", type=Path, default=None,
                        help="re-run the comparison against a saved aggregate")
    args = parser.parse_args()

    if args.json_in:
        data = json.loads(args.json_in.read_text())
        quarter = "from saved snapshot"
    else:
        data = build_aggregate()
        quarter = _report_quarter()
        if args.json_out:
            args.json_out.write_text(json.dumps(data, indent=1))
            print(f"\naggregate written to {args.json_out}", file=sys.stderr)

    result = audit(data, args.min_overlap, args.min_market_cap)
    result["min_market_cap"] = args.min_market_cap
    _print(result, quarter)
    if data.get("failed"):
        print(f"\nWARNING: {len(data['failed'])} manager(s) failed to fetch — "
              "counts are a lower bound.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
