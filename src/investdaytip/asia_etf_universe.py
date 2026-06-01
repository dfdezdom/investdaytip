"""Default universe of UCITS and regional ETFs with significant Asian exposure.

Used when the user requests the Asia region with the ETF asset class.
Includes broad Asia market ETFs, country-specific ETFs, and sector ETFs.
"""

ASIA_ETF_UNIVERSE: list[str] = [
    # Broad Asia & Emerging Markets (US-listed on Yahoo)
    "EEM",     # iShares MSCI Emerging Markets ETF
    "VXUS",    # Vanguard Total International Stock Market ETF
    "IEMG",    # iShares Core MSCI Emerging Markets ETF
    "ASEA",    # Global X FTSE Southeast Asia ETF
    
    # Japan-specific
    "EWJ",     # iShares MSCI Japan ETF
    "EWJD",    # iShares MSCI Japan Dividend ETF
    "HEWJ",    # iShares Currency Hedged MSCI Japan ETF
    "DXJ",     # WisdomTree Japan Hedged Equity Fund
    "YEN",     # Invesco CurrencyShares Japanese Yen Trust
    
    # India-specific
    "INDA",    # iShares MSCI India ETF
    "EPI",     # WisdomTree India Earnings ETF
    "INDY",    # iShares India 50 ETF
    
    # China (excluding Hong Kong)
    "FXI",     # iShares China Large-Cap ETF
    "MCHI",    # iShares MSCI China ETF
    "CXSE",    # WisdomTree China ex-State-Owned Enterprises Fund
    "KWEB",    # KraneShares CSI China Internet ETF
    
    # South Korea
    "EWY",     # iShares MSCI South Korea ETF
    
    # Taiwan
    "EWT",     # iShares MSCI Taiwan ETF
    
    # Hong Kong
    "EWH",     # iShares MSCI Hong Kong ETF
    
    # Southeast Asia / China A-Shares
    "ASHR",    # Xtrackers Harvest CSI 300 A-Shares ETF
]
