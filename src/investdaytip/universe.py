"""Default universe of large-cap, liquid US stocks across sectors.

Used when the user doesn't provide a custom ticker list. Curated to give
the multi-factor model a diverse pool for long-term candidates.
"""

DEFAULT_UNIVERSE: list[str] = [
    # Technology
    "AAPL", "ADBE", "AMD", "AVGO", "CRM", "CSCO", "GOOGL", "IBM",
    "INTC", "META", "MSFT", "MU", "NVDA", "ORCL", "QCOM", "TXN",
    # Semiconductors, networking & hardware
    "LRCX", "KLAC", "MRVL", "ARM", "ANET", "DELL", "HPE", "SNDK",
    "KEYS", "MPWR", "NXPI", "ON", "STX", "TER", "WDC",
    # Software & security
    "NOW", "PLTR", "SNPS", "DDOG", "SNOW", "CRWD", "PANW", "FTNT",
    "WDAY", "ADSK", "ROP", "SHOP",
    # Consumer
    "AMZN", "TSLA", "HD", "MCD", "NKE", "SBUX", "COST", "WMT", "PG",
    "KO", "PEP", "DIS", "MELI", "TGT", "TJX", "ORLY", "LOW", "MAR",
    "EBAY", "ABNB", "DASH", "AZO", "CMG", "DHI", "PHM", "ROST", "YUM",
    # Consumer staples
    "ADM", "CL", "DG", "DLTR", "HSY", "KDP", "KMB", "KR", "MDLZ",
    "MNST", "MO", "SYY",
    # Healthcare
    "ABBV", "ABT", "DHR", "INCY", "INDV",
    "JNJ", "LLY", "MRK", "PFE", "TMO", "UNH",
    "AMGN", "GILD", "VRTX", "ISRG", "MDT", "BSX", "BMY", "CI", "HCA", "EW",
    "HUM", "ZTS",
    # Financials
    "ALL", "AXP", "BAC", "BLK", "GS", "JPM", "MA", "MS",
    "SEZL", "THG", "V", "WFC",
    "BX", "CME", "MSCI",
    # Financials — insurance, banks & asset managers
    "ACGL", "AIG", "AJG", "APO", "ARES", "BAM", "BNY", "CB", "COIN",
    "FISV", "L", "MET", "MRSH", "NDAQ", "NTRS", "PFG", "PGR", "PRU",
    "STT", "TRV", "TROW",
    # Industrials / Energy / Materials
    "BA", "CAT", "CRH", "CSTM", "CVX", "GE", "HON", "IAG",
    "LIN", "UPS", "XOM",
    "COP", "EOG", "VLO", "ETN", "EMR", "PH",
    # Industrials — transport, defense & machinery
    "AME", "CARR", "CMI", "CPRT", "CSX", "CTAS", "DOV", "EXPD", "FAST",
    "GD", "GWW", "IR", "ITW", "LMT", "LUV", "MMM", "NOC", "NSC", "ODFL",
    "OTIS", "PCAR", "PWR", "RSG", "TT", "UNP", "URI", "VRT", "WM",
    # Materials
    "APD", "ECL",
    # Communications / Utilities / Real Estate
    "NFLX", "T", "VZ", "NEE", "AMT",
    "TMUS", "CMCSA", "SPOT",
    "SO", "DUK", "D", "PLD", "EQIX", "SPG",
]
