"""Tests for the recommendation orchestration layer."""
from __future__ import annotations

from investdaytip.data_source import StockData
from investdaytip.recommender import _build_universe, recommend


class TestBuildUniverse:
    def test_custom_tickers_override(self):
        assert _build_universe(["AAA", "BBB"], "all", "all", "all") == ["AAA", "BBB"]

    def test_stocks_us_only(self):
        from investdaytip.universe import DEFAULT_UNIVERSE

        u = _build_universe(None, "stocks", "us", "all")
        assert set(u) == set(DEFAULT_UNIVERSE)

    def test_etfs_only_excludes_stocks(self):
        u = _build_universe(None, "etfs", "all", "all")
        from investdaytip.universe import DEFAULT_UNIVERSE

        assert not (set(u) & set(DEFAULT_UNIVERSE))

    def test_dedupes_overlapping_pools(self):
        # us + asia ETF pools share tickers (e.g. VXUS / IEMG).
        u = _build_universe(None, "etfs", ["us", "asia"], "all")
        assert len(u) == len({t.upper() for t in u})

    def test_currency_narrows_region(self):
        # USD with region=all should derive the US region (including superinvestor).
        from investdaytip.eu_universe import DEFAULT_EU_UNIVERSE
        from investdaytip.superinvestor_universe import SUPERINVESTOR_UNIVERSE
        from investdaytip.universe import DEFAULT_UNIVERSE

        u = _build_universe(None, "stocks", "all", "USD")
        assert set(u) == set(DEFAULT_UNIVERSE) | set(SUPERINVESTOR_UNIVERSE)
        assert not (set(u) & set(DEFAULT_EU_UNIVERSE))

    def test_eur_narrows_to_eu(self):
        from investdaytip.eu_universe import DEFAULT_EU_UNIVERSE

        u = _build_universe(None, "stocks", "all", "EUR")
        assert set(u) == set(DEFAULT_EU_UNIVERSE)

    def test_unknown_currency_keeps_all_regions(self):
        u_all = _build_universe(None, "stocks", "all", "all")
        u_unknown = _build_universe(None, "stocks", "all", "ZZZ")
        assert set(u_unknown) == set(u_all)


class TestRecommend:
    def test_scores_sorts_and_limits(self, mocker):
        def _fake_fetch(ticker, min_market_cap=0.0):
            caps = {"AAA": 5e9, "BBB": 9e9, "CCC": 1e9}
            return StockData(
                ticker=ticker,
                currency="USD",
                market_cap=caps[ticker],
                return_on_equity=0.3 if ticker == "BBB" else 0.05,
                profit_margin=0.25 if ticker == "BBB" else 0.02,
            )

        mocker.patch("investdaytip.recommender.fetch_asset", side_effect=_fake_fetch)
        close = mocker.patch("investdaytip.recommender.close_db")

        out = recommend(tickers=["AAA", "BBB", "CCC"], top_n=2)
        assert len(out) == 2
        assert out[0].total >= out[1].total
        # close_db must run to release worker connections.
        close.assert_called_once()

    def test_currency_filter_keeps_none(self, mocker):
        def _fake_fetch(ticker, min_market_cap=0.0):
            currencies = {"USD_T": "USD", "EUR_T": "EUR", "NONE_T": None}
            return StockData(ticker=ticker, currency=currencies[ticker], market_cap=5e9)

        mocker.patch("investdaytip.recommender.fetch_asset", side_effect=_fake_fetch)
        mocker.patch("investdaytip.recommender.close_db")

        out = recommend(tickers=["USD_T", "EUR_T", "NONE_T"], top_n=10, currency="USD")
        tickers = {s.data.ticker for s in out}
        assert "USD_T" in tickers
        assert "NONE_T" in tickers  # unknown currency is kept
        assert "EUR_T" not in tickers

    def test_swallowed_exception_is_logged(self, mocker, caplog):
        def _fake_fetch(ticker, min_market_cap=0.0):
            raise ValueError("boom")

        mocker.patch("investdaytip.recommender.fetch_asset", side_effect=_fake_fetch)
        mocker.patch("investdaytip.recommender.close_db")

        with caplog.at_level("WARNING"):
            out = recommend(tickers=["XXX"], top_n=5)
        assert out == []
        assert any("Failed to fetch" in rec.message for rec in caplog.records)
