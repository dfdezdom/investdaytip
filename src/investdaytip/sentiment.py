"""Market sentiment indicators — CNN Fear & Greed Index.

Fetches the composite Fear & Greed Index and its 7 sub-indicators
from CNN's public JSON endpoint. All network I/O is wrapped in
proper error handling so a failure never crashes the pipeline.
"""

from __future__ import annotations

import http.client
import json
import math
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

_FEAR_GREED_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"

# Mapping from CNN's JSON keys to friendlier names
_SUB_INDICATOR_KEYS: dict[str, str] = {
    "market_momentum_sp125": "market_momentum",
    "stock_price_strength": "stock_price_strength",
    "stock_price_breadth": "stock_price_breadth",
    "put_call_options": "put_call_options",
    "market_volatility_vix_50": "market_volatility",
    "junk_bond_demand": "junk_bond_demand",
    "safe_haven_demand": "safe_haven_demand",
}


def _fetch_fear_greed_json() -> dict[str, Any] | None:
    """Fetch and parse the CNN Fear & Greed JSON payload.

    Returns the parsed dict, or None on any error (network, JSON, etc.).
    """
    req = Request(
        _FEAR_GREED_URL,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://edition.cnn.com/markets/fear-and-greed",
            "Origin": "https://edition.cnn.com",
        },
    )
    try:
        with urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
        return json.loads(raw)
    except (URLError, OSError, http.client.HTTPException,
            json.JSONDecodeError, ValueError, TimeoutError):
        return None


def _parse_score(val: Any) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _parse_rating(val: Any) -> str | None:
    if isinstance(val, str) and val.strip():
        return val.strip().lower()
    return None


def fear_greed_index() -> dict[str, Any] | None:
    """Fetch the CNN Fear & Greed Index.

    Returns a dict with keys:
        score       — composite 0-100 score (float or None)
        rating      — one of: extreme fear, fear, neutral, greed, extreme greed
        timestamp   — Unix ms timestamp (int or None)
        sub_indicators — dict of {name: {"score": float, "rating": str}}

    Returns None if the fetch fails entirely (network error, parse error).
    """
    from investdaytip.cache import cache_sentiment_get, cache_sentiment_set

    cached = cache_sentiment_get()
    if cached is not None:
        try:
            return json.loads(cached)
        except Exception:
            pass

    payload = _fetch_fear_greed_json()
    if payload is None:
        return None
    if not isinstance(payload, dict):
        return None

    # Composite score
    fg = payload.get("fear_and_greed")
    score = _parse_score(fg.get("score") if isinstance(fg, dict) else None)
    rating = _parse_rating(fg.get("rating") if isinstance(fg, dict) else None)
    timestamp = None
    if isinstance(fg, dict) and fg.get("timestamp") is not None:
        ts = fg["timestamp"]
        if isinstance(ts, (int, float)):
            timestamp = int(ts)
        elif isinstance(ts, str):
            # Handle ISO-8601 string: 2026-06-05T19:59:59+00:00 or 2026-06-05T19:59:59Z
            try:
                from datetime import datetime as _dt
                # Python 3.10 fromisoformat doesn't support "Z" suffix
                ts_normalized = ts.replace("Z", "+00:00")
                timestamp = int(_dt.fromisoformat(ts_normalized).timestamp() * 1000)
            except Exception:
                pass

    # Sub-indicators
    sub_indicators: dict[str, dict[str, Any]] = {}
    for cnn_key, friendly in _SUB_INDICATOR_KEYS.items():
        sub = payload.get(cnn_key)
        if isinstance(sub, dict):
            sub_indicators[friendly] = {
                "score": _parse_score(sub.get("score")),
                "rating": _parse_rating(sub.get("rating")),
            }

    result = {
        "score": score,
        "rating": rating,
        "timestamp": timestamp,
        "sub_indicators": sub_indicators,
    }
    cache_sentiment_set(json.dumps(result))
    return result
