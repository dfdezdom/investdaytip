"""Default universe of large-cap, liquid US stocks across sectors.

Used when the user doesn't provide a custom ticker list. Curated to give
the multi-factor model a diverse pool for long-term candidates.
"""

DEFAULT_UNIVERSE: list[str] = [
    # Technology
    "AAPL", "MSFT", "GOOGL", "META", "NVDA", "AVGO", "ORCL", "ADBE",
    "CRM", "CSCO", "INTC", "AMD", "QCOM", "TXN", "IBM",
    # Consumer
    "AMZN", "TSLA", "HD", "MCD", "NKE", "SBUX", "COST", "WMT", "PG",
    "KO", "PEP", "DIS",
    # Healthcare
    "JNJ", "UNH", "PFE", "MRK", "ABBV", "LLY", "TMO", "ABT", "DHR",
    # Financials
    "JPM", "BAC", "WFC", "GS", "MS", "V", "MA", "BLK", "AXP",
    # Industrials / Energy / Materials
    "BA", "CAT", "GE", "HON", "UPS", "XOM", "CVX", "LIN",
    # Communications / Utilities / Real Estate
    "NFLX", "T", "VZ", "NEE", "AMT",
]
