"""Plan-aware pacing and 429 self-correction — mocked HTTP, no network.

StockFit's per-minute budgets (pricing verified 2026-10-07: Free 50,
Starter/Stock/ETF 300, Professional 500 req/min) used to be hard-coded to
≈460 req/min, so a Starter run 429'd after the first ~300 requests and
demoted most of the universe to yfinance.  These tests pin the fix: pacing
follows the detected plan, a transient 429 heals with a window-aligned
backoff, and a sustained block fails fast instead of stalling every worker.
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.error import HTTPError

import pytest

import investdaytip.data_source_stockfit as dss
from investdaytip.data_source_stockfit import (
    StockfitError,
    StockfitPlanError,
    StockfitRateLimitError,
    _backoff_429,
    _ensure_plan_rate,
    _get,
    _interval_for_plan,
    detect_plan,
)


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setenv("STOCKFIT_API_KEY", "k")


class _FakeResponse:
    """Minimal urlopen context manager serving a JSON payload."""

    def __init__(self, payload: Any) -> None:
        self._payload = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


def _err_429(**headers: str) -> HTTPError:
    return HTTPError("https://x", 429, "Too Many Requests", headers or None, None)


# ── Plan → pacing table ──────────────────────────────────────────────────────


def test_interval_for_plan_matches_official_budgets():
    # 90% safety margin over the published req/min budgets
    assert _interval_for_plan("free") == pytest.approx(60.0 / (50 * 0.9))
    assert _interval_for_plan("starter") == pytest.approx(60.0 / (300 * 0.9))
    assert _interval_for_plan("stock") == pytest.approx(60.0 / (300 * 0.9))
    assert _interval_for_plan("pro") == pytest.approx(60.0 / (500 * 0.9))
    # conservative paid-tier pacing when detection failed or no key is present
    assert _interval_for_plan("unknown") == _interval_for_plan("starter")
    assert _interval_for_plan("none") == _interval_for_plan("starter")
    # a tier must never be paced above its own budget
    for plan, budget in (("free", 50), ("starter", 300), ("pro", 500)):
        assert 60.0 / _interval_for_plan(plan) <= budget


def test_default_interval_is_paid_tier_not_professional():
    # the pre-fix default (0.13s ≈ 460 req/min) exceeded every tier but Pro
    assert dss._RATE_LIMIT_INTERVAL == _interval_for_plan("starter")
    assert dss._RATE_LIMIT_INTERVAL > 0.13


# ── detect_plan re-paces the limiter ────────────────────────────────────────


def test_detect_plan_pro_uses_professional_pacing(mocker):
    mocker.patch("investdaytip.data_source_stockfit._get", return_value=[])
    assert detect_plan() == "pro"
    assert dss._rate_limiter._min_interval == pytest.approx(_interval_for_plan("pro"))


def test_detect_plan_starter_uses_starter_pacing(mocker):
    def fake(path, params=None, **_kw):
        if path == "financials/scores":
            return []
        raise StockfitPlanError("gated")

    mocker.patch("investdaytip.data_source_stockfit._get", side_effect=fake)
    assert detect_plan() == "starter"
    assert dss._rate_limiter._min_interval == pytest.approx(_interval_for_plan("starter"))


def test_detect_plan_free_never_paces_above_free_budget(mocker):
    mocker.patch(
        "investdaytip.data_source_stockfit._get",
        side_effect=StockfitPlanError("gated"),
    )
    assert detect_plan() == "free"
    assert dss._rate_limiter._min_interval == pytest.approx(_interval_for_plan("free"))


def test_ensure_plan_rate_delegates_and_never_raises(mocker):
    patched = mocker.patch(
        "investdaytip.data_source_stockfit.detect_plan", return_value="starter"
    )
    _ensure_plan_rate()
    patched.assert_called_once()

    mocker.patch(
        "investdaytip.data_source_stockfit.detect_plan",
        side_effect=RuntimeError("detection blew up"),
    )
    _ensure_plan_rate()  # must not raise — default pacing just stays


# ── _RateLimiter penalization ───────────────────────────────────────────────


def test_penalize_doubles_interval_and_caps_under_free_budget():
    limiter = dss._RateLimiter(0.05)
    limiter.penalize()
    assert limiter._min_interval == pytest.approx(0.1)
    for _ in range(10):
        limiter.penalize()
    assert limiter._min_interval == dss._MAX_RATE_INTERVAL
    # the cap itself must satisfy even the tightest plan (Free = 50 req/min)
    assert 60.0 / dss._MAX_RATE_INTERVAL < 50


def test_set_interval_overrides_penalization():
    limiter = dss._RateLimiter(0.1)
    limiter.penalize()
    limiter.set_interval(_interval_for_plan("pro"))
    assert limiter._min_interval == pytest.approx(_interval_for_plan("pro"))


# ── 429 backoff ─────────────────────────────────────────────────────────────


def test_backoff_prefers_retry_after_header():
    assert _backoff_429(_err_429(**{"Retry-After": "7"})) == pytest.approx(7.0)
    # never wait longer than a minute on a bogus header
    assert _backoff_429(_err_429(**{"Retry-After": "3600"})) == 60.0
    assert _backoff_429(_err_429(**{"Retry-After": "soon"})) <= 60.5


def test_backoff_without_header_lands_on_next_minute_boundary():
    wait = _backoff_429(_err_429())
    # until the next one-minute window reset (+ up to 0.5s buffer)
    assert 0.5 <= wait <= 60.5
    boundary = 60.0 - (time.time() % 60.0)
    assert wait == pytest.approx(boundary + 0.5, abs=1.0)


# ── _get: transient 429 heals, sustained 429 fails fast ─────────────────────


def test_transient_429_retries_and_succeeds(mocker):
    mocker.patch("investdaytip.data_source_stockfit._backoff_429", return_value=0.0)
    dss._rate_limiter.set_interval(0.01)  # keep the test instant
    call = mocker.patch(
        "investdaytip.data_source_stockfit.urlopen",
        side_effect=[_err_429(), _FakeResponse({"data": [1]})],
    )
    payload = _get("financials/income-statement", {"symbol": "AAPL"})
    assert payload == {"data": [1]}
    assert call.call_count == 2
    # the successful answer clears the streak so the circuit stays closed …
    assert dss._429_streak == 0
    # … but the limiter stayed penalized (slower is always safe)
    assert dss._rate_limiter._min_interval == pytest.approx(0.02)


def test_streak_opens_circuit_and_fails_without_backoff(mocker):
    dss._429_streak = dss._429_STREAK_LIMIT + 1  # sustained block in progress
    dss._429_last = time.monotonic()
    backoff = mocker.patch(
        "investdaytip.data_source_stockfit._backoff_429", return_value=0.0
    )
    dss._rate_limiter.set_interval(0.0)
    call = mocker.patch(
        "investdaytip.data_source_stockfit.urlopen", side_effect=_err_429()
    )
    with pytest.raises(StockfitRateLimitError, match="sustained"):
        _get("financials/income-statement", {"symbol": "AAPL"})
    # fail fast: no window-aligned sleep, a single attempt
    backoff.assert_not_called()
    assert call.call_count == 1


def test_streak_decays_after_a_minute_without_429s(mocker):
    dss._429_streak = dss._429_STREAK_LIMIT + 1
    dss._429_last = time.monotonic() - 61.0  # last 429 was over a minute ago
    mocker.patch("investdaytip.data_source_stockfit._backoff_429", return_value=0.0)
    dss._rate_limiter.set_interval(0.0)
    mocker.patch(
        "investdaytip.data_source_stockfit.urlopen",
        side_effect=[_err_429(), _FakeResponse({"ok": True})],
    )
    # the streak decayed → the 429 is retried instead of tripping the circuit
    assert _get("financials/income-statement", {"symbol": "AAPL"}) == {"ok": True}


# ── Entry-point wiring ───────────────────────────────────────────────────────


def test_fetch_pit_statements_ensures_plan_rate(mocker):
    ensure = mocker.patch("investdaytip.data_source_stockfit._ensure_plan_rate")
    mocker.patch(
        "investdaytip.data_source_stockfit._get", side_effect=StockfitError("boom")
    )
    dss.fetch_pit_statements("AAPL")  # soft-fails to the (empty) snapshot
    ensure.assert_called_once()


def test_fetch_pit_statements_without_key_never_probes(mocker, monkeypatch):
    from investdaytip.data_source_stockfit import fetch_pit_statements

    monkeypatch.delenv("STOCKFIT_API_KEY", raising=False)
    ensure = mocker.patch("investdaytip.data_source_stockfit._ensure_plan_rate")
    fetch_pit_statements("AAPL")
    ensure.assert_not_called()


def test_fetch_fundamental_insights_ensures_plan_rate(mocker):
    ensure = mocker.patch("investdaytip.data_source_stockfit._ensure_plan_rate")
    mocker.patch(
        "investdaytip.data_source_stockfit._get", side_effect=StockfitError("boom")
    )
    assert dss.fetch_fundamental_insights("AAPL") is None  # degrades, never raises
    ensure.assert_called_once()


def test_fetch_asset_stockfit_ensures_plan_rate(mocker):
    ensure = mocker.patch("investdaytip.data_source_stockfit._ensure_plan_rate")
    mocker.patch(
        "investdaytip.data_source_stockfit._get", side_effect=StockfitError("boom")
    )
    # lookup fails → surfaced as unknown ticker (never a real network call)
    with pytest.raises(StockfitError, match="unknown ticker"):
        dss.fetch_asset_stockfit("AAPL")
    ensure.assert_called_once()
