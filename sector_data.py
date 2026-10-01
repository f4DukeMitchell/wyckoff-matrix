import collections

SECTOR_MAP = {
    # Information Technology
    'AAPL': 'Information Technology', 'MSFT': 'Information Technology', 'NVDA': 'Information Technology', 
    'AVGO': 'Information Technology', 'ORCL': 'Information Technology', 'CRM': 'Information Technology', 
    'AMD': 'Information Technology', 'ADBE': 'Information Technology', 'CSCO': 'Information Technology', 
    'ACN': 'Information Technology', 'TXN': 'Information Technology', 'QCOM': 'Information Technology', 
    'INTC': 'Information Technology', 'PANW': 'Information Technology', 'LRCX': 'Information Technology', 
    'KLAC': 'Information Technology', 'SNPS': 'Information Technology', 'CDNS': 'Information Technology', 
    'CRWD': 'Information Technology', 'ASML': 'Information Technology', 'ARM': 'Information Technology', 
    'PLTR': 'Information Technology', 'SQ': 'Information Technology', 'SHOP': 'Information Technology', 
    'U': 'Information Technology', 'IBM': 'Information Technology', 'INTU': 'Information Technology', 
    'NOW': 'Information Technology', 'AMAT': 'Information Technology', 'MU': 'Information Technology', 
    'ADI': 'Information Technology', 'NXPI': 'Information Technology', 'MRVL': 'Information Technology', 
    'MCHP': 'Information Technology', 'FTNT': 'Information Technology', 'GIB': 'Information Technology', 
    'ANSS': 'Information Technology', 'ROP': 'Information Technology', 'PTC': 'Information Technology', 
    'TYL': 'Information Technology', 'CDW': 'Information Technology', 'HPQ': 'Information Technology', 
    'STX': 'Information Technology', 'WDC': 'Information Technology', 'NTAP': 'Information Technology',

    # Health Care
    'LLY': 'Health Care', 'UNH': 'Health Care', 'MRK': 'Health Care', 'ABBV': 'Health Care', 
    'JNJ': 'Health Care', 'TMO': 'Health Care', 'ABT': 'Health Care', 'DHR': 'Health Care', 
    'ISRG': 'Health Care', 'SYK': 'Health Care', 'GILD': 'Health Care', 'VRTX': 'Health Care', 
    'REGN': 'Health Care', 'PFE': 'Health Care', 'AMGN': 'Health Care', 'BMY': 'Health Care', 
    'CVS': 'Health Care', 'CI': 'Health Care', 'ELV': 'Health Care', 'BSX': 'Health Care', 
    'MDT': 'Health Care', 'ZTS': 'Health Care', 'COR': 'Health Care', 'HUM': 'Health Care', 
    'CNC': 'Health Care', 'MCK': 'Health Care', 'EW': 'Health Care', 'BIIB': 'Health Care', 
    'IQV': 'Health Care', 'DXCM': 'Health Care', 'IDXX': 'Health Care', 'ILMN': 'Health Care', 
    'RMD': 'Health Care', 'MTD': 'Health Care', 'ALGN': 'Health Care', 'HOLX': 'Health Care', 
    'WAT': 'Health Care', 'STE': 'Health Care', 'BIO': 'Health Care', 'CRL': 'Health Care',

    # Financials
    'BRK-B': 'Financials', 'JPM': 'Financials', 'V': 'Financials', 'MA': 'Financials', 
    'BAC': 'Financials', 'GS': 'Financials', 'BLK': 'Financials', 'WFC': 'Financials', 
    'MS': 'Financials', 'SCHW': 'Financials', 'AXP': 'Financials', 'C': 'Financials', 
    'PGR': 'Financials', 'CB': 'Financials', 'MMC': 'Financials', 'CME': 'Financials', 
    'SPGI': 'Financials', 'MCO': 'Financials', 'ICE': 'Financials', 'PYPL': 'Financials', 
    'TRV': 'Financials', 'AIG': 'Financials', 'AFL': 'Financials', 'DFS': 'Financials', 
    'COF': 'Financials', 'SYF': 'Financials', 'ALL': 'Financials', 'PRU': 'Financials', 
    'MET': 'Financials', 'TROW': 'Financials', 'BK': 'Financials', 'STT': 'Financials', 
    'NTRS': 'Financials', 'FITB': 'Financials', 'MTB': 'Financials', 'HBAN': 'Financials', 
    'RF': 'Financials', 'KEY': 'Financials', 'CFG': 'Financials', 'CMA': 'Financials',
    'COIN': 'Financials', 'SOFI': 'Financials', 'HOOD': 'Financials', 'MARA': 'Financials', 
    'RIOT': 'Financials',

    # Consumer Discretionary
    'AMZN': 'Consumer Discretionary', 'TSLA': 'Consumer Discretionary', 'HD': 'Consumer Discretionary', 
    'MCD': 'Consumer Discretionary', 'NKE': 'Consumer Discretionary', 'SBUX': 'Consumer Discretionary', 
    'LOW': 'Consumer Discretionary', 'BKNG': 'Consumer Discretionary', 'MAR': 'Consumer Discretionary', 
    'HLT': 'Consumer Discretionary', 'CMG': 'Consumer Discretionary', 'TJX': 'Consumer Discretionary', 
    'YUM': 'Consumer Discretionary', 'ROST': 'Consumer Discretionary', 'ORLY': 'Consumer Discretionary', 
    'AZO': 'Consumer Discretionary', 'TSCO': 'Consumer Discretionary', 'F': 'Consumer Discretionary', 
    'GM': 'Consumer Discretionary', 'DHI': 'Consumer Discretionary', 'LEN': 'Consumer Discretionary', 
    'NVR': 'Consumer Discretionary', 'PHM': 'Consumer Discretionary', 'LVS': 'Consumer Discretionary', 
    'RCL': 'Consumer Discretionary', 'CCL': 'Consumer Discretionary', 'KMX': 'Consumer Discretionary', 
    'EXPE': 'Consumer Discretionary', 'EBAY': 'Consumer Discretionary', 'ETSY': 'Consumer Discretionary', 
    'HAS': 'Consumer Discretionary', 'MAT': 'Consumer Discretionary', 'WHR': 'Consumer Discretionary', 
    'ABNB': 'Consumer Discretionary', 'RIVN': 'Consumer Discretionary', 'LCID': 'Consumer Discretionary', 
    'DASH': 'Consumer Discretionary', 'MELI': 'Consumer Discretionary', 'DKNG': 'Consumer Discretionary',

    # Communication Services
    'META': 'Communication Services', 'GOOGL': 'Communication Services', 'GOOG': 'Communication Services', 
    'NFLX': 'Communication Services', 'CMCSA': 'Communication Services', 'DIS': 'Communication Services', 
    'TMUS': 'Communication Services', 'VZ': 'Communication Services', 'T': 'Communication Services', 
    'CHTR': 'Communication Services', 'WBD': 'Communication Services', 'FOXA': 'Communication Services', 
    'FOX': 'Communication Services', 'NWSA': 'Communication Services', 'NWS': 'Communication Services', 
    'LYV': 'Communication Services', 'TTWO': 'Communication Services', 'EA': 'Communication Services', 
    'MTCH': 'Communication Services', 'PARA': 'Communication Services', 'OMC': 'Communication Services', 
    'IPG': 'Communication Services', 'SNAP': 'Communication Services', 'ROKU': 'Communication Services', 
    'RBLX': 'Communication Services',

    # Industrials
    'HON': 'Industrials', 'UNP': 'Industrials', 'RTX': 'Industrials', 'BA': 'Industrials', 
    'LMT': 'Industrials', 'CAT': 'Industrials', 'GE': 'Industrials', 'DE': 'Industrials', 
    'UPS': 'Industrials', 'FDX': 'Industrials', 'ADP': 'Industrials', 'CSX': 'Industrials', 
    'NSC': 'Industrials', 'WM': 'Industrials', 'RSG': 'Industrials', 'ETN': 'Industrials', 
    'EMR': 'Industrials', 'ITW': 'Industrials', 'PH': 'Industrials', 'CMI': 'Industrials', 
    'PNR': 'Industrials', 'ROK': 'Industrials', 'GWW': 'Industrials', 'URI': 'Industrials', 
    'FAST': 'Industrials', 'CPRT': 'Industrials', 'PAYX': 'Industrials', 'CTAS': 'Industrials', 
    'INFO': 'Industrials', 'EFX': 'Industrials', 'VSK': 'Industrials', 'TRMB': 'Industrials', 
    'JCI': 'Industrials', 'CARR': 'Industrials', 'TT': 'Industrials', 'OTIS': 'Industrials', 
    'DAL': 'Industrials', 'UAL': 'Industrials', 'LUV': 'Industrials',

    # Consumer Staples
    'PG': 'Consumer Staples', 'COST': 'Consumer Staples', 'WMT': 'Consumer Staples', 
    'PEP': 'Consumer Staples', 'KO': 'Consumer Staples', 'MDLZ': 'Consumer Staples', 
    'PM': 'Consumer Staples', 'MO': 'Consumer Staples', 'TGT': 'Consumer Staples', 
    'KHC': 'Consumer Staples', 'HSY': 'Consumer Staples', 'K': 'Consumer Staples', 
    'GIS': 'Consumer Staples', 'CPB': 'Consumer Staples', 'SJM': 'Consumer Staples', 
    'CAG': 'Consumer Staples', 'MKC': 'Consumer Staples', 'CHD': 'Consumer Staples', 
    'CL': 'Consumer Staples', 'CLX': 'Consumer Staples', 'EL': 'Consumer Staples', 
    'STZ': 'Consumer Staples', 'TAP': 'Consumer Staples', 'ADM': 'Consumer Staples', 
    'BG': 'Consumer Staples', 'TSN': 'Consumer Staples', 'SYY': 'Consumer Staples', 
    'KR': 'Consumer Staples',

    # Energy
    'CVX': 'Energy', 'XOM': 'Energy', 'COP': 'Energy', 'EOG': 'Energy', 
    'SLB': 'Energy', 'PXD': 'Energy', 'MPC': 'Energy', 'PSX': 'Energy', 
    'VLO': 'Energy', 'OXY': 'Energy', 'HES': 'Energy', 'BKR': 'Energy', 
    'HAL': 'Energy', 'WMB': 'Energy', 'KMI': 'Energy', 'TRGP': 'Energy', 
    'OKE': 'Energy', 'DVN': 'Energy', 'FANG': 'Energy', 'CTRA': 'Energy', 
    'MRO': 'Energy', 'APA': 'Energy', 'EQT': 'Energy',

    # Utilities
    'NEE': 'Utilities', 'DUK': 'Utilities', 'SO': 'Utilities', 'SRE': 'Utilities', 
    'AEP': 'Utilities', 'D': 'Utilities', 'EXC': 'Utilities', 'XEL': 'Utilities', 
    'ED': 'Utilities', 'WEC': 'Utilities', 'ES': 'Utilities', 'PEG': 'Utilities', 
    'AWK': 'Utilities', 'ETR': 'Utilities', 'CMS': 'Utilities', 'LNT': 'Utilities', 
    'NI': 'Utilities', 'CNP': 'Utilities', 'ATO': 'Utilities', 'PPL': 'Utilities', 
    'FE': 'Utilities', 'EVRG': 'Utilities',

    # Real Estate
    'AMT': 'Real Estate', 'PLD': 'Real Estate', 'CCI': 'Real Estate', 'EQIX': 'Real Estate', 
    'PSA': 'Real Estate', 'SPG': 'Real Estate', 'O': 'Real Estate', 'WELL': 'Real Estate', 
    'VTR': 'Real Estate', 'DLR': 'Real Estate', 'AVB': 'Real Estate', 'EQR': 'Real Estate', 
    'ESS': 'Real Estate', 'MAA': 'Real Estate', 'CPT': 'Real Estate', 'UDR': 'Real Estate', 
    'INVH': 'Real Estate', 'AMH': 'Real Estate', 'ARE': 'Real Estate', 'BXP': 'Real Estate', 
    'KRC': 'Real Estate', 'HPP': 'Real Estate', 'SLG': 'Real Estate', 'VNO': 'Real Estate', 
    'CBRE': 'Real Estate',

    # Materials
    'LIN': 'Materials', 'SHW': 'Materials', 'APD': 'Materials', 'ECL': 'Materials', 
    'NEM': 'Materials', 'FCX': 'Materials', 'NUE': 'Materials', 'STLD': 'Materials', 
    'CTVA': 'Materials', 'DOW': 'Materials', 'DD': 'Materials', 'LYB': 'Materials', 
    'CE': 'Materials', 'EMN': 'Materials', 'HUN': 'Materials', 'FMC': 'Materials', 
    'ALB': 'Materials', 'SQM': 'Materials', 'VMC': 'Materials', 'MLM': 'Materials', 
    'CF': 'Materials', 'MOS': 'Materials'
}

def get_sector(ticker: str) -> str:
    """Returns the sector string for a given ticker. Falls back to 'Other'."""
    return SECTOR_MAP.get(ticker.upper(), 'Other')

def get_tickers_by_sector(sector: str) -> list[str]:
    """Returns a list of all tickers in that sector."""
    return [ticker for ticker, sec in SECTOR_MAP.items() if sec.lower() == sector.lower()]

def get_all_sectors() -> list[str]:
    """Returns sorted list of unique sector names."""
    sectors = set(SECTOR_MAP.values())
    return sorted(list(sectors))

def get_sector_summary(results_list: list) -> dict:
    """
    Takes a list of dicts (each with 'ticker', 'regime', 'exhaustion_bars' keys) 
    and returns a dict keyed by sector with: count of stocks, avg exhaustion, 
    dominant regime (BULL/BEAR/MIXED), and list of top 3 most exhausted tickers in that sector.
    """
    try:
        sector_data = collections.defaultdict(list)
        
        # Group stocks by sector
        for result in results_list:
            ticker = result.get('ticker')
            if not ticker:
                continue
            
            sector = get_sector(ticker)
            sector_data[sector].append(result)
            
        summary = {}
        for sector, stocks in sector_data.items():
            count = len(stocks)
            if count == 0:
                continue
                
            total_exhaustion = sum(s.get('exhaustion_bars', 0) for s in stocks)
            avg_exhaustion = total_exhaustion / count
            
            # Find dominant regime
            regimes = [s.get('regime', 'UNKNOWN') for s in stocks]
            regime_counts = collections.Counter(regimes)
            
            most_common_regime = regime_counts.most_common(1)[0][0]
            if len(regime_counts) > 1 and regime_counts.most_common(2)[0][1] == regime_counts.most_common(2)[1][1]:
                dominant_regime = 'MIXED'
            else:
                dominant_regime = most_common_regime
                
            # Top 3 most exhausted
            sorted_by_exhaustion = sorted(stocks, key=lambda x: x.get('exhaustion_bars', 0), reverse=True)
            top_3_exhausted = [s.get('ticker') for s in sorted_by_exhaustion[:3]]
            
            summary[sector] = {
                'count': count,
                'avg_exhaustion': avg_exhaustion,
                'dominant_regime': dominant_regime,
                'top_3_exhausted': top_3_exhausted
            }
            
        return summary
    except Exception as e:
        print(f"Error computing sector summary: {e}")
        return {}
