"""Tests for the StockFit live data source (--data-source stockfit) — no network."""

from __future__ import annotations

import time

import pytest

from investdaytip.data_source import StockData
from investdaytip.data_source_stockfit import (
    PitStatements,
    StockfitError,
    _parse_periods,
    fetch_asset_stockfit,
)


def _row(period_end: str, date_filed: str, fy: int, facts: dict) -> dict:
    return {
        "period": period_end,
        "fiscalYear": fy,
        "fiscalPeriod": "FY",
        "dateFiled": date_filed,
        "facts": facts,
    }


def _pit() -> PitStatements:
    return PitStatements(
        ticker="AAPL",
        cik=320193,
        income=_parse_periods([
            _row("2025-09-27", "2025-10-31", 2025,
                 {"revenue": 400e9, "netIncome": 100e9, "epsDiluted": 7.0,
                  "grossProfit": 200e9}),
            _row("2024-09-28", "2024-11-01", 2024,
                 {"revenue": 350e9, "netIncome": 90e9, "epsDiluted": 6.0,
                  "grossProfit": 157.5e9}),
        ]),
        balance=_parse_periods([
            _row("2025-09-27", "2025-10-31", 2025, {
                "assets": 400e9, "totalDebt": 100e9, "currentAssets": 150e9,
                "currentLiabilities": 130e9, "sharesOutstanding": 15e9,
                "stockholdersEquity": 220e9}),
            _row("2024-09-28", "2024-11-01", 2024, {
                "assets": 380e9, "totalDebt": 95e9, "currentAssets": 140e9,
                "currentLiabilities": 125e9, "sharesOutstanding": 15.2e9,
                "stockholdersEquity": 200e9}),
        ]),
        cash_flow=_parse_periods([
            _row("2025-09-27", "2025-10-31", 2025, {"freeCashFlow": 95e9}),
            _row("2024-09-28", "2024-11-01", 2024, {"freeCashFlow": 85e9}),
        ]),
    )


_PROFILE = {
    "name": "Apple Inc.", "type": "stock", "cik": 320193,
    "sector": "Information Technology", "exchanges": ["Nasdaq"],
}


def _fake_get(path, params=None, **_kw):
    if path == "price/history":
        start = time.time() - 500 * 86400
        data = [[int((start + i * 86400) * 1000), 100.0 + i * 0.2] for i in range(500)]
        return {"data": data}
    if path == "earnings/dividend-history":
        return [{"period": "2025-09-27", "dividendPerShare": 2.0, "payoutRatio": 0.15}]
    raise AssertionError(f"unexpected path: {path}")


def _patch_core(mocker):
    mocker.patch("investdaytip.data_source_stockfit._lookup_profile", return_value=dict(_PROFILE))
    mocker.patch("investdaytip.data_source_stockfit.fetch_pit_statements", return_value=_pit())
    mocker.patch("investdaytip.data_source_stockfit._get", side_effect=_fake_get)


def test_fetch_asset_stockfit_happy_path(mocker):
    _patch_core(mocker)
    data = fetch_asset_stockfit("AAPL")
    assert isinstance(data, StockData)
    assert data.errors == []
    assert data.name == "Apple Inc."
    assert data.sector == "Technology"  # GICS mapped to yfinance-style
    assert data.currency == "USD"
    assert data.current_price == pytest.approx(100.0 + 499 * 0.2)
    assert data.market_cap == pytest.approx(data.current_price * 15e9)
    assert data.trailing_pe == pytest.approx(data.current_price / 7.0)
    assert data.earnings_growth == pytest.approx((100e9 - 90e9) / 90e9)
    assert data.margin_improving is True   # 0.50 > 0.45
    assert data.roa_improving is True      # 100/400 > 90/360
    assert data.dividend_yield == pytest.approx(2.0 / data.current_price)
    assert data.return_12m is not None     # trend from the price series
    # No analyst estimates in StockFit → these stay neutral/None
    assert data.forward_pe is None
    assert data.peg_ratio is None
    assert data.eps_surprise is None


def test_fetch_asset_stockfit_shares_fallback(mocker):
    """Filers tagging `currentSharesOutstanding` still get P/B and market cap."""
    pit = PitStatements(
        ticker="CVX",
        income=_parse_periods([_row("2025-12-31", "2026-02-01", 2025,
                                    {"revenue": 200e9, "netIncome": 15e9, "epsDiluted": 8.0})]),
        balance=_parse_periods([_row("2025-12-31", "2026-02-01", 2025,
                                     {"assets": 400e9, "stockholdersEquity": 180e9,
                                      "currentSharesOutstanding": 2e9})]),
    )
    mocker.patch("investdaytip.data_source_stockfit._lookup_profile", return_value=dict(_PROFILE))
    mocker.patch("investdaytip.data_source_stockfit.fetch_pit_statements", return_value=pit)
    mocker.patch("investdaytip.data_source_stockfit._get", side_effect=_fake_get)
    data = fetch_asset_stockfit("CVX")
    assert data.market_cap == pytest.approx(data.current_price * 2e9)
    assert data.price_to_book == pytest.approx(data.current_price / (180e9 / 2e9))


def test_fetch_asset_stockfit_dividend_scan(mocker):
    """Empty-shell dividend rows (all-None) are skipped until a real DPS."""
    def fake_get(path, params=None, **_kw):
        if path == "price/history":
            return _fake_get(path, params)
        if path == "earnings/dividend-history":
            return [{"dividendPerShare": None, "payoutRatio": None},
                    {"dividendPerShare": 1.5}]
        raise AssertionError(path)

    mocker.patch("investdaytip.data_source_stockfit._lookup_profile", return_value=dict(_PROFILE))
    mocker.patch("investdaytip.data_source_stockfit.fetch_pit_statements", return_value=_pit())
    mocker.patch("investdaytip.data_source_stockfit._get", side_effect=fake_get)
    data = fetch_asset_stockfit("AAPL")
    assert data.dividend_yield == pytest.approx(1.5 / data.current_price)


def test_fetch_asset_stockfit_uses_fresh_snapshot(mocker):
    """A <7d-old local snapshot serves statements without touching the API."""
    from investdaytip.data_source_stockfit import save_pit_snapshot

    save_pit_snapshot(_pit())
    fetch_mock = mocker.patch(
        "investdaytip.data_source_stockfit.fetch_pit_statements",
        side_effect=AssertionError("fresh snapshot must be used"),
    )
    mocker.patch("investdaytip.data_source_stockfit._lookup_profile", return_value=dict(_PROFILE))
    mocker.patch("investdaytip.data_source_stockfit._get", side_effect=_fake_get)
    data = fetch_asset_stockfit("AAPL")
    fetch_mock.assert_not_called()
    assert data.earnings_growth == pytest.approx((100e9 - 90e9) / 90e9)


def test_fetch_asset_stockfit_stale_snapshot_refetches(mocker):
    import os as _os
    import time as _time

    from investdaytip.data_source_stockfit import save_pit_snapshot, snapshot_dir

    save_pit_snapshot(_pit())
    old = _time.time() - 30 * 86400
    _os.utime(snapshot_dir() / "AAPL.json", (old, old))
    mocker.patch("investdaytip.data_source_stockfit._lookup_profile", return_value=dict(_PROFILE))
    fetch_mock = mocker.patch(
        "investdaytip.data_source_stockfit.fetch_pit_statements",
        side_effect=StockfitError("live unreachable"),
    )
    with pytest.raises(StockfitError):
        fetch_asset_stockfit("AAPL")
    fetch_mock.assert_called_once()


def test_fetch_asset_stockfit_history_cached(enabled_temp_cache, mocker):
    """Prices come from the shared 15-min history cache on the second run."""
    calls: list[str] = []

    def counting_get(path, params=None, **_kw):
        calls.append(path)
        return _fake_get(path, params)

    mocker.patch("investdaytip.data_source_stockfit._lookup_profile", return_value=dict(_PROFILE))
    mocker.patch("investdaytip.data_source_stockfit.fetch_pit_statements", return_value=_pit())
    mocker.patch("investdaytip.data_source_stockfit._get", side_effect=counting_get)

    first = fetch_asset_stockfit("AAPL")
    second = fetch_asset_stockfit("AAPL")
    assert calls.count("price/history") == 1  # second run is a cache hit
    assert second.current_price == pytest.approx(first.current_price)


def test_fetch_asset_stockfit_info_cached(enabled_temp_cache, mocker):
    """Profile + dividends hit the 1-day cache on the second run."""
    lookup_mock = mocker.patch(
        "investdaytip.data_source_stockfit._lookup_profile", return_value=dict(_PROFILE)
    )
    mocker.patch("investdaytip.data_source_stockfit.fetch_pit_statements", return_value=_pit())
    calls: list[str] = []

    def counting_get(path, params=None, **_kw):
        calls.append(path)
        return _fake_get(path, params)

    mocker.patch("investdaytip.data_source_stockfit._get", side_effect=counting_get)

    first = fetch_asset_stockfit("AAPL")
    second = fetch_asset_stockfit("AAPL")
    assert lookup_mock.call_count == 1
    assert calls.count("earnings/dividend-history") == 1
    assert second.dividend_yield == pytest.approx(first.dividend_yield)


def test_fetch_asset_stockfit_rejects_etf(mocker):
    mocker.patch(
        "investdaytip.data_source_stockfit._lookup_profile",
        return_value={**_PROFILE, "type": "etf"},
    )
    data = fetch_asset_stockfit("VOO")
    assert any("stocks only" in e for e in data.errors)


def test_fetch_asset_stockfit_unknown_ticker_raises(mocker):
    mocker.patch("investdaytip.data_source_stockfit._lookup_profile", return_value={})
    with pytest.raises(StockfitError, match="unknown ticker"):
        fetch_asset_stockfit("NOPE")


def test_fetch_asset_stockfit_no_prices_raises(mocker):
    _patch_core(mocker)
    mocker.patch("investdaytip.data_source_stockfit._get", return_value={"data": []})
    with pytest.raises(StockfitError, match="no price history"):
        fetch_asset_stockfit("AAPL")


# ── recommender wiring ───────────────────────────────────────────────────────


def test_recommend_stockfit_requires_key(monkeypatch):
    from investdaytip.recommender import recommend

    monkeypatch.delenv("STOCKFIT_API_KEY", raising=False)
    with pytest.raises(ValueError, match="STOCKFIT_API_KEY"):
        recommend(tickers=["AAPL"], top_n=1, data_source="stockfit")


def test_recommend_stockfit_plan_gate(mocker, monkeypatch):
    from investdaytip.recommender import recommend

    monkeypatch.setenv("STOCKFIT_API_KEY", "k")
    mocker.patch("investdaytip.recommender.detect_plan", return_value="free")
    with pytest.raises(ValueError, match="Starter"):
        recommend(tickers=["AAPL"], top_n=1, data_source="stockfit")


def test_recommend_stockfit_rejects_etfs(monkeypatch):
    from investdaytip.recommender import recommend

    monkeypatch.setenv("STOCKFIT_API_KEY", "k")
    with pytest.raises(ValueError, match="stocks only"):
        recommend(top_n=1, asset_class="etfs", data_source="stockfit")


def test_recommend_stockfit_excludes_non_us(mocker, monkeypatch):
    from investdaytip.recommender import recommend

    monkeypatch.setenv("STOCKFIT_API_KEY", "k")
    mocker.patch("investdaytip.recommender.detect_plan", return_value="pro")
    fetch = mocker.patch(
        "investdaytip.recommender.fetch_asset_stockfit",
        side_effect=lambda t, min_market_cap=0.0, **kw: StockData(
            ticker=t, name=t, market_cap=1e11, trailing_pe=15,
            price_to_book=2.0, peg_ratio=1.0, return_on_equity=0.25,
            profit_margin=0.2, return_on_assets=0.12,
            earnings_growth=0.1, revenue_growth=0.1,
        ),
    )
    recommend(tickers=["AAPL", "SAP.DE"], top_n=5, data_source="stockfit")
    called = [c.args[0] for c in fetch.call_args_list]
    assert called == ["AAPL"]  # SAP.DE excluded — StockFit is US-only


def test_recommend_stockfit_falls_back_to_yfinance(mocker, monkeypatch):
    from investdaytip.recommender import recommend

    monkeypatch.setenv("STOCKFIT_API_KEY", "k")
    mocker.patch("investdaytip.recommender.detect_plan", return_value="pro")

    def stockfit_fetch(ticker, min_market_cap=0.0, **kw):
        raise StockfitError("boom")

    mocker.patch("investdaytip.recommender.fetch_asset_stockfit", side_effect=stockfit_fetch)
    yf_fetch = mocker.patch(
        "investdaytip.recommender.fetch_asset",
        side_effect=lambda t, min_market_cap=0.0, **kw: StockData(
            ticker=t, name=t, market_cap=1e11, trailing_pe=15,
            price_to_book=2.0, peg_ratio=1.0, return_on_equity=0.25,
            profit_margin=0.2, return_on_assets=0.12,
            earnings_growth=0.1, revenue_growth=0.1,
        ),
    )
    results = recommend(tickers=["AAPL", "MSFT"], top_n=5, data_source="stockfit")
    assert yf_fetch.call_count == 2  # both tickers fell back
    assert {r.data.ticker for r in results} == {"AAPL", "MSFT"}
