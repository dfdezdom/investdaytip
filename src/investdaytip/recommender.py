"""Concurrent recommendation engine for stocks and ETFs."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable, Literal

from investdaytip.asia_etf_universe import ASIA_ETF_UNIVERSE
from investdaytip.asia_universe import ASIA_UNIVERSE
from investdaytip.data_source import fetch_asset
from investdaytip.etf_universe import DEFAULT_ETF_UNIVERSE
from investdaytip.eu_etf_universe import DEFAULT_EU_ETF_UNIVERSE
from investdaytip.eu_universe import DEFAULT_EU_UNIVERSE
from investdaytip.scoring import ScoredAsset, score_asset
from investdaytip.universe import DEFAULT_UNIVERSE


AssetClass = Literal["all", "stocks", "etfs"]
Region = Literal["all", "us", "eu", "asia"]
Currency = Literal[
    "all", "USD", "EUR", "GBP", "CHF", "JPY", "HKD", "INR",
    "KRW", "TWD", "SGD", "AUD", "DKK", "SEK", "NOK", "GBp",
]


def _build_universe(
    tickers: Iterable[str] | None,
    asset_class: AssetClass,
    region: Region,
    currency: Currency = "all",
) -> list[str]:
    if tickers:
        return list(tickers)

    # Narrow region based on currency filter to avoid fetching tickers
    # that will be filtered out anyway — reduces yfinance API pressure.
    effective_region: Region = region
    if currency != "all" and effective_region == "all":
        _CURRENCY_TO_REGION: dict[str, Region] = {
            "USD": "us",
            "EUR": "eu", "GBP": "eu", "CHF": "eu",
            "DKK": "eu", "SEK": "eu", "NOK": "eu", "GBp": "eu",
            "JPY": "asia", "HKD": "asia", "INR": "asia",
            "KRW": "asia", "TWD": "asia", "SGD": "asia", "AUD": "asia",
        }
        effective_region = _CURRENCY_TO_REGION.get(currency, "all")

    pools: list[list[str]] = []
    if asset_class in ("stocks", "all"):
        if effective_region in ("us", "all"):
            pools.append(list(DEFAULT_UNIVERSE))
        if effective_region in ("eu", "all"):
            pools.append(list(DEFAULT_EU_UNIVERSE))
        if effective_region in ("asia", "all"):
            pools.append(list(ASIA_UNIVERSE))
    if asset_class in ("etfs", "all"):
        if effective_region in ("us", "all"):
            pools.append(list(DEFAULT_ETF_UNIVERSE))
        if effective_region in ("eu", "all"):
            pools.append(list(DEFAULT_EU_ETF_UNIVERSE))
        if effective_region in ("asia", "all"):
            pools.append(list(ASIA_ETF_UNIVERSE))

    return [t for pool in pools for t in pool]


def recommend(
    tickers: Iterable[str] | None = None,
    top_n: int = 5,
    max_workers: int = 10,
    min_market_cap: float = 2_000_000_000,
    asset_class: AssetClass = "all",
    region: Region = "all",
    currency: Currency = "all",
    progress_cb=None,
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
        region: "all", "us", "eu", or "asia".
        currency: Filter by ISO currency code (e.g. ``"USD"``, ``"EUR"``).
        progress_cb: Optional callable ``(done, total, ticker)``.
    """
    universe = _build_universe(tickers, asset_class, region, currency)
    total = len(universe)
    scored: list[ScoredAsset] = []

    if progress_cb:
        progress_cb(0, total, "")

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(fetch_asset, t, min_market_cap): t for t in universe}
        for i, fut in enumerate(as_completed(futures), start=1):
            ticker = futures[fut]
            try:
                data = fut.result()
                scored.append(score_asset(data))
            except Exception:
                pass
            if progress_cb:
                progress_cb(i, total, ticker)
    filtered = scored
    if currency != "all":
        filtered = [s for s in filtered if s.data.currency == currency]
    filtered.sort(key=lambda s: s.total, reverse=True)
    return filtered[:top_n]
