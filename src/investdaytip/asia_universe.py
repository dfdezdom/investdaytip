"""Default universe of large-cap, liquid Asian stocks across regions and sectors.

Used when the user requests the Asia region. Curated to give the multi-factor
model a diverse pool for long-term candidates across major Asian exchanges.
"""

ASIA_UNIVERSE: list[str] = [
    # Japan (Tokyo Exchange .T)
    "7203.T",  # Toyota Motor
    "6758.T",  # Sony Group
    "9984.T",  # SoftBank Group
    "7267.T",  # Honda Motor
    "8058.T",  # Mitsubishi UFJ Financial
    "8306.T",  # Sumitomo Mitsui Financial
    "6861.T",  # Keyence
    "6701.T",  # NEC
    "4503.T",  # Astellas Pharma
    "6762.T",  # TDK
    "8802.T",  # Mitsubishi Estate
    "4502.T",  # Takeda Pharmaceutical
    "8031.T",  # Mitsui Fudosan
    "8725.T",  # Tokyu Land
    "9432.T",  # Nippon Telegraph & Telephone
    
    # Hong Kong (.HK)
    "0001.HK",  # CKH Holdings
    "3690.HK",  # Meituan
    "0700.HK",  # Tencent Holdings
    "0941.HK",  # China Mobile
    "0762.HK",  # China Unicom
    "0386.HK",  # China Coal Energy
    "0883.HK",  # CNOOC
    "1299.HK",  # AIA Group
    "2318.HK",  # Ping An Insurance
    "1288.HK",  # Agricultural Bank of China
    "9618.HK",  # JD.com
    "9901.HK",  # New Oriental Education
    "0388.HK",  # HKEX
    
    # Singapore (.SI)
    "D05.SI",  # DBS Group Holdings
    "O39.SI",  # OCBC Bank
    "U11.SI",  # United Overseas Bank
    "BN4.SI",  # Keppel Corporation
    "S63.SI",  # ST Engineering
    "Z74.SI",  # Singtel
    "BS6.SI",  # Genting Singapore
    "M44U.SI",  # Mapletree Logistics Trust
    
    # India (NSE .NS and BSE .BO)
    "RELIANCE.NS",  # Reliance Industries
    "TCS.NS",  # Tata Consultancy Services
    "HDFCBANK.NS",  # HDFC Bank
    "INFY.NS",  # Infosys
    "ITC.NS",  # ITC
    "WIPRO.NS",  # Wipro
    "MARUTI.NS",  # Maruti Suzuki
    "BAJAJFINSV.NS",  # Bajaj Finserv
    "HCLTECH.NS",  # HCL Technologies
    "ICICIBANK.NS",  # ICICI Bank
    "NESTLEIND.NS",  # Nestlé India
    "SUNPHARMA.NS",  # Sun Pharmaceutical
    
    # South Korea (.KS)
    "005930.KS",  # Samsung Electronics
    "000660.KS",  # SK Hynix
    "051910.KS",  # LG Chem
    "005380.KS",  # Hyundai Motor
    "012330.KS",  # Hyundai Mobis
    "066570.KS",  # LG Electronics
    "035720.KS",  # Kakao Corp
    "088980.KS",  # Celltrion
    "207940.KS",  # SamsungBio
    "316140.KS",  # Woori Financial Group
    
    # Taiwan (.TW)
    "2330.TW",  # Taiwan Semiconductor Manufacturing Company
    "2454.TW",  # MediaTek
    "2412.TW",  # Chunghwa Telecom
    "1101.TW",  # Taiwan Cement
    "2357.TW",  # Acer
    "2892.TW",  # First Financial Holding
    "3711.TW",  # ASE Technology Holding
    "1605.TW",  # TSRC Corporation
    
    # Australia (.AX)
    "CBA.AX",  # Commonwealth Bank of Australia
    "WBC.AX",  # Westpac Banking Corporation
    "ANZ.AX",  # Australia and New Zealand Banking Group
    "NAB.AX",  # National Australia Bank
    "BHP.AX",  # BHP Group
    "RIO.AX",  # Rio Tinto
    "CPU.AX",  # Computershare
    "CSL.AX",  # CSL
    "IAG.AX",  # Insurance Australia Group
    "MQG.AX",  # Macquarie Group
]
