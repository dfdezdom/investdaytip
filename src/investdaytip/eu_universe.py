"""Default European stock universe — large-caps from major indices.

Covers EURO STOXX 50, DAX, CAC 40, FTSE 100, IBEX 35, AEX, SMI, FTSE MIB.
Tickers use Yahoo Finance exchange suffixes (.DE, .PA, .L, .MC, .AS, .SW, .MI).
"""

DEFAULT_EU_UNIVERSE: list[str] = [
    # Germany (Xetra, .DE)
    "SAP.DE", "SIE.DE", "ALV.DE", "DTE.DE", "MBG.DE", "BMW.DE",
    "BAS.DE", "BAYN.DE", "MUV2.DE", "IFX.DE", "ADS.DE", "DBK.DE",
    # France (Euronext Paris, .PA)
    "MC.PA", "OR.PA", "AIR.PA", "SAN.PA", "TTE.PA", "BNP.PA",
    "RMS.PA", "CS.PA", "DG.PA", "EL.PA", "KER.PA", "SU.PA",
    # Netherlands (Euronext Amsterdam, .AS)
    "ASML.AS", "PRX.AS", "INGA.AS", "AD.AS", "HEIA.AS", "PHIA.AS",
    "WKL.AS", "ADYEN.AS",
    # UK (London, .L) — prices in GBp
    "SHEL.L", "AZN.L", "HSBA.L", "ULVR.L", "BP.L", "GSK.L",
    "RIO.L", "DGE.L", "BATS.L", "LSEG.L",
    # Spain (Madrid, .MC)
    "SAN.MC", "IBE.MC", "ITX.MC", "BBVA.MC", "TEF.MC", "REP.MC",
    # Italy (Milan, .MI)
    "ENEL.MI", "ENI.MI", "ISP.MI", "UCG.MI", "STLAM.MI", "RACE.MI",
    # Switzerland (SIX, .SW)
    "NESN.SW", "RO.SW", "NOVN.SW", "ZURN.SW", "ABBN.SW", "UHR.SW",
    # Belgium / Ireland / Nordics
    "ABI.BR", "CRH.L", "NOVO-B.CO", "EQNR.OL", "VOLV-B.ST",
]
