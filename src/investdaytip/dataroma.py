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
# v2: GOOG merged into GOOGL; v3: share-class dots normalized to Yahoo dashes
# (BRK.B → BRK-B) and keys uppercased.
CACHE_KEY = "superinvestor:holdings:v3"


def _fetch(url: str) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "identity",
            "Connection": "keep-alive",
        },
    )
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

    The holdings page uses a table where each row has the stock link in the
    second <td> and the portfolio percentage in the third <td>.
    """
    tickers: dict[str, float] = {}
    for row in re.finditer(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL):
        row_html = row.group(1)
        # Find the stock link in the "stock" column
        stock_match = re.search(
            r'<td class="stock">.*?<a href="/m/stock\.php\?sym=([^"]+)">',
            row_html,
            re.DOTALL,
        )
        if not stock_match:
            continue
        ticker = stock_match.group(1)
        # Find the percentage in the next <td> after the stock column
        # Pattern: <td class="stock">...</td> followed by <td>XX.YY</td>
        pct_match = re.search(
            r'<td class="stock">.*?</td>\s*<td[^>]*>([\d.,]+)</td>',
            row_html,
            re.DOTALL,
        )
        if pct_match:
            try:
                pct_str = pct_match.group(1).replace(',', '')
                pct = float(pct_str)
                if ticker not in tickers:
                    tickers[ticker] = pct
            except ValueError:
                pass
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
    progress_cb=None,
) -> list[str]:
    """Return ticker list of stocks held by at least ``min_overlap`` superinvestors.

    Results are cached for 7 days.  Use ``min_overlap=1`` to include any stock
    held by at least one superinvestor, ``min_overlap=2`` for consensus picks, etc.
    """
    cached = get_db().get(CACHE_KEY)
    if cached:
        try:
            data = json.loads(cached)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Corrupt superinvestor cache, refetching: %s", exc)
            data = None
    else:
        data = None

    if data is None:
        logger.info("Fetching superinvestor list from DataRoma ...")
        managers = fetch_manager_list()
        aggregate: dict[str, int] = {}
        weights: dict[str, float] = {}
        for i, (code, name) in enumerate(managers, start=1):
            if progress_cb:
                progress_cb(i, len(managers), name)
            for attempt in range(max_retries):
                try:
                    holdings = fetch_manager_holdings(code)
                    for ticker, pct in holdings.items():
                        # DataRoma is US-only (13F), so dots only appear in
                        # share classes (BRK.B) which Yahoo writes with a
                        # dash (BRK-B). Normalize to Yahoo's convention.
                        sym = ticker.replace(".", "-").upper()
                        aggregate[sym] = aggregate.get(sym, 0) + 1
                        weights[sym] = weights.get(sym, 0.0) + pct
                    logger.debug("Fetched %s (%s) — %d tickers", name, code, len(holdings))
                    break
                except Exception as exc:
                    logger.warning("Failed %s (%s): %s", name, code, exc)
                    if attempt < max_retries - 1:
                        time.sleep(5)
                    else:
                        logger.warning("Exhausted retries for %s", name)
            # Polite delay between manager requests
            time.sleep(0.5)

        data = {
            "aggregate": aggregate,
            "weights": weights,
            "manager_count": len(managers),
        }
        if aggregate:
            get_db().set(CACHE_KEY, json.dumps(data), TTL_SUPERINVESTOR)
        else:
            logger.warning(
                "DataRoma returned no holdings (page structure changed?) — "
                "not caching the empty result"
            )

    # Normalizar tickers duplicados: GOOG → GOOGL (defensa contra cache v1 o corrupta)
    aggregate = data.get("aggregate", {})
    weights = data.get("weights", {})
    if "GOOG" in aggregate:
        aggregate["GOOGL"] = aggregate.get("GOOGL", 0) + aggregate["GOOG"]
        weights["GOOGL"] = weights.get("GOOGL", 0.0) + weights.get("GOOG", 0.0)
        del aggregate["GOOG"]
        del weights["GOOG"]
        logger.debug("Merged GOOG into GOOGL: total managers=%d", aggregate["GOOGL"])
        data["aggregate"] = aggregate
        data["weights"] = weights

    tickers = [
        t for t, count in data["aggregate"].items()
        if count >= min_overlap and re.match(r"^[A-Z0-9.\-]+$", t)
    ]
    tickers.sort(key=lambda t: (-data["aggregate"][t], -data["weights"].get(t, 0)))
    return tickers


def get_superinvestor_data() -> dict[str, dict[str, Any]]:
    """Return per-ticker superinvestor metadata for scoring.

    Returns::
        {"AAPL": {"manager_count": 12, "total_weight": 34.5}, ...}
    """
    cached = get_db().get(CACHE_KEY)
    if not cached:
        return {}
    try:
        data = json.loads(cached)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Corrupt superinvestor cache: %s", exc)
        return {}
    agg = data.get("aggregate", {})
    wgt = data.get("weights", {})
    # Normalizar: GOOG -> GOOGL (defensa contra cache v1 o corrupta)
    if "GOOG" in agg:
        agg["GOOGL"] = agg.get("GOOGL", 0) + agg["GOOG"]
        wgt["GOOGL"] = wgt.get("GOOGL", 0.0) + wgt.get("GOOG", 0.0)
        del agg["GOOG"]
        del wgt["GOOG"]
    result: dict[str, dict[str, Any]] = {}
    for ticker, count in agg.items():
        result[ticker] = {
            "manager_count": count,
            "total_weight": round(wgt.get(ticker, 0.0), 2),
        }
    return result
