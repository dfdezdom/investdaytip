"""DataRoma superinvestor holdings integration.

Fetches and aggregates 13F portfolio holdings of well-known value
investors tracked by DataRoma (https://www.dataroma.com/m/managers.php).

Usage::
    from investdaytip.dataroma import fetch_superinvestor_universe
    tickers = fetch_superinvestor_universe(min_overlap=2)
"""

from __future__ import annotations

import json
import logging
import re
import time
from html import unescape
from typing import Any
from urllib.request import Request, urlopen

from investdaytip.cache import get_db

logger = logging.getLogger(__name__)

MANAGERS_URL = "https://www.dataroma.com/m/managers.php"
HOLDINGS_URL = "https://www.dataroma.com/m/holdings.php?m={code}"

TTL_SUPERINVESTOR = 86400 * 7  # 7 days (quarterly data)
CACHE_KEY = "superinvestor:holdings"


def _fetch(url: str) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def _extract_manager_codes(html: str) -> list[tuple[str, str]]:
    """Parse managers page, returning [(code, name), ...]."""
    managers: list[tuple[str, str]] = []
    for m in re.finditer(
        r'<a href="(/m/holdings\.php\?m=([^"]+))"[^>]*>([^<]+)</a>',
        html,
    ):
        code = m.group(2)
        name = unescape(m.group(3)).strip()
        managers.append((code, name))
    return managers


def _extract_tickers_from_holdings_page(html: str) -> dict[str, float]:
    """Parse a manager's holdings page, returning {ticker: pct_of_portfolio}.

    The holdings page includes the top-10 table  on the managers summary,
    or the full holdings table on the dedicated page.
    """
    tickers: dict[str, float] = {}
    for m in re.finditer(
        r'<a href="/m/stock\.php\?sym=([^"]+)"[^>]*>'
        r'[^<]+</a>.*?'
        r'(\d+\.\d+)%\s*of\s*portfolio',
        html,
        re.DOTALL,
    ):
        ticker = m.group(1)
        pct = float(m.group(2))
        if ticker not in tickers:
            tickers[ticker] = pct
    return tickers


def fetch_manager_list() -> list[tuple[str, str]]:
    """Fetch the list of superinvestors from DataRoma."""
    html = _fetch(MANAGERS_URL)
    return _extract_manager_codes(html)


def fetch_manager_holdings(code: str) -> dict[str, float]:
    """Fetch a single manager's holdings, returning {ticker: portfolio_pct}."""
    html = _fetch(HOLDINGS_URL.format(code=code))
    return _extract_tickers_from_holdings_page(html)


def fetch_superinvestor_universe(
    min_overlap: int = 2,
    max_retries: int = 3,
) -> list[str]:
    """Return ticker list of stocks held by at least ``min_overlap`` superinvestors.

    Results are cached for 7 days.  Use ``min_overlap=1`` to include any stock
    held by at least one superinvestor, ``min_overlap=2`` for consensus picks, etc.
    """
    cached = get_db().get(CACHE_KEY)
    if cached:
        data = json.loads(cached)
    else:
        logger.info("Fetching superinvestor list from DataRoma ...")
        managers = fetch_manager_list()
        aggregate: dict[str, int] = {}
        weights: dict[str, float] = {}
        for code, name in managers:
            for attempt in range(max_retries):
                try:
                    holdings = fetch_manager_holdings(code)
                    for ticker, pct in holdings.items():
                        aggregate[ticker] = aggregate.get(ticker, 0) + 1
                        weights[ticker] = weights.get(ticker, 0.0) + pct
                    logger.debug("Fetched %s (%s) — %d tickers", name, code, len(holdings))
                    break
                except Exception as exc:
                    logger.warning("Failed %s (%s): %s", name, code, exc)
                    if attempt < max_retries - 1:
                        time.sleep(5)
        data = {
            "aggregate": aggregate,
            "weights": weights,
            "manager_count": len(managers),
        }
        get_db().set(CACHE_KEY, json.dumps(data), TTL_SUPERINVESTOR)

    tickers = [
        t for t, count in data["aggregate"].items()
        if count >= min_overlap and re.match(r"^[A-Z0-9.\-]+$", t)
    ]
    tickers.sort(key=lambda t: (-data["aggregate"][t], -data["weights"].get(t, 0)))
    return tickers


def get_superinvestor_data() -> dict[str, dict[str, Any]]:
    """Return per-ticker superinvestor metadata for scoring.

    Returns::
        {"AAPL": {"manager_count": 12, "total_weight": 34.5, "buys": 3}, ...}
    """
    cached = get_db().get(CACHE_KEY)
    if not cached:
        return {}
    data = json.loads(cached)
    agg = data.get("aggregate", {})
    wgt = data.get("weights", {})
    result: dict[str, dict[str, Any]] = {}
    for ticker, count in agg.items():
        result[ticker] = {
            "manager_count": count,
            "total_weight": round(wgt.get(ticker, 0.0), 2),
        }
    return result
