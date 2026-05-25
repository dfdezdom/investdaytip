"""Default European UCITS ETF universe — broad, low-cost, long-term oriented.

Tickers use Yahoo Finance exchange suffixes (.DE, .AS, .L, .MI, .PA, .SW).
"""

DEFAULT_EU_ETF_UNIVERSE: list[str] = [
    # Global / Developed Markets
    "VWCE.DE",   # Vanguard FTSE All-World UCITS
    "IWDA.AS",   # iShares Core MSCI World UCITS
    "SWDA.MI",   # iShares Core MSCI World UCITS (Milan)
    "EUNL.DE",   # iShares Core MSCI World UCITS (Xetra)
    "VHVE.L",    # Vanguard FTSE Developed World UCITS
    # S&P 500 (EUR-listed)
    "VUSA.AS", "CSPX.AS", "SXR8.DE", "VUAA.DE",
    # Nasdaq 100
    "EQQQ.L", "CNDX.AS",
    # Europe
    "VEUR.AS",   # Vanguard FTSE Developed Europe
    "CEU.PA",    # Amundi MSCI Europe
    "MEUD.PA",   # Lyxor Core STOXX Europe 600
    "EXSA.DE",   # iShares STOXX Europe 600
    # Eurozone
    "EXW1.DE",   # iShares EURO STOXX 50 (Xetra)
    "EUE.L",
    # Emerging Markets
    "EMIM.AS", "EIMI.L", "VFEM.L",
    # Small caps
    "WSML.L", "ZPRS.DE",
    # Factor / Quality / Dividend
    "IWQU.L",    # iShares Edge MSCI World Quality Factor
    "VHYL.AS",   # Vanguard FTSE All-World High Dividend
    # Bonds
    "AGGH.AS", "EUNA.DE", "VAGP.L",
    # Thematic core
    "WTEC.MI",   # iShares MSCI World IT
    # Sector ETFs (Europe-listed UCITS)
    # Semiconductors
    "SMH.L",     # VanEck Semiconductor UCITS ETF (London)
    # Quantum computing
    "QNTM.L",    # VanEck Quantum Computing UCITS ETF (London)
    "QANT.L",    # iShares Quantum Computing UCITS ETF (London)
    # Space / Aerospace
    "JEDI.L",    # VanEck Space Innovators UCITS ETF (London)
]
