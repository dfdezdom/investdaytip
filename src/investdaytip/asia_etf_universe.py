"""Default universe of UCITS and regional ETFs with significant Asian exposure.

Used when the user requests the Asia region with the ETF asset class.
Includes broad Asia market ETFs, country-specific ETFs, and sector ETFs.
"""

ASIA_ETF_UNIVERSE: list[str] = [
    # Broad Asia & Emerging Markets (US-listed on Yahoo)
    "EEM",     # iShares MSCI Emerging Markets ETF
    "VXUS",    # Vanguard Total International Stock Market ETF
    "IEMG",    # iShares Core MSCI Emerging Markets ETF
    "ASEA",    # Invesco Emerging Markets Sovereign Debt ETF
    
    # Japan-specific
    "EWJ",     # iShares MSCI Japan ETF
    "EWJD",    # iShares MSCI Japan Dividend ETF
    "JPX",     # iShares MSCI Japan USD Hedged ETF
    "DXJ",     # Wisdom Tree Japan Hedged Equity Fund
    "YEN",     # Invesco CurrencyShares Japanese Yen Trust
    
    # India-specific
    "INDA",    # iShares MSCI India ETF
    "INDL",    # WisdomTree India Earnings ETF
    "INDY",    # Invesco India ETF
    
    # China (excluding Hong Kong)
    "FXI",     # iShares China Large-Cap ETF
    "MCHI",    # iShares MSCI China ETF
    "CXSE",    # Invesco CSI China Internet ETF
    "KWEB",    # Invesco QQQ China Tech ETF
    
    # South Korea
    "EWY",     # iShares MSCI South Korea ETF
    "EOKH",    # iShares MSCI South Korea Capped ETF
    
    # Taiwan
    "EWT",     # iShares MSCI Taiwan ETF
    
    # Hong Kong
    "EWH",     # iShares MSCI Hong Kong ETF
    
    # Southeast Asia
    "ASHR",    # Xtrackers Harvest CSI A-Shares ETF
    "VTIAX",   # Vanguard International Total Stock Market ETF
    
    # UCITS ETFs (European-listed, accessible in EU)
    "ASDX",    # iShares MSCI AC Asia ex Japan UCITS ETF (if available on Yahoo)
    "EUNL",    # iShares Core DAX UCITS ETF (reference for structure)
]
