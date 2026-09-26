"""Tests for the local StockFit PIT snapshot layer — no network, no real home."""

from __future__ import annotations

from datetime import datetime

import pytest

from investdaytip.data_source_stockfit import (
    PitPeriod,
    PitStatements,
    StockfitError,
    check_pit_access,
    fetch_pit_statements,
    load_pit_snapshot,
    save_pit_snapshot,
    snapshot_available,
    snapshot_dir,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _period(fy: int, period_end: str, date_filed: str) -> PitPeriod:
    return PitPeriod(
        period_end=datetime.strptime(period_end, "%Y-%m-%d"),
        fiscal_year=fy,
        fiscal_period="FY",
        date_filed=datetime.strptime(date_filed, "%Y-%m-%d"),
        facts={"revenue": 100e9 * fy, "netIncome": 10e9 * fy},
    )


def _statements(ticker: str = "AAPL") -> PitStatements:
    return PitStatements(
        ticker=ticker,
        cik=320193,
        stitched=ticker == "XOM",
        income=[_period(2025, "2025-09-30", "2025-11-01"),
                _period(2024, "2024-09-30", "2024-11-02")],
        balance=[_period(2025, "2025-09-30", "2025-11-03")],
        cash_flow=[_period(2025, "2025-09-30", "2025-11-01")],
    )


# ── save / load ──────────────────────────────────────────────────────────────


def test_save_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKFIT_PIT_SNAPSHOT_DIR", str(tmp_path / "pit"))
    assert save_pit_snapshot(_statements()) is True

    loaded = load_pit_snapshot("AAPL")
    assert loaded is not None
    assert loaded.ticker == "AAPL"
    assert loaded.cik == 320193
    assert loaded.stitched is False
    assert len(loaded.income) == 2
    assert loaded.income[0].fiscal_year == 2025  # sorted newest first
    assert loaded.income[0].date_filed == datetime(2025, 11, 1)
    assert loaded.income[1].fact("revenue") == 100e9 * 2024
    assert len(loaded.balance) == 1
    assert len(loaded.cash_flow) == 1
    assert snapshot_available() is True


def test_save_empty_statements_not_written(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKFIT_PIT_SNAPSHOT_DIR", str(tmp_path / "pit"))
    assert save_pit_snapshot(PitStatements(ticker="AAPL")) is False
    assert not snapshot_dir().exists()
    assert snapshot_available() is False


def test_load_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKFIT_PIT_SNAPSHOT_DIR", str(tmp_path / "pit"))
    assert load_pit_snapshot("NOPE") is None


def test_load_corrupt_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKFIT_PIT_SNAPSHOT_DIR", str(tmp_path / "pit"))
    snapshot_dir().mkdir(parents=True)
    (snapshot_dir() / "AAPL.json").write_text("{not json", encoding="utf-8")
    assert load_pit_snapshot("AAPL") is None


def test_load_empty_income_returns_none(tmp_path, monkeypatch):
    """A file with no income periods is treated as unusable, not as data."""
    monkeypatch.setenv("STOCKFIT_PIT_SNAPSHOT_DIR", str(tmp_path / "pit"))
    save_pit_snapshot(_statements("AAPL"))
    (snapshot_dir() / "AAPL.json").write_text(
        '{"ticker": "AAPL", "income": [], "balance": [], "cash_flow": []}',
        encoding="utf-8",
    )
    assert load_pit_snapshot("AAPL") is None


# ── check_pit_access ─────────────────────────────────────────────────────────


def test_check_pit_access_with_key(monkeypatch, tmp_path):
    monkeypatch.setenv("STOCKFIT_PIT_SNAPSHOT_DIR", str(tmp_path / "pit"))
    monkeypatch.setenv("STOCKFIT_API_KEY", "k")
    check_pit_access()  # no raise


def test_check_pit_access_with_snapshot(monkeypatch, tmp_path):
    monkeypatch.setenv("STOCKFIT_PIT_SNAPSHOT_DIR", str(tmp_path / "pit"))
    monkeypatch.delenv("STOCKFIT_API_KEY", raising=False)
    save_pit_snapshot(_statements())
    check_pit_access()  # no raise


def test_check_pit_access_without_both_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("STOCKFIT_PIT_SNAPSHOT_DIR", str(tmp_path / "pit"))
    monkeypatch.delenv("STOCKFIT_API_KEY", raising=False)
    with pytest.raises(StockfitError, match="pit_snapshot.py"):
        check_pit_access()


# ── fetch_pit_statements resolution order ────────────────────────────────────


def test_fetch_without_key_uses_snapshot(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("STOCKFIT_PIT_SNAPSHOT_DIR", str(tmp_path / "pit"))
    monkeypatch.delenv("STOCKFIT_API_KEY", raising=False)
    save_pit_snapshot(_statements("AAPL"))

    guard = mocker.patch(
        "investdaytip.data_source_stockfit._get",
        side_effect=AssertionError("network must not be touched without a key"),
    )
    stmts = fetch_pit_statements("AAPL")
    assert len(stmts.income) == 2
    assert stmts.income[0].fiscal_year == 2025
    guard.assert_not_called()


def test_fetch_with_key_saves_snapshot(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("STOCKFIT_PIT_SNAPSHOT_DIR", str(tmp_path / "pit"))
    monkeypatch.setenv("STOCKFIT_API_KEY", "k")

    rows = {
        "financials/income-statement": [
            {"period": f"{fy}-09-30", "fiscalYear": fy, "fiscalPeriod": "FY",
             "dateFiled": f"{fy}-11-01", "facts": {"revenue": 400e9}}
            for fy in (2025, 2024, 2023)
        ],
        "financials/balance-sheet": [],
        "financials/cash-flow-statement": [],
    }
    mocker.patch(
        "investdaytip.data_source_stockfit._get",
        side_effect=lambda path, params=None, **kw: rows[path],
    )
    stmts = fetch_pit_statements("AAPL")
    assert len(stmts.income) == 3

    saved = load_pit_snapshot("AAPL")
    assert saved is not None
    assert saved.income[0].fiscal_year == 2025


def test_fetch_live_failure_falls_back_to_snapshot(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("STOCKFIT_PIT_SNAPSHOT_DIR", str(tmp_path / "pit"))
    monkeypatch.setenv("STOCKFIT_API_KEY", "k")
    save_pit_snapshot(_statements("AAPL"))

    mocker.patch(
        "investdaytip.data_source_stockfit._get",
        side_effect=StockfitError("boom"),
    )
    stmts = fetch_pit_statements("AAPL")
    assert len(stmts.income) == 2  # snapshot data, not an empty result


def test_fetch_live_empty_falls_back_to_snapshot_and_never_wipes(
    tmp_path, monkeypatch, mocker
):
    monkeypatch.setenv("STOCKFIT_PIT_SNAPSHOT_DIR", str(tmp_path / "pit"))
    monkeypatch.setenv("STOCKFIT_API_KEY", "k")
    save_pit_snapshot(_statements("AAPL"))

    mocker.patch(
        "investdaytip.data_source_stockfit._get", return_value=[]
    )
    stmts = fetch_pit_statements("AAPL")
    assert len(stmts.income) == 2
    saved = load_pit_snapshot("AAPL")
    assert saved is not None and len(saved.income) == 2  # not wiped


def test_fetch_no_key_no_snapshot_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKFIT_PIT_SNAPSHOT_DIR", str(tmp_path / "pit"))
    monkeypatch.delenv("STOCKFIT_API_KEY", raising=False)
    stmts = fetch_pit_statements("AAPL")
    assert stmts.income == []
    assert stmts.balance == []
    assert stmts.cash_flow == []


# ── backtest CLI wiring ──────────────────────────────────────────────────────


def test_cli_stockfit_without_key_but_with_snapshot_runs(mocker, monkeypatch, tmp_path):
    from investdaytip.backtest import BacktestResult
    from investdaytip.main import main

    monkeypatch.delenv("STOCKFIT_API_KEY", raising=False)
    monkeypatch.setenv("STOCKFIT_PIT_SNAPSHOT_DIR", str(tmp_path / "pit"))
    save_pit_snapshot(_statements("AAPL"))

    result = BacktestResult(snapshots=[], total_snapshots=0)
    mock_run = mocker.patch("investdaytip.backtest.run_backtest", return_value=result)
    mocker.patch("investdaytip.main.export_backtest_html")

    rc = main(["backtest", "-t", "AAPL", "--pit-source", "stockfit"])
    assert rc == 0
    mock_run.assert_called_once()


def test_run_backtest_with_snapshot_but_no_key_starts(mocker, monkeypatch, tmp_path):
    """run_backtest's fail-fast passes when only a snapshot is available."""
    from investdaytip.backtest import BacktestResult, run_backtest

    monkeypatch.delenv("STOCKFIT_API_KEY", raising=False)
    monkeypatch.setenv("STOCKFIT_PIT_SNAPSHOT_DIR", str(tmp_path / "pit"))
    save_pit_snapshot(_statements("AAPL"))

    mock_fetch = mocker.patch(
        "investdaytip.backtest._fetch_all_data", return_value={}
    )
    result = run_backtest(tickers=["AAPL"], pit_source="stockfit", period="1y")
    assert isinstance(result, BacktestResult)
    mock_fetch.assert_called_once()


# ── scripts/pit_snapshot.py ──────────────────────────────────────────────────


def _load_script():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "scripts" / "pit_snapshot.py"
    spec = importlib.util.spec_from_file_location("pit_snapshot_script", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_script_status_empty_and_populated(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("STOCKFIT_PIT_SNAPSHOT_DIR", str(tmp_path / "pit"))
    script = _load_script()

    assert script.main(["--status"]) == 1
    assert "No snapshots" in capsys.readouterr().out

    save_pit_snapshot(_statements("AAPL"))
    assert script.main(["--status"]) == 0
    out = capsys.readouterr().out
    assert "AAPL" in out
    assert "2 FY" in out


def test_script_without_key_returns_1(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("STOCKFIT_PIT_SNAPSHOT_DIR", str(tmp_path / "pit"))
    monkeypatch.delenv("STOCKFIT_API_KEY", raising=False)
    script = _load_script()

    assert script.main(["-t", "AAPL"]) == 1
    assert "STOCKFIT_API_KEY" in capsys.readouterr().err
