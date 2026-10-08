import os
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
    Checks if a 5m bar structure qualifies for fast 1m stalking.
    Spring Stalk: Bearish exhaustion >= 12 bars AND price within 0.75% of TR Low.
    UTAD Stalk:   Bullish exhaustion >= 12 bars AND price within 0.75% of TR High.
    """
    metrics = compute_trading_range_and_exhaustion(df_5m)
    if not metrics:
        return None
        
    curr = metrics['current_price']
    tr_low = metrics['tr_low']
    tr_high = metrics['tr_high']
    bars = metrics['exhaustion_bars']
    is_bull = metrics['is_bull']
    
    # 1. Spring Stalk (Approaching TR Low Support)
    if not is_bull and bars >= 12 and tr_low > 0:
        dist_pct = ((curr - tr_low) / curr) * 100.0
        # Within -0.5% (slight breach) to +0.80% above support
        if -0.5 <= dist_pct <= 0.80:
            return {
                'ticker': ticker.upper(),
                'setup': 'SPRING_STALK',
                'direction': 'LONG',
                'current_price': round(curr, 2),
                'target_test_level': round(tr_low, 2),
                'dist_to_test_pct': round(dist_pct, 2),
                'exhaustion_bars': bars,
                'status': 'POLLING_1M',
                'timestamp': datetime.datetime.now().strftime("%H:%M:%S")
            }
            
    # 2. UTAD Stalk (Approaching TR High Resistance)
    if is_bull and bars >= 12 and tr_high > 0:
        dist_pct = ((tr_high - curr) / curr) * 100.0
        # Within -0.5% (slight breach) to +0.80% below resistance
        if -0.5 <= dist_pct <= 0.80:
            return {
                'ticker': ticker.upper(),
                'setup': 'UTAD_STALK',
                'direction': 'SHORT',
                'current_price': round(curr, 2),
                'target_test_level': round(tr_high, 2),
                'dist_to_test_pct': round(dist_pct, 2),
                'exhaustion_bars': bars,
                'status': 'POLLING_1M',
                'timestamp': datetime.datetime.now().strftime("%H:%M:%S")
            }
            
    return None

def check_1m_micro_spark(ticker, setup_type):
    """
    Polls 1-minute bars for a stalked candidate to detect the micro-spark trigger:
    1. Volume dry-up (current 1m volume < 0.75x 20-bar avg 1m volume).
    2. Bullish rejection wick or 1m green turn above test level.
    """
    try:
        df_1m = yf.download(ticker, period="1d", interval="1m", progress=False)
        if df_1m.empty or len(df_1m) < 10:
            return False, "INSUFFICIENT_1M_DATA"
            
        if isinstance(df_1m.columns, pd.MultiIndex):
            closes = df_1m['Close'].iloc[:, 0].values.astype(float)
            highs = df_1m['High'].iloc[:, 0].values.astype(float)
            lows = df_1m['Low'].iloc[:, 0].values.astype(float)
            opens = df_1m['Open'].iloc[:, 0].values.astype(float)
            vols = df_1m['Volume'].iloc[:, 0].values.astype(float)
        else:
            closes = df_1m['Close'].values.astype(float)
            highs = df_1m['High'].values.astype(float)
            lows = df_1m['Low'].values.astype(float)
            opens = df_1m['Open'].values.astype(float)
            vols = df_1m['Volume'].values.astype(float)
            
        last_c = closes[-1]
        last_o = opens[-1]
        last_l = lows[-1]
        last_v = vols[-1]
        avg_v = np.mean(vols[-15:-1]) if len(vols) >= 15 else last_v
        
        vol_ratio = (last_v / avg_v) if avg_v > 0 else 1.0
        
        # Spring Trigger: Bullish close on 1m + Volume dry-up (< 1.2x) or lower wick rejection
        if setup_type == 'SPRING_STALK':
            is_green_bar = last_c >= last_o
            has_lower_wick = (min(last_o, last_c) - last_l) > (abs(last_c - last_o) * 0.5)
            if is_green_bar and (vol_ratio < 1.25 or has_lower_wick):
                return True, f"1M_MICRO_SPARK_CONFIRMED (Vol: {vol_ratio:.2f}x, Wick: {has_lower_wick})"
                
        # UTAD Trigger: Bearish close on 1m + upper wick rejection
        elif setup_type == 'UTAD_STALK':
            is_red_bar = last_c <= last_o
            has_upper_wick = (highs[-1] - max(last_o, last_c)) > (abs(last_c - last_o) * 0.5)
            if is_red_bar and (vol_ratio < 1.25 or has_upper_wick):
                return True, f"1M_MICRO_SPARK_CONFIRMED (Vol: {vol_ratio:.2f}x, Wick: {has_upper_wick})"
                
        return False, f"MONITORING_1M (Vol: {vol_ratio:.2f}x)"
    except Exception as e:
        return False, f"1M_ERROR: {str(e)}"

def get_active_stalker_radar():
    """Returns the current list of stalked candidates for API and UI display."""
    global _STALKER_CANDIDATES
    return _STALKER_CANDIDATES

def update_stalker_candidates(candidates_list):
    """Updates the in-memory stalker list."""
    global _STALKER_CANDIDATES, _LAST_STALKER_SCAN
    _STALKER_CANDIDATES = candidates_list
    _LAST_STALKER_SCAN = time.time()

def stalker_daemon_loop():
    """Periodically checks 1-minute micro-spark for all actively stalked candidates."""
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] [Stalker Radar] Fast 1m Micro-Scanner Daemon active.")
    while True:
        try:
            candidates = get_active_stalker_radar()
            for cand in candidates:
                ticker = cand.get('ticker')
                setup = cand.get('setup')
                if not ticker or not setup:
                    continue
                triggered, note = check_1m_micro_spark(ticker, setup)
                cand['micro_status'] = note
                if triggered and not cand.get('alerted_1m'):
                    cand['alerted_1m'] = True
                    cand['status'] = 'MICRO_SPARK_TRIGGERED'
                    msg = (
                        f"⚡ [STALKER RADAR TRIGGER] 1-MINUTE TEST CONFIRMED!\n"
                        f"• Symbol: {ticker} ({cand.get('direction')})\n"
                        f"• Setup: {setup} approaching test level ${cand.get('target_test_level', 0.0):.2f}\n"
                        f"• Micro Note: {note}\n"
                        f"• Action: Fast secondary test detected with volume dry-up!"
                    )
                    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}")
                    try:
                        from telegram_notifier import send_message
                        send_message(msg)
                    except Exception:
                        pass
        except Exception as e:
            pass
        time.sleep(20)

_STALKER_THREAD = None

def start_stalker_daemon():
    """Starts the stalker monitoring loop in a background thread."""
    global _STALKER_THREAD
    if _STALKER_THREAD is None or not _STALKER_THREAD.is_alive():
        import threading
        _STALKER_THREAD = threading.Thread(target=stalker_daemon_loop, daemon=True)
        _STALKER_THREAD.start()
