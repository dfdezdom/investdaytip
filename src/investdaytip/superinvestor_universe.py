"""Superinvestor consensus universe — US-listed stocks with superinvestor backing.

Source: DataRoma (https://www.dataroma.com) — 13F filings from the ~82
superinvestors tracked by the platform.

The list is a **hand-curated** subset, not the mechanical "≥2 managers" set:
in the Q3 2026 filings 784 symbols are held by two or more managers and 375 of
those clear the size/fund filter without being curated here (a good half of the
US large-cap market). A ticker qualifies when all of these hold:

- it appears in **five or more** independent managers' portfolios — the
  consensus signal the feature exists for. Five is the floor new entries are
  held to; a handful of older names sit at two or three and stay;
- it is a large cap — the $10B floor applied when the list was last pruned;
- it is a stock, not a fund (``SGOV`` was removed for sitting at the top of
  the conservative-count ranking as a Treasury ETF);
- one share class only — ``GOOG`` is merged into ``GOOGL`` and ``BRK-A`` is
  deliberately excluded (its share price makes a position unscorable).

Audited 2026-11-17 against the live DataRoma aggregate — 82/82 managers, the
Q3 2026 cycle complete (the 45-day deadline fell on Saturday 2026-11-14 and was
observed on the Monday), 1726 symbols. No curated ticker had left every
portfolio. The ten names that met the rule while sitting in no pool at all are
now in: ``A``, ``AER``, ``ALLY``, ``CCK``, ``CSL``, ``DAL``, ``FTV``, ``HONA``,
``MLM``, ``NEM`` — five managers each, $11.2B to $114.8B. ``CRS`` fell to a
single manager that quarter and is **kept on purpose**: it is a $13B large cap
that entered on consensus, and one quarter's filing is not a curation event.
The median curated name is held by seven managers; only ``FIVE`` and ``UHAL``
(2) sit below five. Re-run the audit with
``python scripts/superinvestor_audit.py --min-market-cap 10`` — the list is
refreshed by hand, so nothing in CI fails when it drifts.
"""

SUPERINVESTOR_UNIVERSE: list[str] = [
    "A", "AAPL", "ACN", "ADI", "ADP", "AER", "ALLY", "AMAT",
    "AMZN", "AON", "APG", "APH", "APP", "ASML", "AVGO", "AVTR",
    "AXP", "B", "BABA", "BAC", "BAX", "BDX", "BF-B", "BKNG",
    "BN", "BRK-B", "C", "CBRE", "CCK", "CHTR", "CLX", "CNH",
    "CNI", "COF", "CP", "CPNG", "CRM", "CRS", "CSGP", "CSL",
    "CTSH", "CVNA", "CVS", "CVX", "DAL", "DE", "DEO", "DHR",
    "DIS", "DVN", "EFX", "ELV", "EPD", "EQH", "ET", "FDX",
    "FERG", "FICO", "FIVE", "FTV", "FWONK", "GE", "GEHC", "GEV",
    "GM", "GNRC", "GOOGL", "GPC", "GSK", "HDB", "HLN", "HONA",
    "IBKR", "ICE", "ICLR", "IDXX", "IFF", "INTU", "JD", "JLL",
    "JNJ", "JPM", "KHC", "KKR", "KOF", "KSPI", "LEN", "LFUS",
    "LLY", "LYV", "MA", "MCO", "MDLN", "META", "MGM", "MICC",
    "MKL", "MLM", "MRK", "MSFT", "MSI", "NEM", "NFLX", "NU",
    "NVDA", "NVS", "OXY", "PDD", "PEP", "PFE", "PM", "PSX",
    "PYPL", "QSR", "RACE", "REGN", "RKT", "RPRX", "RTX", "SAP",
    "SCHW", "SE", "SGI", "SHW", "SIRI", "SLB", "SNX", "SPCX",
    "SPGI", "SUNB", "SYK", "TDG", "TECH", "TEL", "TMO", "TRU",
    "TSLA", "TSM", "TSN", "UBER", "UHAL", "UNH", "USB", "V",
    "VEEV", "VMC", "VST", "WAT", "WTW", "WY", "ZBH",
]
