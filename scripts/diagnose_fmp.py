"""Diagnostic tool for the FMP data source.

Tests the four FMP stable endpoints used by InvestDayTip and prints the raw
HTTP status + response body so users can verify their API key/plan.

Usage:
    export FMP_API_KEY=your_key
    python scripts/diagnose_fmp.py [TICKER]
"""

from __future__ import annotations

import json
import os
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

FMP_BASE = "https://financialmodelingprep.com/stable"
USER_AGENT = "InvestDayTip/0.8.0"


def _call(endpoint: str, params: dict[str, str]) -> tuple[int, object]:
    """Make a single FMP request and return (status, parsed body)."""
    api_key = os.environ.get("FMP_API_KEY")
    if not api_key:
        print("ERROR: FMP_API_KEY environment variable not set", file=sys.stderr)
        sys.exit(1)

    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{FMP_BASE}/{endpoint}?apikey={api_key}&{query}"

    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=10) as resp:
            body = resp.read().decode()
            return resp.getcode(), json.loads(body)
    except HTTPError as exc:
        body = exc.read().decode() if exc.fp else ""
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = body
        return exc.code, parsed


def _check(name: str, endpoint: str, params: dict[str, str]) -> None:
    print(f"\n=== {name} ===")
    print(f"URL: {FMP_BASE}/{endpoint}")
    status, body = _call(endpoint, params)
    print(f"HTTP {status}")
    print(json.dumps(body, indent=2)[:2000])


def main() -> int:
    ticker = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    print(f"FMP_API_KEY present: {'yes' if os.environ.get('FMP_API_KEY') else 'no'}")
    print(f"Testing ticker: {ticker}")

    _check("Company Profile", "profile", {"symbol": ticker})
    _check("Financial Ratios TTM", "ratios-ttm", {"symbol": ticker})
    _check("Earnings Surprises", "earnings-surprises", {"symbol": ticker})
    _check(
        "Historical Price EOD (full)",
        "historical-price-eod/full",
        {"symbol": ticker, "from": "2024-01-01", "to": "2024-01-10"},
    )

    print("\n=== Pre-flight probe (SPY) ===")
    _check("Company Profile (SPY)", "profile", {"symbol": "SPY"})

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
