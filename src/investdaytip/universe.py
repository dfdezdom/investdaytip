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
    "ABBV", "ABT", "DHR", "INCY", "INDV",
    "JNJ", "LLY", "MRK", "PFE", "TMO", "UNH",
    # Financials
    "ALL", "AXP", "BAC", "BLK", "GS", "JPM", "MA", "MS",
    "SEZL", "THG", "V", "WFC",
    # Industrials / Energy / Materials
    "BA", "CAT", "CRH", "CSTM", "CVX", "GE", "HON", "IAG",
    "LIN", "UPS", "XOM",
    # Communications / Utilities / Real Estate
    "NFLX", "T", "VZ", "NEE", "AMT",
]
