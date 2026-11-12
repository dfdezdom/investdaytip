"""Superinvestor consensus universe — US-listed stocks with superinvestor backing.

Source: DataRoma (https://www.dataroma.com) — 13F filings from the ~82
superinvestors tracked by the platform.

The list is a **hand-curated** subset, not the mechanical "≥2 managers" set:
in the Q3 2026 filings that cut alone matches 794 symbols. A ticker qualifies
when all of these hold:

- it appears in at least two independent managers' portfolios (the consensus
  signal the feature exists for);
- it is a large cap — the $10B floor applied when the list was last pruned;
- it is a stock, not a fund (``SGOV`` was removed for sitting at the top of
  the conservative-count ranking as a Treasury ETF);
- one share class only — ``GOOG`` is merged into ``GOOGL`` and ``BRK-A`` is
  deliberately excluded (its share price makes a position unscorable).

Audited 2026-11-12 against the live DataRoma aggregate (82/82 managers, Q2/Q3
2026 filings, 1742 symbols): kept every ticker still held by two or more
managers and added the 40 that met the criteria but were missing, 11 of them
held by 8+ managers (``ACN``, ``GEV``, ``TEL``, ``MSI``, ``MICC``, ``EFX``,
``DEO``). Only ``MGM`` now sits below the $10B floor. Re-run the audit with
``python scripts/superinvestor_audit.py --min-market-cap 10`` — the list is
refreshed by hand, so nothing in CI fails when it drifts.
"""

SUPERINVESTOR_UNIVERSE: list[str] = [
    "AAPL", "ACN", "ADI", "ADP", "AMAT", "AMZN", "AON", "APG",
    "APH", "APP", "ASML", "AVGO", "AVTR", "AXP", "B", "BABA",
    "BAC", "BAX", "BDX", "BF-B", "BKNG", "BN", "BRK-B", "C",
    "CBRE", "CHTR", "CLX", "CNH", "CNI", "COF", "CP", "CPNG",
    "CRM", "CRS", "CSGP", "CTSH", "CVNA", "CVS", "CVX", "DE",
    "DEO", "DHR", "DIS", "DVN", "EFX", "ELV", "EPD", "EQH",
    "ET", "FDX", "FERG", "FICO", "FIVE", "FWONK", "GE", "GEHC",
    "GEV", "GM", "GNRC", "GOOGL", "GPC", "GSK", "HDB", "HLN",
    "IBKR", "ICE", "ICLR", "IDXX", "IFF", "INTU", "JD", "JLL",
    "JNJ", "JPM", "KHC", "KKR", "KOF", "KSPI", "LEN", "LFUS",
    "LLY", "LYV", "MA", "MCO", "MDLN", "META", "MGM", "MICC",
    "MKL", "MRK", "MSFT", "MSI", "NFLX", "NU", "NVDA", "NVS",
    "OXY", "PDD", "PEP", "PFE", "PM", "PSX", "PYPL", "QSR",
    "RACE", "REGN", "RKT", "RPRX", "RTX", "SAP", "SCHW", "SE",
    "SGI", "SHW", "SIRI", "SLB", "SNX", "SPCX", "SPGI", "SUNB",
    "SYK", "TDG", "TECH", "TEL", "TMO", "TRU", "TSLA", "TSM",
    "TSN", "UBER", "UHAL", "UNH", "USB", "V", "VEEV", "VMC",
    "VST", "WAT", "WTW", "WY", "ZBH",
]
