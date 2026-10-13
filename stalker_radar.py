import os
import json
import time
import datetime
import pandas as pd
import numpy as np
import yfinance as yf

# Global memory cache for actively stalked candidates
_STALKER_CANDIDATES = []
_LAST_STALKER_SCAN = 0

def compute_trading_range_and_exhaustion(df):
    """Computes TR High, TR Low, SuperTrend regimes and exhaustion bar count."""
    if df.empty or len(df) < 20:
        return None
        
    highs = df['High'].values.astype(float)
    lows = df['Low'].values.astype(float)
    closes = df['Close'].values.astype(float)
    vols = df['Volume'].values.astype(float)
    
    # 20-bar rolling range
    tr_high = float(np.max(highs[-20:]))
    tr_low = float(np.min(lows[-20:]))
    curr_price = float(closes[-1])
    
    # ATR & SuperTrend (9, 9.0)
    tr0 = np.abs(highs - lows)
    tr1 = np.abs(highs - np.roll(closes, 1))
    tr2 = np.abs(lows - np.roll(closes, 1))
    tr = np.maximum(tr0, np.maximum(tr1, tr2))
    tr[0] = 0
    atr = np.zeros_like(closes)
    length = 9
    if len(closes) > length:
        atr[length] = np.mean(tr[1:length+1])
        for i in range(length+1, len(closes)):
            atr[i] = (atr[i-1] * (length - 1) + tr[i]) / length
            
    hl2 = (highs + lows) / 2
    upper = hl2 + (9.0 * atr)
    lower = hl2 - (9.0 * atr)
    in_uptrend = np.ones(len(closes), dtype=bool)
    for i in range(1, len(closes)):
        if closes[i] > upper[i-1]:
            in_uptrend[i] = True
        elif closes[i] < lower[i-1]:
            in_uptrend[i] = False
        else:
            in_uptrend[i] = in_uptrend[i-1]
            if in_uptrend[i] and lower[i] < lower[i-1]:
                lower[i] = lower[i-1]
            if not in_uptrend[i] and upper[i] > upper[i-1]:
                upper[i] = upper[i-1]
                
    is_bull = bool(in_uptrend[-1])
    exhaustion_bars = 0
    for i in range(len(in_uptrend)-1, -1, -1):
        if in_uptrend[i] == is_bull:
            exhaustion_bars += 1
        else:
            break
            
    return {
        'current_price': curr_price,
        'tr_high': tr_high,
        'tr_low': tr_low,
        'is_bull': is_bull,
        'exhaustion_bars': exhaustion_bars,
        'volume': float(vols[-1])
    }

def evaluate_ticker_for_stalker(ticker, df_5m):
    """
    Deactivated: Stalker program removed to preserve clean 5M Wyckoff execution
    and prevent Yahoo Finance rate-limiting.
    """
    return None

def check_1m_micro_spark(ticker, setup_type):
    return False, "DEACTIVATED"

STALKER_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stalker_radar.json")

def get_active_stalker_radar():
    """Returns empty list: Stalker radar deactivated."""
    return []

def update_stalker_candidates(candidates_list):
    """No-op: Stalker radar deactivated."""
    global _STALKER_CANDIDATES
    _STALKER_CANDIDATES = []
    try:
        if os.path.exists(STALKER_CACHE_FILE):
            with open(STALKER_CACHE_FILE, "w") as f:
                json.dump([], f)
    except Exception:
        pass

def stalker_daemon_loop():
    """Deactivated."""
    return

_STALKER_THREAD = None

def start_stalker_daemon():
    """Deactivated: Stalker program removed in favor of core 5M Wyckoff execution."""
    print("[Stalker Radar] 1M Stalker daemon is DEACTIVATED (core 5M Wyckoff engine active).")
    return
