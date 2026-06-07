"""Integration tests for end-to-end flows.

All network access is mocked; ``fetch_asset``, ``recommend``, and
``export_recommendations_html`` are patched so no live calls occur.
"""

from __future__ import annotations

from datetime import datetime

from investdaytip.backtest import BacktestResult, BacktestSnapshot
from investdaytip.data_source import EtfData, StockData
from investdaytip.main import main
from investdaytip.recommender import _build_universe, recommend
from investdaytip.scoring import ScoredAsset

# =========================================================================
# recommend() — end-to-end sorting, filtering, callbacks
# =========================================================================


class TestRecommendFullFlow:
    def _mock_fetch(self, ticker: str, min_market_cap: float = 0.0) -> StockData:
        """Deterministic data: BBB > AAA > CCC."""
        vals = {
            "BBB": dict(
                return_on_equity=0.30, profit_margin=0.25,
                trailing_pe=12.0, price_to_book=1.5,
                debt_to_equity=20.0, current_ratio=3.0, free_cashflow=1e9,
                price_vs_sma200=0.10, return_12m=0.25, return_1m=0.03,
            ),
            "AAA": dict(
                return_on_equity=0.15, profit_margin=0.12,
                trailing_pe=20.0, price_to_book=3.0,
                debt_to_equity=60.0, current_ratio=2.0, free_cashflow=5e8,
                price_vs_sma200=0.05, return_12m=0.10, return_1m=0.01,
            ),
            "CCC": dict(
                return_on_equity=0.02, profit_margin=0.01,
                trailing_pe=50.0, price_to_book=8.0,
                debt_to_equity=300.0, current_ratio=0.8, free_cashflow=-5e8,
                price_vs_sma200=-0.25, return_12m=-0.30, return_1m=-0.10,
            ),
        }
        v = vals.get(ticker, vals["CCC"])
        return StockData(
            ticker=ticker, currency="USD",
            sector="Technology" if ticker != "CCC" else "Financial",
            market_cap={"BBB": 9e9, "AAA": 5e9, "CCC": 1e9}.get(ticker, 5e9),
            **v,
        )

    def test_sorts_by_score_desc(self, mocker):
        mocker.patch("investdaytip.recommender.fetch_asset", side_effect=self._mock_fetch)
        mocker.patch("investdaytip.recommender.close_db")
        out = recommend(tickers=["AAA", "BBB", "CCC"], top_n=10, min_market_cap=0)
        assert out[0].data.ticker == "BBB"
        assert out[1].data.ticker == "AAA"
        assert out[2].data.ticker == "CCC"

    def test_top_n_limit(self, mocker):
        mocker.patch("investdaytip.recommender.fetch_asset", side_effect=self._mock_fetch)
        mocker.patch("investdaytip.recommender.close_db")
        out = recommend(tickers=["AAA", "BBB", "CCC"], top_n=2)
        assert len(out) == 2

    def test_sector_filter_matches_prefix(self, mocker):
        mocker.patch("investdaytip.recommender.fetch_asset", side_effect=self._mock_fetch)
        mocker.patch("investdaytip.recommender.close_db")
        out = recommend(tickers=["AAA", "BBB", "CCC"], top_n=10, sector="Technology")
        tickers = {s.data.ticker for s in out}
        assert "BBB" in tickers
        assert "AAA" in tickers
        assert "CCC" not in tickers

    def test_sector_filter_case_insensitive(self, mocker):
        mocker.patch("investdaytip.recommender.fetch_asset", side_effect=self._mock_fetch)
        mocker.patch("investdaytip.recommender.close_db")
        out = recommend(tickers=["AAA", "BBB", "CCC"], top_n=10, sector="technology")
        assert "CCC" not in {s.data.ticker for s in out}

    def test_sector_filter_empty_when_no_match(self, mocker):
        mocker.patch("investdaytip.recommender.fetch_asset", side_effect=self._mock_fetch)
        mocker.patch("investdaytip.recommender.close_db")
        out = recommend(tickers=["AAA", "BBB", "CCC"], top_n=10, sector="Healthcare")
        assert out == []

    def test_currency_filter_keeps_none_and_matches(self, mocker):
        def _fetch(ticker, min_market_cap=0.0):
            currencies = {"USD_T": "USD", "EUR_T": "EUR", "NONE_T": None}
            return StockData(ticker=ticker, currency=currencies[ticker], market_cap=5e9)

        mocker.patch("investdaytip.recommender.fetch_asset", side_effect=_fetch)
        mocker.patch("investdaytip.recommender.close_db")
        out = recommend(tickers=["USD_T", "EUR_T", "NONE_T"], top_n=10, currency="USD")
        tickers = {s.data.ticker for s in out}
        assert "USD_T" in tickers
        assert "NONE_T" in tickers
        assert "EUR_T" not in tickers

    def test_min_market_cap_is_passed_to_fetch(self, mocker):
        fetch = mocker.patch("investdaytip.recommender.fetch_asset", return_value=StockData(ticker="X"))
        mocker.patch("investdaytip.recommender.close_db")
        recommend(tickers=["X"], top_n=10, min_market_cap=2e9)
        fetch.assert_called_once_with("X", 2e9)

    def test_progress_cb_called(self, mocker):
        mocker.patch("investdaytip.recommender.fetch_asset", side_effect=self._mock_fetch)
        mocker.patch("investdaytip.recommender.close_db")
        cb = mocker.Mock()
        recommend(tickers=["AAA", "BBB"], top_n=10, progress_cb=cb)
        assert cb.call_count >= 2
        last_call = cb.call_args_list[-1]
        assert last_call[0][1] == 2

    def test_etf_scoring_mixed_with_stocks(self, mocker):
        def _fetch(ticker, min_market_cap=0.0):
            if ticker == "VOO":
                return EtfData(
                    ticker="VOO", currency="USD", category="Large Blend",
                    total_assets=100e9, expense_ratio=0.0003,
                    three_year_return=0.12, return_12m=0.18,
                )
            return self._mock_fetch(ticker, min_market_cap)

        mocker.patch("investdaytip.recommender.fetch_asset", side_effect=_fetch)
        mocker.patch("investdaytip.recommender.close_db")
        out = recommend(tickers=["BBB", "VOO"], top_n=10)
        types = {s.asset_type for s in out}
        assert "STOCK" in types
        assert "ETF" in types


# =========================================================================
# main() — full CLI flow (recommend → render → export)
# =========================================================================


def _scored(ticker: str) -> ScoredAsset:
    return ScoredAsset(
        data=StockData(ticker=ticker, currency="USD"),
        asset_type="STOCK", total=50.0,
        breakdown={"Q": 50, "V": 50, "H": 50, "T": 50},
        rationale=["ok"],
    )


class TestMainFlow:
    def test_main_calls_recommend_with_args(self, mocker):
        recs = [_scored("AAPL"), _scored("MSFT")]
        mocker.patch("investdaytip.main.recommend", return_value=recs)
        mocker.patch("investdaytip.main.export_recommendations_html")
        rc = main(["-t", "AAPL", "MSFT", "-n", "3"])
        assert rc == 0

    def test_main_with_html_export(self, mocker, tmp_path):
        scored = _scored("AAPL")
        mocker.patch("investdaytip.main.recommend", return_value=[scored])
        export_mock = mocker.patch("investdaytip.main.export_recommendations_html",
                                   return_value=str(tmp_path / "out.html"))
        report = tmp_path / "report.html"
        rc = main(["-t", "AAPL", "--export-html", str(report)])
        assert rc == 0
        export_mock.assert_called_once()
        args, _ = export_mock.call_args
        assert args[0] == [scored]
        assert str(report) in args[1]

    def test_main_with_tickers_file(self, mocker, tmp_path):
        tickers_file = tmp_path / "mylist.txt"
        tickers_file.write_text("AAPL, MSFT\nVOO\n")
        recs = [_scored("AAPL"), _scored("MSFT"), _scored("VOO")]
        mocker.patch("investdaytip.main.recommend", return_value=recs)
        mocker.patch("investdaytip.main.export_recommendations_html")
        rc = main(["--tickers-file", str(tickers_file)])
        assert rc == 0

    def test_main_tickers_file_not_found(self, mocker):
        mocker.patch("investdaytip.main.export_recommendations_html")
        rc = main(["--tickers-file", "/nonexistent/path.txt"])
        assert rc == 1

    def test_main_recommend_error_returns_1(self, mocker):
        mocker.patch("investdaytip.main.recommend", side_effect=RuntimeError("boom"))
        mocker.patch("investdaytip.main.export_recommendations_html")
        rc = main(["-t", "AAPL"])
        assert rc == 1

    def test_main_export_html_error_returns_1(self, mocker, tmp_path):
        recs = [_scored("AAPL")]
        mocker.patch("investdaytip.main.recommend", return_value=recs)
        mocker.patch("investdaytip.main.export_recommendations_html",
                      side_effect=OSError("disk full"))
        rc = main(["-t", "AAPL", "--export-html", str(tmp_path / "out.html")])
        assert rc == 1


# =========================================================================
# _build_universe() — multi-region and multi-currency
# =========================================================================


class TestBuildUniverseExtended:
    def test_multi_region_stocks(self):
        from investdaytip.eu_universe import DEFAULT_EU_UNIVERSE
        from investdaytip.universe import DEFAULT_UNIVERSE

        u = _build_universe(None, "stocks", ["us", "eu"], "all")
        expected = set(DEFAULT_UNIVERSE) | set(DEFAULT_EU_UNIVERSE)
        # Aliases applied when multiple regions merged: RACE.MI -> RACE
        expected = (expected - {"RACE.MI"}) | {"RACE"}
        assert set(u) == expected
        assert len(u) == len(expected)

    def test_multi_region_etfs(self):
        from investdaytip.asia_etf_universe import ASIA_ETF_UNIVERSE
        from investdaytip.etf_universe import DEFAULT_ETF_UNIVERSE
        from investdaytip.eu_etf_universe import DEFAULT_EU_ETF_UNIVERSE

        u = _build_universe(None, "etfs", ["us", "eu", "asia"], "all")
        expected = set(DEFAULT_ETF_UNIVERSE) | set(DEFAULT_EU_ETF_UNIVERSE) | set(ASIA_ETF_UNIVERSE)
        assert set(u) == expected

    def test_multi_currency_narrows_to_regions(self):
        from investdaytip.eu_universe import DEFAULT_EU_UNIVERSE
        from investdaytip.superinvestor_universe import SUPERINVESTOR_UNIVERSE
        from investdaytip.universe import DEFAULT_UNIVERSE

        u = _build_universe(None, "stocks", "all", ["USD", "EUR"])
        expected = set(DEFAULT_UNIVERSE) | set(DEFAULT_EU_UNIVERSE) | set(SUPERINVESTOR_UNIVERSE)
        # Aliases applied when multiple regions merged: RACE.MI -> RACE
        expected = (expected - {"RACE.MI"}) | {"RACE"}
        assert set(u) == expected
        from investdaytip.asia_universe import ASIA_UNIVERSE

        assert not (set(ASIA_UNIVERSE) & set(u))

    def test_currency_usd_includes_superinvestor(self):
        from investdaytip.superinvestor_universe import SUPERINVESTOR_UNIVERSE
        from investdaytip.universe import DEFAULT_UNIVERSE

        u = _build_universe(None, "stocks", "all", "USD")
        assert set(SUPERINVESTOR_UNIVERSE).issubset(set(u))
        assert set(DEFAULT_UNIVERSE).issubset(set(u))


# =========================================================================
# backtest CLI subcommand
# =========================================================================


class TestBacktestCLI:
    def test_backtest_subcommand_dispatches(self, mocker):
        result = BacktestResult(
            snapshots=[BacktestSnapshot(
                date=datetime(2024, 6, 1), picks=[_scored("AAPL")],
                avg_return_6m=0.05, avg_return_12m=0.15,
                benchmark_return_6m=0.03, benchmark_return_12m=0.10,
            )],
            total_snapshots=1, cumulative_return=0.05,
            benchmark_cumulative_return=0.03, sharpe=1.2,
            alpha=0.02, max_drawdown=0.01,
        )
        mocker.patch("investdaytip.backtest.run_backtest", return_value=result)
        mocker.patch("investdaytip.main.export_backtest_html")
        rc = main(["backtest", "-t", "AAPL", "-n", "3", "--export-html"])
        assert rc == 0

    def test_backtest_html_export(self, mocker, tmp_path):
        result = BacktestResult(snapshots=[], total_snapshots=0)
        mocker.patch("investdaytip.backtest.run_backtest", return_value=result)
        export_mock = mocker.patch("investdaytip.main.export_backtest_html")
        report = tmp_path / "bt.html"
        rc = main(["backtest", "-t", "AAPL", "--export-html", str(report)])
        assert rc == 0
        export_mock.assert_called_once()

    def test_backtest_with_region_and_period(self, mocker):
        result = BacktestResult(snapshots=[], total_snapshots=0)
        mocker.patch("investdaytip.backtest.run_backtest", return_value=result)
        mocker.patch("investdaytip.main.export_backtest_html")
        rc = main(["backtest", "-r", "eu", "--period", "10y", "-t", "AAPL"])
        assert rc == 0

    def test_backtest_export_html_error_returns_1(self, mocker):
        result = BacktestResult(snapshots=[], total_snapshots=0)
        mocker.patch("investdaytip.backtest.run_backtest", return_value=result)
        mocker.patch("investdaytip.main.export_backtest_html",
                      side_effect=OSError("write error"))
        rc = main(["backtest", "-t", "AAPL", "--export-html", "/x/y/z.html"])
        assert rc == 1
