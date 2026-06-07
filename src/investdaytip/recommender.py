"""Concurrent recommendation engine for stocks and ETFs."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable, Literal

from investdaytip.asia_etf_universe import ASIA_ETF_UNIVERSE
from investdaytip.asia_universe import ASIA_UNIVERSE
from investdaytip.cache import close_db
from investdaytip.data_source import fetch_asset
from investdaytip.dataroma import get_superinvestor_data
from investdaytip.etf_universe import DEFAULT_ETF_UNIVERSE
from investdaytip.eu_etf_universe import DEFAULT_EU_ETF_UNIVERSE
from investdaytip.eu_universe import DEFAULT_EU_UNIVERSE
from investdaytip.scoring import ScoredAsset, score_asset
from investdaytip.superinvestor_universe import SUPERINVESTOR_UNIVERSE
from investdaytip.universe import DEFAULT_UNIVERSE

logger = logging.getLogger(__name__)


AssetClass = Literal["all", "stocks", "etfs"]


def _build_universe(
    tickers: Iterable[str] | None,
    asset_class: AssetClass | str,
    region: str | list[str],
    currency: str | list[str] = "all",
) -> list[str]:
    if tickers:
        return list(tickers)

    regions = [region] if isinstance(region, str) else region
    currencies = [currency] if isinstance(currency, str) else currency

    # Narrow region based on currency filter to avoid fetching tickers
    # that will be filtered out anyway — reduces yfinance API pressure.
    if "all" in regions and "all" not in currencies:
        _CURRENCY_TO_REGION: dict[str, str] = {
            "USD": "us",
            "EUR": "eu", "GBP": "eu", "CHF": "eu",
            "DKK": "eu", "SEK": "eu", "NOK": "eu", "GBp": "eu",
            "JPY": "asia", "HKD": "asia", "INR": "asia",
            "KRW": "asia", "TWD": "asia", "SGD": "asia", "AUD": "asia",
        }
        derived: set[str] = set()
        for c in currencies:
            mapped = _CURRENCY_TO_REGION.get(c)
            if mapped:
                derived.add(mapped)
            else:
                derived.add("all")
        # When USD is selected, also include the superinvestor consensus universe
        # (all superinvestor tickers are US-listed and overlap with the US universe).
        if "us" in derived:
            derived.add("superinvestor")
        regions = sorted(derived) if "all" not in derived else ["all"]

    pools: list[list[str]] = []
    if asset_class in ("stocks", "all"):
        if "all" in regions or "us" in regions:
            pools.append(list(DEFAULT_UNIVERSE))
        if "all" in regions or "eu" in regions:
            pools.append(list(DEFAULT_EU_UNIVERSE))
        if "all" in regions or "asia" in regions:
            pools.append(list(ASIA_UNIVERSE))
        if "all" in regions or "superinvestor" in regions:
            pools.append(list(SUPERINVESTOR_UNIVERSE))
    if asset_class in ("etfs", "all"):
        if "all" in regions or "us" in regions:
            pools.append(list(DEFAULT_ETF_UNIVERSE))
        if "all" in regions or "eu" in regions:
            pools.append(list(DEFAULT_EU_ETF_UNIVERSE))
        if "all" in regions or "asia" in regions:
            pools.append(list(ASIA_ETF_UNIVERSE))

    # Deduplicate across pools (overlapping universes, e.g. VXUS/IEMG appear
    # in both US-ETF and Asia-ETF lists) case-insensitively, preserving the
    # first-occurrence casing. Avoids fetching/scoring the same ticker twice.
    #
    # Also handles ticker aliases: same company listed on different exchanges
    # with different tickers (e.g. TSM = 2330.TW for Taiwan Semiconductor).
    # Aliases are only applied when multiple pools are merged (region=all), not
    # when a single region is requested, so the original ticker format is preserved.
    _TICKER_ALIASES: dict[str, str] = {
        "2330.TW": "TSM",    # Taiwan Semiconductor
        "9988.HK": "BABA",   # Alibaba
        "RACE.MI": "RACE",   # Ferrari
        "RIO.AX": "RIO.L",   # Rio Tinto (ASX → LSE)
        "ASML.AS": "ASML",   # ASML (Euronext → NASDAQ ADR)
    }
    seen: set[str] = set()
    merged: list[str] = []
    use_aliases = len(pools) > 1  # Only dedupe aliases when multiple universes are merged
    for pool in pools:
        for t in pool:
            # Normalize via alias if applicable (only when multiple pools merged)
            canonical = _TICKER_ALIASES.get(t.upper(), t) if use_aliases else t
            key = canonical.upper()
            if key not in seen:
                seen.add(key)
                merged.append(canonical)
    return merged


def recommend(
    tickers: Iterable[str] | None = None,
    top_n: int = 5,
    max_workers: int = 10,
    min_market_cap: float = 2_000_000_000,
    asset_class: AssetClass | str = "all",
    region: str | list[str] = "all",
    currency: str | list[str] = "all",
    sector: str | None = None,
    progress_cb=None,
    include_technical: bool = False,
) -> list[ScoredAsset]:
    """Score each ticker and return the top ``top_n`` long-term buys.

    Args:
        tickers: Custom universe; overrides ``asset_class``/``region``.
        top_n: Number of recommendations to return.
        max_workers: Threads used for parallel data fetching.
        min_market_cap: Filter out tickers below this value. Compared
            against yfinance's reported market cap / AUM. **Note:** yfinance
            reports figures in the asset's native currency, so this acts as
            an approximate filter for non-USD listings.
        asset_class: "all", "stocks", or "etfs".
        region: Region(s) — ``"all"``, ``"us"``, ``"eu"``, ``"asia"``, ``"superinvestor"`` or a list.
        currency: Currency filter(s) — e.g. ``"USD"``, ``["USD", "EUR"]``.
        sector: Sector/category prefix filter (case-insensitive) — e.g. ``"Technology"``, ``"Healthcare"``.
        progress_cb: Optional callable ``(done, total, ticker)``.
    """
    universe = _build_universe(tickers, asset_class, region, currency)
    total = len(universe)
    scored: list[ScoredAsset] = []

    si_data = get_superinvestor_data()

    if progress_cb:
        progress_cb(0, total, "")

    try:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(fetch_asset, t, min_market_cap): t for t in universe}
            for i, fut in enumerate(as_completed(futures), start=1):
                ticker = futures[fut]
                try:
                    data = fut.result()
                    scored.append(score_asset(data, include_technical=include_technical, si_data=si_data))
                except Exception:
                    logger.warning("Failed to fetch/score %s", ticker, exc_info=True)
                if progress_cb:
                    progress_cb(i, total, ticker)
    finally:
        # Release per-thread cache connections opened by the worker pool.
        close_db()

    currencies = [currency] if isinstance(currency, str) else currency
    if "all" not in currencies:
        # Keep assets whose currency is unknown (None): a missing field should
        # not silently exclude an otherwise-valid candidate.
        filtered = [
            s for s in scored
            if s.data.currency is None or s.data.currency in currencies
        ]
    else:
        filtered = scored

    if min_market_cap > 0:
        filtered = [
            s for s in filtered
            if s.data.market_cap is not None and s.data.market_cap >= min_market_cap
        ]

    if sector:
        sector_lower = sector.lower()
        filtered = [
            s for s in filtered
            if s.data.sector and s.data.sector.lower().startswith(sector_lower)
        ]

    filtered.sort(key=lambda s: s.total, reverse=True)
    return filtered[:top_n]
