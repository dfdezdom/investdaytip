"""Tests for data_source_stockfit — mocked HTTP, no live network."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
import pytest

from investdaytip.data_source_stockfit import (
    PitStatements,
    StockfitError,
    StockfitRateLimitError,
    _parse_periods,
    _search_queries,
    check_api_key,
    fetch_pit_statements,
    pit_fact_asof,
)

# ── Fixtures ─────────────────────────────────────────────────────────────────


def _row(period_end: str, date_filed: str, fy: int, facts: dict) -> dict:
    return {
        "period": period_end,
        "fiscalYear": fy,
        "fiscalPeriod": "FY",
        "dateFiled": date_filed,
        "facts": facts,
    }


def _income_rows() -> list[dict]:
    return [
        _row("2025-09-30", "2025-11-01", 2025,
             {"revenue": 400e9, "netIncome": 100e9, "eps": 7.0}),
        _row("2024-09-30", "2024-11-02", 2024,
             {"revenue": 350e9, "netIncome": 90e9, "eps": 6.0}),
        _row("2023-09-30", "2023-11-03", 2023,
             {"revenue": 300e9, "netIncome": 80e9, "eps": 5.0}),
    ]


def _balance_rows() -> list[dict]:
    return [
        _row("2025-09-30", "2025-11-03", 2025, {
            "stockholdersEquity": 220e9, "assets": 350e9, "totalDebt": 110e9,
            "currentAssets": 150e9, "currentLiabilities": 130e9,
            "sharesOutstanding": 15.2e9,
        }),
        _row("2024-09-30", "2024-11-05", 2024, {
            "stockholdersEquity": 200e9, "assets": 320e9, "totalDebt": 100e9,
            "currentAssets": 140e9, "currentLiabilities": 120e9,
            "sharesOutstanding": 15.0e9,
        }),
        _row("2023-09-30", "2023-11-07", 2023, {
            "stockholdersEquity": 180e9, "assets": 300e9, "totalDebt": 90e9,
            "currentAssets": 130e9, "currentLiabilities": 110e9,
            "sharesOutstanding": 14.8e9,
        }),
    ]


def _cashflow_rows() -> list[dict]:
    return [
        _row("2025-09-30", "2025-11-01", 2025, {"freeCashFlow": 95e9}),
        _row("2024-09-30", "2024-11-02", 2024, {"freeCashFlow": 85e9}),
        _row("2023-09-30", "2023-11-03", 2023, {"freeCashFlow": 75e9}),
    ]


def _mock_get_statements(path: str, params: dict | None = None) -> Any:
    """Standard by-symbol statement mock for the three endpoints."""
    params = params or {}
    assert "symbol" in params, f"expected symbol selector, got {params}"
    if path == "financials/income-statement":
        return _income_rows()
    if path == "financials/balance-sheet":
        return _balance_rows()
    if path == "financials/cash-flow-statement":
        return _cashflow_rows()
    raise AssertionError(f"unexpected path: {path}")


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setenv("STOCKFIT_API_KEY", "test_key")


# ── _parse_periods ───────────────────────────────────────────────────────────


def test_parse_periods_sorts_newest_first_and_skips_malformed():
    rows = _income_rows() + [
        {"period": "bad-date", "dateFiled": "2020-01-01", "facts": {}},  # no fy
        "not-a-dict",
    ]
    periods = _parse_periods(rows)
    assert len(periods) == 3
    assert periods[0].fiscal_year == 2025
    assert periods[0].date_filed == datetime(2025, 11, 1)
    assert periods[0].fact("netIncome") == 100e9


def test_parse_periods_non_list_returns_empty():
    assert _parse_periods(None) == []
    assert _parse_periods({"error": "x"}) == []


# ── fetch_pit_statements ─────────────────────────────────────────────────────


def test_fetch_pit_statements_happy_path(mocker):
    mocker.patch(
        "investdaytip.data_source_stockfit._get", side_effect=_mock_get_statements
    )
    stmts = fetch_pit_statements("AAPL")
    assert stmts.ticker == "AAPL"
    assert stmts.cik is None
    assert stmts.stitched is False
    assert len(stmts.income) == 3
    assert stmts.income[0].fact("netIncome") == 100e9
    assert stmts.balance[0].fact("sharesOutstanding") == 15.2e9
    assert stmts.cash_flow[0].fact("freeCashFlow") == 95e9


def test_entity_stitching_fallback_picks_longest_series(mocker):
    """XOM-style holdco reorg: symbol resolves to a short/empty entity."""
    short = [_row("2026-09-30", "2026-11-01", 2026, {"netIncome": 1e9, "revenue": 5e9})]

    def mock_get(path: str, params: dict | None = None) -> Any:
        params = params or {}
        if path == "lookup/batch":
            return {"XOM": {"cik": 2115436, "name": "Exxon Mobil Corp", "status": "active"}}
        if path == "lookup/search":
            return [
                {"cik": 2115436, "type": "stock", "name": "Exxon Mobil Corp"},
                {"cik": 34088, "type": "stock", "name": "Exxon Corp"},
            ]
        if params.get("symbol") == "XOM":
            if path == "financials/income-statement":
                return short
            return []
        if params.get("cik") == 34088:
            return _mock_get_statements(path, {"symbol": "XOM"})
        if params.get("cik") == 2115436:
            return short if path == "financials/income-statement" else []
        raise AssertionError(f"unexpected: {path} {params}")

    get_mock = mocker.patch(
        "investdaytip.data_source_stockfit._get", side_effect=mock_get
    )
    stmts = fetch_pit_statements("XOM")
    assert stmts.stitched is True
    assert stmts.cik == 34088
    assert len(stmts.income) == 3
    # Probed only the candidate CIK (3 statement calls), never the current entity
    probed_ciks = [
        c.args[1].get("cik")
        for c in get_mock.call_args_list
        if len(c.args) > 1 and isinstance(c.args[1], dict) and c.args[1].get("cik")
    ]
    assert set(probed_ciks) == {34088}
    assert len(probed_ciks) == 3


def test_entity_stitching_no_candidates_keeps_short_series(mocker):
    short = [_row("2026-09-30", "2026-11-01", 2026, {"netIncome": 1e9})]

    def mock_get(path: str, params: dict | None = None) -> Any:
        if path == "lookup/batch":
            return {"NEWCO": {"cik": 999, "name": "New Co Inc"}}
        if path == "lookup/search":
            return []
        if path == "financials/income-statement":
            return short
        return []

    mocker.patch("investdaytip.data_source_stockfit._get", side_effect=mock_get)
    stmts = fetch_pit_statements("NEWCO")
    assert stmts.stitched is False
    assert stmts.cik == 999
    assert len(stmts.income) == 1


def test_search_queries_derives_shorter_variants():
    """Renamed holdcos only share their first (possibly camel-split) word."""
    queries = _search_queries("ExxonMobil Holdings Corp")
    assert queries[0] == "ExxonMobil Holdings Corp"
    assert "ExxonMobil" in queries
    assert "Exxon" in queries
    assert _search_queries("Apple Inc") == ["Apple Inc", "Apple"]


def test_entity_stitching_via_short_query(mocker):
    """Exact-name search fails → shorter camel-split query finds predecessor."""
    short = [_row("2026-09-30", "2026-11-01", 2026, {"netIncome": 1e9})]
    searches: list[str] = []

    def mock_get(path: str, params: dict | None = None) -> Any:
        params = params or {}
        if path == "lookup/batch":
            return {"XOM": {"cik": 2115436, "name": "ExxonMobil Holdings Corp"}}
        if path == "lookup/search":
            query = params.get("searchString", "")
            searches.append(query)
            if query == "Exxon":
                return [{"cik": 34088, "type": "stock", "name": "EXXON MOBIL CORP",
                         "status": "delisted"}]
            return [{"cik": 2115436, "type": "stock",
                     "name": "ExxonMobil Holdings Corp"}]
        if params.get("symbol") == "XOM":
            return short if path == "financials/income-statement" else []
        if params.get("cik") == 34088:
            return _mock_get_statements(path, {"symbol": "XOM"})
        return []

    mocker.patch("investdaytip.data_source_stockfit._get", side_effect=mock_get)
    stmts = fetch_pit_statements("XOM")
    assert stmts.stitched is True
    assert stmts.cik == 34088
    assert len(stmts.income) == 3
    assert "Exxon" in searches


def test_unknown_ticker_returns_empty_statements(mocker):
    mocker.patch("investdaytip.data_source_stockfit._get", return_value=[])
    stmts = fetch_pit_statements("NOSUCH")
    assert stmts.income == []
    assert stmts.balance == []
    assert stmts.cash_flow == []


# ── pit_fact_asof (look-ahead protection) ────────────────────────────────────


def test_pit_fact_asof_excludes_filings_after_as_of():
    periods = _parse_periods(_income_rows())

    # FY2025 was filed 2025-11-01 → invisible at 2025-10-15
    cur, prev = pit_fact_asof(periods, "netIncome", datetime(2025, 10, 15))
    assert cur == 90e9
    assert prev == 80e9

    # …but visible from 2025-11-01 onwards
    cur, prev = pit_fact_asof(periods, "netIncome", datetime(2025, 11, 1))
    assert cur == 100e9
    assert prev == 90e9


def test_pit_fact_asof_before_any_filing_returns_none():
    periods = _parse_periods(_income_rows())
    assert pit_fact_asof(periods, "netIncome", datetime(2020, 1, 1)) == (None, None)


def test_pit_fact_asof_missing_key_returns_none():
    periods = _parse_periods(_income_rows())
    cur, prev = pit_fact_asof(periods, "ebitda", datetime(2025, 12, 1))
    assert cur is None and prev is None


# ── _get / key handling ──────────────────────────────────────────────────────


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("STOCKFIT_API_KEY", raising=False)
    with pytest.raises(StockfitError, match="STOCKFIT_API_KEY"):
        check_api_key()
    with pytest.raises(StockfitError, match="STOCKFIT_API_KEY"):
        from investdaytip.data_source_stockfit import _get

        _get("financials/income-statement", {"symbol": "AAPL"})


def test_rate_limit_raises_stockfit_rate_limit_error(mocker):
    from urllib.error import HTTPError

    def raise_429(*args, **kwargs):
        raise HTTPError("https://x", 429, "Too Many Requests", None, None)

    mocker.patch("investdaytip.data_source_stockfit.urlopen", side_effect=raise_429)
    from investdaytip.data_source_stockfit import _get

    with pytest.raises(StockfitRateLimitError):
        _get("financials/income-statement", {"symbol": "AAPL"})


# ── _build_pit_stock_data ────────────────────────────────────────────────────


def _price_history() -> pd.DataFrame:
    idx = pd.bdate_range("2023-01-01", "2026-01-31")
    return pd.DataFrame({"Close": 100.0}, index=idx)


def _pit_statements() -> PitStatements:
    return PitStatements(
        ticker="TEST",
        cik=1,
        income=_parse_periods(_income_rows()),
        balance=_parse_periods(_balance_rows()),
        cash_flow=_parse_periods(_cashflow_rows()),
    )


def test_build_pit_stock_data_uses_only_filings_known_at_snapshot():
    from investdaytip.backtest import _build_pit_stock_data

    # Snapshot BEFORE the FY2025 filing (2025-11-01) → FY2024 fundamentals
    sd = _build_pit_stock_data(
        ticker="TEST",
        info={"shortName": "Test Corp", "sector": "Technology",
              "currency": "USD", "exchange": "NMS"},
        price_history=_price_history(),
        snapshot_date=datetime(2025, 10, 15),
        pit=_pit_statements(),
        dividends=pd.Series(dtype=float),
    )
    assert sd.current_price == 100.0
    # (90e9 - 80e9) / 80e9 — if the look-ahead FY2025 leaked in, this
    # would be (100-90)/90 = 0.1111 instead.
    assert sd.earnings_growth == pytest.approx(0.125)
    assert sd.return_on_equity == pytest.approx(90e9 / 200e9)
    assert sd.trailing_pe == pytest.approx(100.0 / 6.0)
    assert sd.market_cap == pytest.approx(100.0 * 15.0e9)
    assert sd.free_cashflow == 85e9
    assert sd.name == "Test Corp"


def test_build_pit_stock_data_picks_up_new_filing_after_date():
    from investdaytip.backtest import _build_pit_stock_data

    # Snapshot AFTER the FY2025 filing → FY2025 fundamentals
    sd = _build_pit_stock_data(
        ticker="TEST",
        info={},
        price_history=_price_history(),
        snapshot_date=datetime(2025, 12, 1),
        pit=_pit_statements(),
        dividends=pd.Series(dtype=float),
    )
    assert sd.return_on_equity == pytest.approx(100e9 / 220e9)
    assert sd.trailing_pe == pytest.approx(100.0 / 7.0)
    assert sd.earnings_growth == pytest.approx((100e9 - 90e9) / 90e9)


def test_build_pit_stock_data_empty_pit_yields_neutral_values():
    from investdaytip.backtest import _build_pit_stock_data

    sd = _build_pit_stock_data(
        ticker="TEST",
        info={},
        price_history=_price_history(),
        snapshot_date=datetime(2025, 10, 15),
        pit=PitStatements(ticker="TEST"),
        dividends=pd.Series(dtype=float),
    )
    assert sd.trailing_pe is None
    assert sd.return_on_equity is None
    assert sd.market_cap is None
    assert sd.current_price == 100.0  # price history still drives trend data


def test_build_pit_stock_data_derives_shares_from_eps():
    """Balance sheets without share counts (e.g. FLWS) → net income / basic EPS."""
    from investdaytip.backtest import _build_pit_stock_data

    balance_no_shares = [
        _row(pe, df, fy, {
            "stockholdersEquity": 200e9, "assets": 320e9, "totalDebt": 100e9,
            "currentAssets": 140e9, "currentLiabilities": 120e9,
        })
        for pe, df, fy in [
            ("2025-09-30", "2025-11-03", 2025),
            ("2024-09-30", "2024-11-05", 2024),
            ("2023-09-30", "2023-11-07", 2023),
        ]
    ]
    pit = PitStatements(
        ticker="TEST",
        cik=1,
        income=_parse_periods(_income_rows()),
        balance=_parse_periods(balance_no_shares),
        cash_flow=_parse_periods(_cashflow_rows()),
    )
    sd = _build_pit_stock_data(
        ticker="TEST",
        info={},
        price_history=_price_history(),
        snapshot_date=datetime(2025, 10, 15),
        pit=pit,
        dividends=pd.Series(dtype=float),
    )
    # At 2025-10-15: ni=90e9, eps=6.0 → derived shares = 15e9
    assert sd.market_cap == pytest.approx(100.0 * 15e9)
    assert sd.price_to_book == pytest.approx(100.0 / (200e9 / 15e9))


# ── run_backtest / _fetch_pit_statements guards ──────────────────────────────


def test_run_backtest_rejects_unknown_pit_source():
    from investdaytip.backtest import run_backtest

    with pytest.raises(ValueError, match="pit_source"):
        run_backtest(tickers=["AAPL"], pit_source="yahoo")


def test_run_backtest_stockfit_without_key_fails_fast(monkeypatch):
    from investdaytip.backtest import run_backtest

    monkeypatch.delenv("STOCKFIT_API_KEY", raising=False)
    with pytest.raises(StockfitError, match="STOCKFIT_API_KEY"):
        run_backtest(tickers=["AAPL"], pit_source="stockfit")


def test_fetch_pit_statements_all_fail_raises(mocker):
    from investdaytip.backtest import _fetch_pit_statements

    def fail(_ticker: str) -> PitStatements:
        raise StockfitError("boom")

    mocker.patch("investdaytip.backtest.fetch_pit_statements", side_effect=fail)
    with pytest.raises(StockfitError, match="all 2 tickers"):
        _fetch_pit_statements(["A", "B"])


def test_fetch_pit_statements_partial_failure_keeps_good_tickers(mocker):
    from investdaytip.backtest import _fetch_pit_statements

    good = PitStatements(
        ticker="GOOD", income=_parse_periods(_income_rows())
    )

    def fake(ticker: str) -> PitStatements:
        if ticker == "BAD":
            raise StockfitError("boom")
        if ticker == "EMPTY":
            return PitStatements(ticker="EMPTY")
        return good

    mocker.patch("investdaytip.backtest.fetch_pit_statements", side_effect=fake)
    result = _fetch_pit_statements(["GOOD", "BAD", "EMPTY"])
    assert set(result) == {"GOOD"}


# ── CLI wiring ───────────────────────────────────────────────────────────────


def test_cli_pit_source_passes_through(mocker):
    from investdaytip.backtest import BacktestResult
    from investdaytip.main import main

    result = BacktestResult(snapshots=[], total_snapshots=0)
    mock_run = mocker.patch("investdaytip.backtest.run_backtest", return_value=result)
    mocker.patch("investdaytip.main.export_backtest_html")

    rc = main(["backtest", "-t", "AAPL", "--pit-source", "stockfit"])
    assert rc == 0
    assert mock_run.call_args.kwargs["pit_source"] == "stockfit"

    rc = main(["backtest", "-t", "AAPL"])
    assert rc == 0
    assert mock_run.call_args.kwargs["pit_source"] == "none"


def test_cli_stockfit_without_key_returns_1(mocker, monkeypatch):
    from investdaytip.backtest import BacktestResult
    from investdaytip.main import main

    monkeypatch.delenv("STOCKFIT_API_KEY", raising=False)
    result = BacktestResult(snapshots=[], total_snapshots=0)
    mock_run = mocker.patch("investdaytip.backtest.run_backtest", return_value=result)

    rc = main(["backtest", "-t", "AAPL", "--pit-source", "stockfit"])
    assert rc == 1
    mock_run.assert_not_called()
