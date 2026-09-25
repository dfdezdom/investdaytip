"""Data-integrity checks for the curated ticker universes.

These guard against malformed symbols and accidental cross-list duplicates;
they do not hit the network.
"""
from __future__ import annotations

import re

import pytest

from investdaytip.asia_etf_universe import ASIA_ETF_UNIVERSE
from investdaytip.asia_universe import ASIA_UNIVERSE
from investdaytip.etf_universe import DEFAULT_ETF_UNIVERSE
from investdaytip.eu_etf_universe import DEFAULT_EU_ETF_UNIVERSE
from investdaytip.eu_universe import DEFAULT_EU_UNIVERSE
from investdaytip.superinvestor_universe import SUPERINVESTOR_UNIVERSE
from investdaytip.universe import DEFAULT_UNIVERSE

# Yahoo symbols: a base (letters/digits, may contain - or .) optionally followed
# by a single exchange suffix. This is intentionally permissive but rejects
# whitespace and empty tokens.
_TICKER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]*$")

ALL_UNIVERSES = {
    "us_stocks": DEFAULT_UNIVERSE,
    "eu_stocks": DEFAULT_EU_UNIVERSE,
    "asia_stocks": ASIA_UNIVERSE,
    "superinvestor_stocks": SUPERINVESTOR_UNIVERSE,
    "us_etfs": DEFAULT_ETF_UNIVERSE,
    "eu_etfs": DEFAULT_EU_ETF_UNIVERSE,
    "asia_etfs": ASIA_ETF_UNIVERSE,
}


@pytest.mark.parametrize("name,universe", list(ALL_UNIVERSES.items()))
def test_tickers_are_well_formed(name, universe):
    assert universe, f"{name} universe is empty"
    for t in universe:
        assert isinstance(t, str) and t, f"{name}: empty ticker"
        assert t == t.strip(), f"{name}: '{t}' has surrounding whitespace"
        assert _TICKER_RE.match(t), f"{name}: malformed ticker '{t}'"


@pytest.mark.parametrize("name,universe", list(ALL_UNIVERSES.items()))
def test_no_intra_universe_duplicates(name, universe):
    seen: dict[str, str] = {}
    for t in universe:
        key = t.upper()
        assert key not in seen, f"{name}: duplicate '{t}' (also '{seen[key]}')"
        seen[key] = t


def test_asia_known_corrections_applied():
    # Regression guard for the fixes in Phase 3.
    assert "INFY.NS" in ASIA_UNIVERSE
    assert "INFOSY.NS" not in ASIA_UNIVERSE
    assert "CPU.AX" in ASIA_UNIVERSE
    assert "CCP.AX" not in ASIA_UNIVERSE


def test_asia_etf_invalid_symbols_removed():
    for bad in ("EUNL", "EOKH", "ASDX", "VTIAX"):
        assert bad not in ASIA_ETF_UNIVERSE, f"{bad} should have been removed"


def test_no_duplicate_alphabet_in_superinvestor():
    assert "GOOG" not in SUPERINVESTOR_UNIVERSE, "GOOG should be merged into GOOGL"
    assert "GOOGL" in SUPERINVESTOR_UNIVERSE


def test_delisted_and_renamed_symbols_removed():
    """Symbols Yahoo no longer resolves — regression guard (verified 2026-09-25).

    Both were caught by a full-universe sweep against the Yahoo chart endpoint:
    they return a one-key ``info`` dict and an empty history.
    """
    # SPLG was renamed SPYM on 2025-10-31 (State Street rebrand).
    assert "SPLG" not in DEFAULT_ETF_UNIVERSE
    assert "SPYM" in DEFAULT_ETF_UNIVERSE

    # CRH plc delisted from London and Dublin; it trades on NYSE as CRH.
    assert "CRH.L" not in DEFAULT_EU_UNIVERSE
    assert "CRH" in DEFAULT_UNIVERSE
