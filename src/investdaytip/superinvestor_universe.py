"""Superinvestor consensus universe — tickers held by ≥2 top investors.

Source: DataRoma (https://www.dataroma.com) — 13F filings from ~82
superinvestors tracked by the platform. Only tickers with at least two
independent manager positions are included.
"""

SUPERINVESTOR_UNIVERSE: list[str] = [
    "AAPL", "ADI", "AMAT", "AMZN", "AON", "APG", "APP",
    "ASML", "AVGO", "AXP", "BABA", "BAC", "BAX", "BDX", "BKNG", "BN",
    "BRK-B", "C", "CBRE", "CNI", "COF", "CP", "CPNG", "CRM",
    "CRS", "CSGP", "CVNA", "CVS", "CVX", "DE", "DHR", "DIS",
    "ELV", "ET", "FDX", "FERG", "FICO", "FIVE", "FWONK", "GE", "GEHC",
    "GOOGL", "IBKR", "ICE", "IFF", "INTU", "JNJ", "JPM",
    "KHC", "KKR", "KOF", "LEN", "LLY", "LYV", "MA", "MCO",
    "MDLN", "META", "MGM", "MKL", "MRK", "MSFT", "NFLX", "NU",
    "NVDA", "OXY", "PDD", "PEP", "PFE", "PM",
    "PYPL", "QSR", "RACE", "REGN", "RKT", "RPRX", "RTX", "SCHW",
    "SGI", "SGOV", "SHW", "SLB", "SNX", "SPGI", "SUNB", "SYK",
    "TDG", "TMO", "TRU", "TSLA", "TSM", "TSN",     "UBER", "UHAL",
    "UNH", "USB", "V", "VMC", "VST", "WAT", "WTW", "ZBH",
]
