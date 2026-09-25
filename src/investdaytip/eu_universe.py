"""Default European stock universe — large-caps from major indices.

Covers EURO STOXX 50, DAX, CAC 40, FTSE 100, IBEX 35, AEX, SMI, FTSE MIB
and the largest Nordic listings. Tickers use Yahoo Finance exchange
suffixes (.DE, .PA, .L, .MC, .AS, .SW, .MI, .BR, .ST, .CO, .OL, .HE).
"""

DEFAULT_EU_UNIVERSE: list[str] = [
    # Germany (Xetra, .DE)
    "SAP.DE", "SIE.DE", "ALV.DE", "DTE.DE", "MBG.DE", "BMW.DE",
    "BAS.DE", "BAYN.DE", "MUV2.DE", "IFX.DE", "ADS.DE", "DBK.DE",
    "RHM.DE", "CON.DE", "VOW3.DE", "HNR1.DE",
    # France (Euronext Paris, .PA)
    "MC.PA", "OR.PA", "AIR.PA", "SAN.PA", "TTE.PA", "BNP.PA",
    "RMS.PA", "CS.PA", "DG.PA", "EL.PA", "KER.PA", "SU.PA",
    "AI.PA", "SAF.PA", "SGO.PA", "BN.PA", "CAP.PA", "DSY.PA",
    # Netherlands (Euronext Amsterdam, .AS)
    "ASML.AS", "PRX.AS", "INGA.AS", "AD.AS", "HEIA.AS", "PHIA.AS",
    "WKL.AS", "ADYEN.AS", "NN.AS",
    # UK (London, .L) — prices in GBp
    "SHEL.L", "AZN.L", "HSBA.L", "ULVR.L", "BP.L", "GSK.L",
    "RIO.L", "DGE.L", "BATS.L", "LSEG.L",
    "LLOY.L", "BARC.L", "NWG.L", "NG.L", "GLEN.L", "REL.L",
    "AV.L", "ANTO.L",
    # Spain (Madrid, .MC)
    "SAN.MC", "IBE.MC", "ITX.MC", "BBVA.MC", "TEF.MC", "REP.MC",
    "AENA.MC", "FER.MC",
    # Italy (Milan, .MI)
    "ENEL.MI", "ENI.MI", "ISP.MI", "UCG.MI", "STLAM.MI", "RACE.MI",
    "BPE.MI",
    # Switzerland (SIX, .SW)
    "NESN.SW", "RO.SW", "NOVN.SW", "ZURN.SW", "ABBN.SW", "UHR.SW",
    "SREN.SW", "GIVN.SW", "SLHN.SW",
    # Belgium (Euronext Brussels, .BR)
    "ABI.BR", "KBC.BR", "UCB.BR",
    # Nordics — Sweden (.ST), Denmark (.CO), Norway (.OL), Finland (.HE)
    "VOLV-B.ST", "ATCO-B.ST", "ERIC-B.ST", "SAND.ST",
    "NOVO-B.CO", "DSV.CO", "MAERSK-B.CO",
    "EQNR.OL", "DNB.OL", "YAR.OL",
    "NOKIA.HE",
]
