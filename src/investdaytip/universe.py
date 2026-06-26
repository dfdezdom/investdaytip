"""Default universe of large-cap, liquid US stocks across sectors.

Used when the user doesn't provide a custom ticker list. Curated to give
the multi-factor model a diverse pool for long-term candidates.
"""

DEFAULT_UNIVERSE: list[str] = [
    # Technology
    "AAPL", "ADBE", "AMD", "AVGO", "CRM", "CSCO", "GOOGL", "IBM",
    "INTC", "META", "MSFT", "MU", "NVDA", "ORCL", "QCOM", "TXN",
    # Consumer
    "AMZN", "TSLA", "HD", "MCD", "NKE", "SBUX", "COST", "WMT", "PG",
    "KO", "PEP", "DIS",
    # Healthcare
    "ABBV", "ABT", "AUPH", "AVAH", "DHR", "INCY", "INDV", "INVA",
    "JNJ", "LLY", "MRK", "PFE", "TMO", "UNH",
    # Financials
    "ALL", "AXP", "BAC", "BLK", "GS", "JPM", "MA", "MS",
    "SEZL", "THG", "UVE", "V", "WFC",
    # Industrials / Energy / Materials
    "BA", "CAT", "CSTM", "CVX", "GE", "HON", "IAG",
    "LIN", "SHIP", "UPS", "XOM",
    # Communications / Utilities / Real Estate
    "NFLX", "T", "VZ", "NEE", "AMT",
]
