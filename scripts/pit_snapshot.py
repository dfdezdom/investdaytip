#!/usr/bin/env python3
"""Build (or inspect) the local StockFit PIT statement snapshot.

The snapshot lets ``backtest --pit-source stockfit`` run without an API key:
annual statements (12 fiscal years with ``dateFiled``) are fetched while your
key is valid and stored under ``~/.investdaytip/pit/`` (override with the
``STOCKFIT_PIT_SNAPSHOT_DIR`` environment variable).

Usage:
    python scripts/pit_snapshot.py                  # full US stock universe
    python scripts/pit_snapshot.py -t "AAPL MSFT"   # specific tickers
    python scripts/pit_snapshot.py --status         # inspect files (offline)
    python scripts/pit_snapshot.py --force          # refetch already-saved
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from investdaytip.data_source_stockfit import (
    StockfitError,
    check_api_key,
    fetch_pit_statements,
    snapshot_dir,
)
from investdaytip.recommender import _build_universe


def _status(dir_path: Path) -> int:
    """Print an offline summary of the existing snapshot files."""
    files = sorted(dir_path.glob("*.json")) if dir_path.is_dir() else []
    if not files:
        print(f"No snapshots in {dir_path}")
        return 1
    total_fy = 0
    print(f"{len(files)} snapshots in {dir_path}:")
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            n = len(payload.get("income") or [])
            saved = str(payload.get("saved_at", "?"))[:10]
            stitched = " (stitched)" if payload.get("stitched") else ""
        except (OSError, ValueError):
            n, saved, stitched = -1, "corrupt", ""
        total_fy += max(n, 0)
        print(f"  {path.stem:<12} {n:>3} FY  saved {saved}{stitched}")
    print(f"Total: {total_fy} fiscal years")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-t", "--tickers", default=None,
                        help="quoted ticker list (whitespace/comma separated)")
    parser.add_argument("--status", action="store_true",
                        help="list existing snapshot files (no network)")
    parser.add_argument("--force", action="store_true",
                        help="refetch tickers already present in the snapshot")
    parser.add_argument("--workers", type=int, default=6,
                        help="parallel fetch threads (default: 6)")
    args = parser.parse_args(argv)

    dir_path = snapshot_dir()
    if args.status:
        return _status(dir_path)

    try:
        check_api_key()
    except StockfitError as exc:
        print(exc, file=sys.stderr)
        return 1

    if args.tickers:
        tickers = [t for t in re.split(r"[\s,]+", args.tickers.strip()) if t]
    else:
        tickers = sorted(_build_universe(None, "stocks", "us", "all"))
    if not tickers:
        print("Empty ticker list", file=sys.stderr)
        return 1

    todo = tickers if args.force else [
        t for t in tickers if not (dir_path / f"{t}.json").exists()
    ]
    skipped = len(tickers) - len(todo)
    print(f"Snapshot dir: {dir_path}")
    print(f"{len(todo)} to fetch, {skipped} already saved, {len(tickers)} total")

    saved = empty = failed = 0
    started = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_pit_statements, t): t for t in todo}
        for i, future in enumerate(as_completed(futures), 1):
            ticker = futures[future]
            try:
                statements = future.result()
            except Exception as exc:  # pragma: no cover - defensive
                failed += 1
                print(f"\r{i}/{len(todo)} {ticker:<12} FAILED ({exc})          ")
                continue
            if statements.income:
                saved += 1
                note = " (stitched)" if statements.stitched else ""
                print(f"\r{i}/{len(todo)} {ticker:<12} {len(statements.income)} FY{note}      ")
            else:
                empty += 1
                print(f"\r{i}/{len(todo)} {ticker:<12} no data               ")

    elapsed = time.time() - started
    print(f"\nDone in {elapsed:.0f}s — {saved} saved, {empty} without data, "
          f"{failed} failed, {skipped} skipped (already present)")
    print("Backtest without a key: investdaytip backtest --pit-source stockfit ...")
    return 0 if saved or skipped else 1


if __name__ == "__main__":
    raise SystemExit(main())
