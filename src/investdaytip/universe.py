"""Default universe of large-cap, liquid US stocks across sectors.

Used when the user doesn't provide a custom ticker list. Curated to give
the multi-factor model a diverse pool for long-term candidates.
"""

DEFAULT_UNIVERSE: list[str] = [
    # Technology
    "AAPL", "ADBE", "AMD", "AVGO", "CRM", "CSCO", "GOOGL", "IBM",
    "INTC", "META", "MSFT", "MU", "NVDA", "ORCL", "QCOM", "TXN",
    # Semiconductors, networking & hardware
    "LRCX", "KLAC", "MRVL", "ARM", "ANET", "DELL", "HPE",
    # Software & security
    "NOW", "PLTR", "SNPS", "DDOG", "SNOW", "CRWD", "PANW", "FTNT",
    "WDAY", "ADSK",
    # Consumer
    "AMZN", "TSLA", "HD", "MCD", "NKE", "SBUX", "COST", "WMT", "PG",
    "KO", "PEP", "DIS", "MELI", "TGT", "TJX", "ORLY", "LOW", "MAR",
    "EBAY", "ABNB", "DASH",
    # Healthcare
    "ABBV", "ABT", "DHR", "INCY", "INDV",
    "JNJ", "LLY", "MRK", "PFE", "TMO", "UNH",
    "AMGN", "GILD", "VRTX", "ISRG", "MDT", "BSX", "BMY", "CI", "HCA", "EW",
    # Financials
    "ALL", "AXP", "BAC", "BLK", "GS", "JPM", "MA", "MS",
    "SEZL", "THG", "V", "WFC",
    "BX", "CME", "MSCI",
    # Industrials / Energy / Materials
    "BA", "CAT", "CRH", "CSTM", "CVX", "GE", "HON", "IAG",
    "LIN", "UPS", "XOM",
    "COP", "EOG", "VLO", "ETN", "EMR", "PH",
    # Communications / Utilities / Real Estate
    "NFLX", "T", "VZ", "NEE", "AMT",
    "TMUS", "CMCSA", "SPOT",
    "SO", "DUK", "D", "PLD", "EQIX", "SPG",
]
