"""Default ETF universe — diversified, low-cost, long-term oriented."""

DEFAULT_ETF_UNIVERSE: list[str] = [
    # Broad US market
    "VTI", "ITOT", "SCHB",
    # S&P 500
    "VOO", "SPY", "IVV", "SPLG",
    # Growth / Tech
    "QQQ", "QQQM", "VUG", "SCHG", "VGT", "XLK",
    # Value / Dividend
    "VTV", "SCHD", "VYM", "DGRO", "VIG",
    # Small / Mid cap
    "VB", "IJR", "VO", "IJH",
    # International / Emerging
    "VXUS", "VEA", "IEFA", "VWO", "IEMG",
    # Sectors
    "XLF", "XLV", "XLE", "XLY", "XLP", "XLI", "XLU", "XLRE",
    # Fixed income (defensive long-term holdings)
    "BND", "AGG", "BNDX",
    # Thematic / Quality
    "QUAL", "MTUM", "USMV",
]
