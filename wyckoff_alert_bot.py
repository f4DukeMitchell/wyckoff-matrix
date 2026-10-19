import time
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
import datetime
import smtplib
import yfinance as yf
import pandas as pd
import numpy as np
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import subprocess
import os
try:
    import zoneinfo
    ET_TZ = zoneinfo.ZoneInfo("America/New_York")
except Exception:
    ET_TZ = None

def get_market_now():
    """Returns current datetime in America/New_York (EST/EDT) market time."""
    if ET_TZ:
        return datetime.datetime.now(ET_TZ)
    return datetime.datetime.now()


# --- UPGRADE MODULES ---
try:
    from trade_tracker import (log_alert, check_open_trades, has_open_alerted_trade,
                               has_open_trade, mark_trade_alerted, sync_public_positions,
                               calculate_ml_confidence)
    TRACKER_ENABLED = True
except:
    TRACKER_ENABLED = False

try:
    from telegram_notifier import is_configured as tg_configured, send_trade_alert as tg_trade_alert, send_daily_recap as tg_recap, send_market_radar as tg_radar
    TELEGRAM_ENABLED = tg_configured()
except:
    TELEGRAM_ENABLED = False

try:
    from options_flow import get_options_flow, get_public_quotes
    OPTIONS_ENABLED = True
except:
    OPTIONS_ENABLED = False

try:
    from institutional_engine import compute_all_institutional_features
    INST_ENGINE_ENABLED = True
except:
    INST_ENGINE_ENABLED = False

try:
    from data_feed_public import stream_ticker_bars, get_spy_trend_public, get_public_bars_sync
    PUBLIC_DATA_ENABLED = True
except Exception as e:
    PUBLIC_DATA_ENABLED = False

try:
    from wyckoff_ml_engine import calculate_optimal_target_r
    ML_TARGET_OPT_ENABLED = True
except:
    ML_TARGET_OPT_ENABLED = False

# ==========================================
# USER CONFIGURATION
# ==========================================
GMAIL_USER = "f4dukemitchell@gmail.com"
GMAIL_APP_PASSWORD = "aakv dgpp wfwx nhua"
DESTINATION_EMAIL = "matt.smith@pga.com"

import json

try:
    with open("all_tickers.json", "r") as f:
        TICKERS = json.load(f)
except:
    TICKERS = ["AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "BRK-B", "LLY", "AVGO", "JPM", "V"]

TIMEFRAMES = [
    {"interval": "5m", "period": "5d", "lookback": 200},
    {"interval": "15m", "period": "20d", "lookback": 80},
    {"interval": "1h", "period": "60d", "lookback": 100},
    {"interval": "1d", "period": "2y", "lookback": 100}
]
SL_BUFFER = 0.01
MIN_R_UNITS = 0.55  # Dynamic Sweet-Spot Floor: Captures valid 0.55R+ setups across expanded ticker universe
VOL_LIMIT = 1.2

last_alerted = {ticker: 0 for ticker in TICKERS}

def send_email_alert(ticker, action, price, sl, tp, regime, options_flow=None, interval="5m"):
    subject = f"WYCKOFF ALERT: {action} on {ticker} ({interval})"
    body = f"""
    Wyckoff Institutional Terminal Alert
    ------------------------------------
    TICKER: {ticker} ({interval})
    ACTION: {action}
    
    ENTRY PRICE: ${price:.2f}
    STOP LOSS:   ${sl:.2f}
    TAKE PROFIT: ${tp:.2f}
    
    REGIME: {regime}
    TIME: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    """
    
    if options_flow:
        body += f"""
    OPTIONS FLOW INTEL
    ------------------------------------
    Sentiment: {options_flow.get('net_sentiment', 'N/A')}
    Put/Call Ratio: {options_flow.get('put_call_ratio', 'N/A')} ({options_flow.get('put_call_label', 'N/A')})
    Gamma Wall (Magnet Target): ${options_flow.get('gamma_wall', 'N/A')}
    Max Pain: ${options_flow.get('max_pain', 'N/A')}
    """

    body += """
    *Stalk the entry. Manage your risk.*
    """
    
    msg = MIMEMultipart()
    msg['From'] = GMAIL_USER
    msg['To'] = DESTINATION_EMAIL
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain', 'utf-8'))
    
    try:
        # server = smtplib.SMTP('smtp.gmail.com', 587)
        # server.starttls()()
        # server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        # server.sendmail(GMAIL_USER, DESTINATION_EMAIL, msg.as_string())
        # server.quit()
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] EMAIL SENT: {action} on {ticker}")
    except Exception as e:
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ERROR SENDING EMAIL: {e}")

def get_supertrend(high, low, close, length, multiplier):
    tr0 = np.abs(high - low)
    tr1 = np.abs(high - np.roll(close, 1))
    tr2 = np.abs(low - np.roll(close, 1))
    tr = np.maximum(tr0, np.maximum(tr1, tr2))
    tr[0] = 0
    atr = np.zeros_like(close)
    if len(close) > length:
        atr[length] = np.mean(tr[1:length+1])
        for i in range(length+1, len(close)):
            atr[i] = (atr[i-1] * (length - 1) + tr[i]) / length
    hl2 = (high + low) / 2
    upperband = hl2 + (multiplier * atr)
    lowerband = hl2 - (multiplier * atr)
    in_uptrend = np.ones(len(close), dtype=bool)
    for i in range(1, len(close)):
        if close[i] > upperband[i-1]: in_uptrend[i] = True
        elif close[i] < lowerband[i-1]: in_uptrend[i] = False
        else:
            in_uptrend[i] = in_uptrend[i-1]
            if in_uptrend[i] and lowerband[i] < lowerband[i-1]: lowerband[i] = lowerband[i-1]
            if not in_uptrend[i] and upperband[i] > upperband[i-1]: upperband[i] = upperband[i-1]
    return in_uptrend

# --- INSTITUTIONAL FEATURE CALCULATIONS ---
def calc_vwap(highs, lows, closes, volumes):
    """Volume Weighted Average Price"""
    typical_price = (highs + lows + closes) / 3.0
    cum_tp_vol = np.cumsum(typical_price * volumes)
    cum_vol = np.cumsum(volumes)
    with np.errstate(divide='ignore', invalid='ignore'):
        vwap = np.where(cum_vol > 0, cum_tp_vol / cum_vol, closes)
    return vwap

def calc_atr_expansion(highs, lows, closes, period=14):
    """ATR divided by its own 20-period SMA = volatility expansion ratio"""
    tr0 = np.abs(highs - lows)
    tr1 = np.abs(highs - np.roll(closes, 1))
    tr2 = np.abs(lows - np.roll(closes, 1))
    tr = np.maximum(tr0, np.maximum(tr1, tr2))
    tr[0] = 0
    atr = pd.Series(tr).rolling(period, min_periods=1).mean().values
    atr_sma = pd.Series(atr).rolling(20, min_periods=1).mean().values
    with np.errstate(divide='ignore', invalid='ignore'):
        expansion = np.where(atr_sma > 0, atr / atr_sma, 1.0)
    return expansion

_MACRO_CACHE = {
    'last_time': 0,
    'spy_bullish': True,
    'spy_30m_ret': 0.0,
    'vix_slope': 0.0
}

def get_macro_context():
    """Fetches and caches SPY 30m return, VIX/VIXY 30m slope, and SPY macro trend (2-min TTL) via Public.com API primary."""
    global _MACRO_CACHE
    now_ts = time.time()
    if now_ts - _MACRO_CACHE['last_time'] < 120 and _MACRO_CACHE['last_time'] > 0:
        return _MACRO_CACHE['spy_bullish'], _MACRO_CACHE['spy_30m_ret'], _MACRO_CACHE['vix_slope']
    
    spy_ret = 0.0
    vix_ret = 0.0
    spy_bullish = True
    
    try:
        spy = get_public_bars_sync("SPY", "5m") if PUBLIC_DATA_ENABLED else pd.DataFrame()
        if spy.empty:
            spy = yf.download("SPY", period="5d", interval="5m", progress=False)
        if not spy.empty and len(spy) >= 7:
            if isinstance(spy.columns, pd.MultiIndex):
                s_c = spy['Close'].iloc[:, 0].values.astype(float)
            else:
                s_c = spy['Close'].values.astype(float)
            spy_ret = float((s_c[-1] - s_c[-7]) / s_c[-7] * 100.0) if s_c[-7] > 0 else 0.0
    except Exception:
        pass

    try:
        vix = get_public_bars_sync("VIXY", "5m") if PUBLIC_DATA_ENABLED else pd.DataFrame()
        if vix.empty:
            vix = yf.download("^VIX", period="5d", interval="5m", progress=False)
        if not vix.empty and len(vix) >= 7:
            if isinstance(vix.columns, pd.MultiIndex):
                v_c = vix['Close'].iloc[:, 0].values.astype(float)
            else:
                v_c = vix['Close'].values.astype(float)
            vix_ret = float((v_c[-1] - v_c[-7]) / v_c[-7] * 100.0) if v_c[-7] > 0 else 0.0
    except Exception:
        pass

    try:
        spy_bullish = get_spy_trend()
    except Exception:
        spy_bullish = True

    _MACRO_CACHE['last_time'] = now_ts
    _MACRO_CACHE['spy_bullish'] = spy_bullish
    _MACRO_CACHE['spy_30m_ret'] = spy_ret
    _MACRO_CACHE['vix_slope'] = vix_ret
    return spy_bullish, spy_ret, vix_ret

def get_spy_trend():
    """Get SPY macro trend using 9-period SuperTrend via Public.com API primary."""
    try:
        spy = get_public_bars_sync("SPY", "1h") if PUBLIC_DATA_ENABLED else pd.DataFrame()
        if spy.empty:
            spy = yf.download("SPY", period="60d", interval="1h", progress=False)
        if spy.empty: return True  # default bullish
        highs = spy['High'].values.flatten().astype(float)
        lows = spy['Low'].values.flatten().astype(float)
        closes = spy['Close'].values.flatten().astype(float)
        u9 = get_supertrend(highs, lows, closes, 9, 9.0)
        return bool(u9[-1])  # True = SPY bullish
    except:
        return True

# --- SECTOR ETF MAPPING & CONTEXT ---
SECTOR_MAP = {
    # Tech (XLK)
    'AAPL': 'XLK', 'MSFT': 'XLK', 'NVDA': 'XLK', 'AMD': 'XLK', 'AVGO': 'XLK',
    'CRM': 'XLK', 'ADBE': 'XLK', 'INTC': 'XLK', 'CSCO': 'XLK', 'QCOM': 'XLK',
    'AMAT': 'XLK', 'MU': 'XLK', 'LRCX': 'XLK', 'NOW': 'XLK', 'PANW': 'XLK',
    'SNPS': 'XLK', 'CDNS': 'XLK', 'KLAC': 'XLK', 'ANET': 'XLK', 'CRWD': 'XLK',
    'PLTR': 'XLK', 'DELL': 'XLK', 'SMCI': 'XLK',
    # Comm Services (XLC)
    'GOOGL': 'XLC', 'GOOG': 'XLC', 'META': 'XLC', 'NFLX': 'XLC', 'DIS': 'XLC',
    'CMCSA': 'XLC', 'TMUS': 'XLC', 'CHTR': 'XLC', 'VZ': 'XLC', 'T': 'XLC',
    # Consumer Discretionary (XLY)
    'AMZN': 'XLY', 'TSLA': 'XLY', 'HD': 'XLY', 'MCD': 'XLY', 'NKE': 'XLY',
    'SBUX': 'XLY', 'BKNG': 'XLY', 'LOW': 'XLY', 'TJX': 'XLY', 'TGT': 'XLY',
    'LULU': 'XLY', 'ROST': 'XLY', 'ABNB': 'XLY', 'MAR': 'XLY', 'ORLY': 'XLY',
    # Financials (XLF)
    'JPM': 'XLF', 'BAC': 'XLF', 'WFC': 'XLF', 'C': 'XLF', 'GS': 'XLF',
    'MS': 'XLF', 'BLK': 'XLF', 'SCHW': 'XLF', 'V': 'XLF', 'MA': 'XLF',
    'AXP': 'XLF', 'PGR': 'XLF', 'CB': 'XLF', 'MMC': 'XLF', 'AON': 'XLF',
    # Healthcare (XLV)
    'UNH': 'XLV', 'JNJ': 'XLV', 'LLY': 'XLV', 'ABBV': 'XLV', 'MRK': 'XLV',
    'PFE': 'XLV', 'TMO': 'XLV', 'ABT': 'XLV', 'DHR': 'XLV', 'BMY': 'XLV',
    'AMGN': 'XLV', 'ISRG': 'XLV', 'VRTX': 'XLV', 'GILD': 'XLV', 'REGN': 'XLV',
    # Industrials (XLI)
    'CAT': 'XLI', 'GE': 'XLI', 'UNP': 'XLI', 'HON': 'XLI', 'BA': 'XLI',
    'UPS': 'XLI', 'RTX': 'XLI', 'DE': 'XLI', 'LMT': 'XLI', 'ETN': 'XLI',
    # Energy (XLE)
    'XOM': 'XLE', 'CVX': 'XLE', 'COP': 'XLE', 'SLB': 'XLE', 'EOG': 'XLE',
    'MPC': 'XLE', 'PSX': 'XLE', 'VLO': 'XLE', 'OXY': 'XLE', 'WMB': 'XLE',
    # Consumer Staples (XLP)
    'PG': 'XLP', 'COST': 'XLP', 'PEP': 'XLP', 'KO': 'XLP', 'WMT': 'XLP',
    # Materials (XLB)
    'LIN': 'XLB', 'APD': 'XLB', 'SHW': 'XLB', 'FCX': 'XLB', 'NEM': 'XLB',
    # Utilities (XLU)
    'NEE': 'XLU', 'SO': 'XLU', 'DUK': 'XLU', 'CEG': 'XLU', 'SRE': 'XLU',
    # Real Estate (XLRE)
    'PLD': 'XLRE', 'AMT': 'XLRE', 'EQIX': 'XLRE', 'CCI': 'XLRE', 'SPG': 'XLRE'
}

_SECTOR_CACHE = {}

def get_sector_return(ticker):
    """Calculates and caches the intraday return % of the ticker's SPDR sector ETF (2-min TTL) via Public.com API primary."""
    global _SECTOR_CACHE
    sec_etf = SECTOR_MAP.get(ticker, 'SPY')
    now_ts = time.time()
    if sec_etf in _SECTOR_CACHE and (now_ts - _SECTOR_CACHE[sec_etf]['time'] < 120):
        return _SECTOR_CACHE[sec_etf]['ret']
        
    try:
        etf_data = get_public_bars_sync(sec_etf, "5m") if PUBLIC_DATA_ENABLED else pd.DataFrame()
        if etf_data.empty:
            etf_data = yf.download(sec_etf, period="1d", interval="5m", progress=False)
        if not etf_data.empty and len(etf_data) >= 2:
            if isinstance(etf_data.columns, pd.MultiIndex):
                c_vals = etf_data['Close'].iloc[:, 0].values.astype(float)
                o_vals = etf_data['Open'].iloc[:, 0].values.astype(float)
            else:
                c_vals = etf_data['Close'].values.astype(float)
                o_vals = etf_data['Open'].values.astype(float)
            last_date = etf_data.index[-1].date() if hasattr(etf_data.index[-1], 'date') else None
            if last_date is not None:
                day_indices = [i for i, ts in enumerate(etf_data.index) if hasattr(ts, 'date') and ts.date() == last_date and (not hasattr(ts, 'hour') or ts.hour >= 9)]
                day_open = o_vals[day_indices[0]] if day_indices else o_vals[0]
            else:
                day_open = o_vals[0]
            curr_c = c_vals[-1]
            ret_pct = float((curr_c - day_open) / day_open * 100.0) if day_open > 0 else 0.0
            _SECTOR_CACHE[sec_etf] = {'ret': ret_pct, 'time': now_ts}
            return ret_pct
    except Exception:
        pass
    return 0.0

# --- TELEGRAM COMMAND LISTENER ---
LAST_UPDATE_ID = 0

def check_telegram_commands():
    global LAST_UPDATE_ID
    if not TELEGRAM_ENABLED: return
    try:
        import requests
        from telegram_notifier import TELEGRAM_BOT_TOKEN, send_message
        if not TELEGRAM_BOT_TOKEN: return
        
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        params = {"offset": LAST_UPDATE_ID + 1, "timeout": 1}
        r = requests.get(url, params=params, timeout=2)
        data = r.json()
        
        if data.get("ok") and data.get("result"):
            for update in data["result"]:
                LAST_UPDATE_ID = update["update_id"]
                msg = update.get("message", {})
                txt = msg.get("text", "").strip().lower()
                
                if txt == "/report":
                    send_message("Generating on-demand Market Report, please wait...")
                    send_market_report("On-Demand")
                elif txt == "/recap":
                    send_message("Generating on-demand Daily Recap, please wait...")
                    send_daily_recap()
                elif txt == "/status":
                    send_message("Wyckoff ML Bot is actively running and monitoring all timeframes.")
    except:
        pass

def evaluate_ticker_data(ticker, df, interval, lookback, spy_bullish, hour_of_day, now, spy_30m_ret=0.0, vix_slope=0.0):
    """
    Core Wyckoff detection and trade evaluation on a ticker DataFrame.
    Shared by both Public.com streaming scan and fallback bulk scans.
    """
    try:
        min_required = min(lookback, 40)
        if df.empty or len(df) < min_required:
            return
        eff_lookback = min(lookback, max(20, len(df) - 5))
            
        if isinstance(df.columns, pd.MultiIndex):
            opens = df['Open'].iloc[:, 0].values.astype(float) if 'Open' in df else closes
            highs = df['High'].iloc[:, 0].values.astype(float)
            lows = df['Low'].iloc[:, 0].values.astype(float)
            closes = df['Close'].iloc[:, 0].values.astype(float)
            vols = df['Volume'].iloc[:, 0].values.astype(float)
        else:
            opens = df['Open'].values.astype(float) if 'Open' in df else closes
            highs = df['High'].values.astype(float)
            lows = df['Low'].values.astype(float)
            closes = df['Close'].values.astype(float)
            vols = df['Volume'].values.astype(float)
            
        u1 = get_supertrend(highs, lows, closes, 1, 1.0)
        u9 = get_supertrend(highs, lows, closes, 9, 9.0)
        u14 = get_supertrend(highs, lows, closes, 14, 14.0)
        
        # --- Bars in Regime ---
        curr_u9 = u9[-1]
        curr_u14 = u14[-1]
        bars_in_regime = 0
        for i in range(len(u9)-1, -1, -1):
            if u9[i] == curr_u9 and u14[i] == curr_u14:
                bars_in_regime += 1
            else:
                break
        
        # --- Institutional & Wyckoff VSA Features ---
        vwap = calc_vwap(highs, lows, closes, vols)
        vwap_distance = (closes[-1] - vwap[-1]) / vwap[-1] * 100 if vwap[-1] > 0 else 0
        vwap_slope = float((vwap[-1] - vwap[-7]) / vwap[-7] * 100.0) if (len(vwap) >= 7 and vwap[-7] > 0) else 0.0
        
        atr_exp = calc_atr_expansion(highs, lows, closes)
        atr_expansion = float(atr_exp[-1])
        
        vol_sma = pd.Series(vols).rolling(20, min_periods=1).mean().values
        with np.errstate(divide='ignore', invalid='ignore'):
            rel_vol = np.where(vol_sma > 0, vols / vol_sma, 1.0)
        
        curr = len(df) - 1
        
        # Wyckoff Law 3 (Effort vs. Result): Volume relative to candle spread
        curr_spread = abs(highs[curr] - lows[curr])
        spread_sma = pd.Series(highs - lows).rolling(20, min_periods=1).mean().values[curr]
        rel_spread = (curr_spread / spread_sma) if spread_sma > 0 else 1.0
        effort_vs_result = float(rel_vol[curr] / max(0.1, rel_spread))
        
        # Wyckoff Phase C Secondary Test Volume Ratio: test volume vs previous bar
        prev_vol = vols[curr - 1] if curr > 0 and vols[curr - 1] > 0 else vols[curr]
        test_vol_ratio = float(vols[curr] / prev_vol) if prev_vol > 0 else 1.0
        
        # Wyckoff Indicator 1.1: Cumulative Volume Delta (CVD) Absorption Divergence
        curr_rng = abs(highs[curr] - lows[curr])
        cvd_delta_ratio = float((2.0 * (closes[curr] - lows[curr]) - curr_rng) / curr_rng) if curr_rng > 1e-6 else 0.0
        prior_start = max(0, curr - 10)
        prior_low_idx = prior_start + int(np.argmin(lows[prior_start:curr])) if curr > prior_start else curr
        sub_rng = np.where((highs[prior_start:curr+1] - lows[prior_start:curr+1]) == 0, 1e-6, (highs[prior_start:curr+1] - lows[prior_start:curr+1]))
        sub_deltas = vols[prior_start:curr+1] * ((2.0 * (closes[prior_start:curr+1] - lows[prior_start:curr+1]) - sub_rng) / sub_rng)
        sub_cvd = np.cumsum(sub_deltas)
        cvd_higher = bool(sub_cvd[-1] >= sub_cvd[prior_low_idx - prior_start]) if prior_low_idx >= prior_start else True
        cvd_absorption = bool(cvd_delta_ratio >= -0.20 or cvd_higher)
        
        # Indicator 1.2: Time-of-Day Relative Volume (RVOL-TOD)
        try:
            curr_dt = df.index[-1]
            curr_t = curr_dt.time() if hasattr(curr_dt, 'time') else None
            if curr_t:
                same_time_mask = [t.time() == curr_t for t in df.index[:-1]]
                same_vols = vols[:-1][same_time_mask]
                baseline_tod = float(np.median(same_vols[-15:])) if len(same_vols) >= 3 else float(np.median(vols[max(0, curr-20):curr]))
            else:
                baseline_tod = float(np.median(vols[max(0, curr-20):curr]))
            rvol_tod = float(vols[curr] / max(1.0, baseline_tod))
        except Exception:
            rvol_tod = 1.0
        
        range_high = pd.Series(highs).rolling(eff_lookback, min_periods=20).max().shift(1).values
        range_low = pd.Series(lows).rolling(eff_lookback, min_periods=20).min().shift(1).values
        
        if pd.isna(range_high[curr]): return

        if interval in ['5m', '15m']:
            try:
                from priority_queue import update_ticker_in_queue
                update_ticker_in_queue(
                    ticker=ticker,
                    price=float(closes[curr]),
                    tr_low=float(range_low[curr]),
                    tr_high=float(range_high[curr]),
                    bars_in_regime=bars_in_regime,
                    is_bull=bool(curr_u9)
                )
            except Exception:
                pass
        
        # Indicator 2.1: Spring Penetration Depth (%)
        sweep_low = min(lows[curr], lows[curr-1])
        rl = range_low[curr]
        penetration_depth_pct = float(max(0.0, (rl - sweep_low) / rl * 100.0)) if (rl and rl > 0) else 0.0

        # Indicator 2.3: Candle Close Location Value (CLV)
        candle_rng = highs[curr] - lows[curr]
        candle_clv = float(np.clip((closes[curr] - lows[curr]) / candle_rng, 0.0, 1.0)) if candle_rng > 0 else 0.5

        # Indicator 3.1: Consumed Intraday ATR Ratio
        try:
            day_high = np.max(highs[max(0, curr-78):curr+1])
            day_low = np.min(lows[max(0, curr-78):curr+1])
            day_range = day_high - day_low
            tr = np.maximum(highs[1:curr+1] - lows[1:curr+1],
                            np.maximum(abs(highs[1:curr+1] - closes[:curr]),
                                       abs(lows[1:curr+1] - closes[:curr])))
            avg_tr = np.mean(tr[-78:]) * np.sqrt(78) if len(tr) >= 78 else np.mean(tr) * np.sqrt(len(tr))
            consumed_atr = float(day_range / avg_tr) if avg_tr > 0 else 1.0
            atr_headroom = max(0.0, round(1.0 - consumed_atr, 3))
        except Exception:
            consumed_atr = 1.0
            atr_headroom = 0.50

        # Indicator 2.4 (Roadmap #9): Secondary Test / SOS Reclaim Volume Ratio
        prev_v = vols[curr - 1]
        st_volume_ratio = float(vols[curr] / prev_v) if prev_v > 0 else 1.0

        # Indicator 1.4 (Roadmap #4): Intraday VPOC Distance (%)
        try:
            lookback_day = min(curr, 78)
            sess_closes = closes[curr - lookback_day:curr + 1]
            sess_vols = vols[curr - lookback_day:curr + 1]
            if len(sess_closes) >= 5 and np.sum(sess_vols) > 0:
                hist, bin_edges = np.histogram(sess_closes, bins=20, weights=sess_vols)
                max_bin = np.argmax(hist)
                vpoc_price = (bin_edges[max_bin] + bin_edges[max_bin + 1]) * 0.5
                vpoc_distance_pct = float((closes[curr] - vpoc_price) / vpoc_price * 100.0) if vpoc_price > 0 else 0.0
            else:
                vpoc_distance_pct = 0.0
        except Exception:
            vpoc_distance_pct = 0.0

        # Indicator 3.2 (Roadmap #12): Multi-Timeframe ATR Expansion Ratio
        try:
            start_36 = max(0, curr - 36)
            tr_36 = np.maximum(highs[start_36:curr+1] - lows[start_36:curr+1],
                               np.maximum(abs(highs[start_36:curr+1] - closes[start_36-1:curr]),
                                          abs(lows[start_36:curr+1] - closes[start_36-1:curr]))) if start_36 > 0 else (highs[start_36:curr+1] - lows[start_36:curr+1])
            short_atr = np.mean(tr_36[-6:]) if len(tr_36) >= 6 else 1.0
            long_atr = np.mean(tr_36) if len(tr_36) > 0 else 1.0
            mtf_atr_ratio = float(short_atr / long_atr) if long_atr > 0 else 1.0
        except Exception:
            mtf_atr_ratio = 1.0

        # Indicator 2.5 (Roadmap #10): Pre-Spring Trend Exhaustion Score
        try:
            start_ex = max(0, curr - 8)
            red_count = sum(1 for i in range(start_ex, curr) if closes[i] < opens[i])
            lower_low_count = sum(1 for i in range(start_ex + 1, curr) if lows[i] < lows[i-1])
            trend_exhaustion = float((red_count + lower_low_count) / 2.0)
        except Exception:
            trend_exhaustion = 3.0

        # Indicator 4.2 (#14): Relative Strength vs SPY (30m Alpha)
        try:
            curr_start_6 = max(0, curr - 6)
            ticker_30m_ret = float((closes[curr] - closes[curr_start_6]) / closes[curr_start_6] * 100.0) if closes[curr_start_6] > 0 else 0.0
            rs_alpha = float(ticker_30m_ret - spy_30m_ret)
        except Exception:
            rs_alpha = 0.0

        # Indicator 2.7: Opening Range (ORB) Position (First 6 bars: 9:30 - 10:00 AM)
        try:
            curr_date = df.index[curr].date() if hasattr(df.index[curr], 'date') else None
            if curr_date is not None:
                sess_bars = [i for i in range(max(0, curr - 78), curr + 1) if hasattr(df.index[i], 'date') and df.index[i].date() == curr_date]
            else:
                sess_bars = list(range(max(0, curr - 20), curr + 1))
            
            if len(sess_bars) >= 6:
                orb_bars = sess_bars[:6]
                orb_h = max(highs[orb_bars])
                orb_l = min(lows[orb_bars])
                if closes[curr] > orb_h:
                    orb_regime_code = 1
                elif closes[curr] < orb_l:
                    orb_regime_code = -1
                else:
                    orb_regime_code = 0
            else:
                orb_regime_code = 0
        except Exception:
            orb_regime_code = 0

        # Indicator 4.4: Sector ETF Intraday Return (%)
        try:
            sector_ret_pct = round(get_sector_return(ticker), 2)
        except Exception:
            sector_ret_pct = 0.0

        c_below = (lows[curr] < range_low[curr]) or (lows[curr-1] < range_low[curr-1])
        vol_dry = rel_vol[curr] < VOL_LIMIT
        # Mode 2: High-Volume Institutional Absorption Spring (Shakeout with Hammer CLV >= 0.65 & Positive CVD >= 0.20)
        vol_absorption = (rel_vol[curr] >= VOL_LIMIT) and (candle_clv >= 0.65) and (cvd_delta_ratio >= 0.20)
        vol_spring_ok = vol_dry or vol_absorption
        
        # Also catch late-candle 5m Spring completions on curr-1 if the new bar just opened and still holds u1[curr]
        c_below_prev = (curr >= 2) and ((lows[curr-1] < range_low[curr-1]) or (lows[curr-2] < range_low[curr-2]))
        prev_rng = abs(highs[curr-1] - lows[curr-1]) if curr >= 1 else 0.0
        prev_clv = float(np.clip((closes[curr-1] - lows[curr-1]) / prev_rng, 0.0, 1.0)) if prev_rng > 1e-6 else 0.5
        prev_cvd = float((2.0 * (closes[curr-1] - lows[curr-1]) - prev_rng) / prev_rng) if prev_rng > 1e-6 else 0.0
        vol_prev_ok = (curr >= 1) and ((rel_vol[curr-1] < VOL_LIMIT) or (prev_clv >= 0.65 and prev_cvd >= 0.20))
        is_spring_just_closed = (
            interval == '5m' and curr >= 2 and
            c_below_prev and u1[curr-1] and not u1[curr-2] and u1[curr] and not u9[curr] and vol_prev_ok
        )
        
        # LONG (Spring): 100% Intact Dry-Up (< 1.2x) + High-Volume Institutional Absorption + late-candle catch
        is_spring = (c_below and u1[curr] and not u1[curr-1] and not u9[curr] and vol_spring_ok) or is_spring_just_closed
        is_absorption_spring = bool(is_spring and not vol_dry and (vol_absorption or (is_spring_just_closed and rel_vol[curr-1] >= VOL_LIMIT)))
        
        # SHORT (UTAD / LPSY - Decoupled Wyckoff Distribution Engine):
        # 1. Structural UTAD Sweep: Price probed above Range High in previous 1 to 10 bars
        sweep_bars = [k for k in range(max(0, curr - 10), curr) if highs[k] > range_high[k]]
        has_utad_probe = len(sweep_bars) > 0
        utad_peak = max(highs[k] for k in sweep_bars) if sweep_bars else highs[curr]
        
        # 2. Failed Auction Acceptance (Time In Range): 2 consecutive closes back below Range High
        rejection_accepted = (closes[curr] < range_high[curr]) and (closes[curr-1] < range_high[curr-1])
        
        # 3. LPSY Secondary Test: Current test bar must NOT make a higher high above the UTAD peak
        is_lower_high = highs[curr] < utad_peak
        
        # 4. Micro Rollover: Micro Supertrend flips RED on the test rejection
        micro_red_flip = not u1[curr] and u1[curr-1]
        
        # 5. Dedicated UTAD Signal Trigger: Requires true Phase B distribution time (>= 20 bars in regime)
        is_utad = (
            has_utad_probe and 
            rejection_accepted and 
            is_lower_high and 
            micro_red_flip and 
            (bars_in_regime >= 20)
        )
        
        current_time = time.time()
        
        if is_spring and (current_time - last_alerted.get(ticker, 0) > 900):
            open_second_spring = None
            if TRACKER_ENABLED and (has_open_alerted_trade(ticker, interval) or has_open_trade(ticker, interval)):
                try:
                    from auto_buyer import get_open_trade_for_second_spring
                    open_second_spring = get_open_trade_for_second_spring(ticker, closes[curr])
                except Exception as e:
                    open_second_spring = None

                if not open_second_spring:
                    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] BLOCKED DUPLICATE: {ticker} already has active open trade on {interval}")
                    last_alerted[ticker] = current_time
                    return
                else:
                    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🎯 SECOND SPRING DETECTED: {ticker} testing support above ${open_second_spring.get('initial_stop_loss', 0.0):.2f}")

            # Morning Opening Shield (9:30 - 9:40 AM open order imbalance)
            if now.hour == 9 and 30 <= now.minute < 40 and interval == "5m":
                print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] OPENING SHIELD: {ticker} 5m Spring suppressed during 9:30-9:40 AM")
                return

            # Indicator 1.1: Cumulative Volume Delta (CVD) Extreme Blowout Shield (Only block extreme toxic dumps < -0.60)
            if cvd_delta_ratio < -0.60:
                print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] BLOCKED BY CVD BLOWOUT: {ticker} LONG - extreme toxic selling delta ({cvd_delta_ratio:.2f} < -0.60)")
                last_alerted[ticker] = current_time
                return

            flow = None
            if OPTIONS_ENABLED:
                flow = get_options_flow(ticker)
                if flow.get('put_call_ratio', 0) > 1.3:
                    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] BLOCKED: {ticker} LONG - P/C: {flow.get('put_call_ratio')}")
                    last_alerted[ticker] = current_time
                    return
            
            price = closes[curr]
            sl = min(lows[curr], lows[curr-1]) * (1.0 - SL_BUFFER)
            risk = abs(price - sl)
            if risk <= 0.001:
                return

            spring_subtype = "High-Vol Absorption Spring" if is_absorption_spring else "Dry-Up Spring"
            regime = f"BEARISH ({spring_subtype})" if not u9[curr] and not u14[curr] else f"MIXED ({spring_subtype})"
            
            # --- Extract All 4 Institutional Tactics ---
            inst = compute_all_institutional_features(ticker, price, vols[curr], rel_vol=rel_vol[curr],
                                                      effort_vs_result=effort_vs_result, options_flow=flow,
                                                      current_time=now) if INST_ENGINE_ENABLED else {}

            # --- ML Dynamic Sweet-Spot Target Optimization [0.65R to 1.35R] ---
            if ML_TARGET_OPT_ENABLED:
                opt_target_r = calculate_optimal_target_r(
                    atr_expansion=atr_expansion,
                    test_vol_ratio=test_vol_ratio,
                    dealer_gamma_regime=inst.get('dealer_gamma_regime', 0),
                    gamma_wall_dist_pct=inst.get('gamma_wall_dist_pct', 0.0),
                    institutional_block_ratio=inst.get('institutional_block_ratio', 1.0),
                    effort_vs_result=effort_vs_result,
                    cvd_delta_ratio=cvd_delta_ratio,
                    rvol_tod=rvol_tod,
                    penetration_depth_pct=penetration_depth_pct,
                    candle_clv=candle_clv,
                    consumed_atr=consumed_atr,
                    st_volume_ratio=st_volume_ratio,
                    vpoc_distance_pct=vpoc_distance_pct,
                    mtf_atr_ratio=mtf_atr_ratio,
                    trend_exhaustion=trend_exhaustion,
                    vix_slope=vix_slope,
                    rs_alpha=rs_alpha,
                    pcr=flow.get('put_call_ratio') if flow else 0.70,
                    vwap_slope=vwap_slope,
                    orb_regime_code=orb_regime_code,
                    atr_headroom=atr_headroom,
                    sector_ret_pct=sector_ret_pct,
                    sector_aligned=1 if sector_ret_pct >= 0.0 else 0
                )
            else:
                opt_target_r = 1.15
                
            tp = price + (opt_target_r * risk)
            if flow and flow.get('gamma_wall', 0) > price:
                gw = flow.get('gamma_wall', 0)
                gw_r = (gw - price) / risk if risk > 0 else 0
                if 1.05 <= gw_r <= 1.35:
                    tp = gw
                    opt_target_r = round(gw_r, 2)
                    
            reward = abs(tp - price)
            r_units = (reward / risk) if risk > 0 else 0

            # Calculate Real ML Model Win Probability with All Institutional Features
            ml_conf = calculate_ml_confidence(bars_in_regime, vwap_distance, atr_expansion, hour_of_day, "LONG",
                                              effort_vs_result=effort_vs_result, test_vol_ratio=test_vol_ratio,
                                              days_to_rebalance=inst.get('days_to_rebalance', 45),
                                              is_triple_witching=inst.get('is_triple_witching', 0),
                                              dealer_gamma_regime=inst.get('dealer_gamma_regime', 0),
                                              gamma_wall_dist_pct=inst.get('gamma_wall_dist_pct', 0.0),
                                              moc_surge_score=inst.get('moc_surge_score', 0.0),
                                              institutional_block_ratio=inst.get('institutional_block_ratio', 1.0),
                                              cvd_delta_ratio=cvd_delta_ratio,
                                              rvol_tod=rvol_tod,
                                              penetration_depth_pct=penetration_depth_pct,
                                              candle_clv=candle_clv,
                                              consumed_atr=consumed_atr,
                                              st_volume_ratio=st_volume_ratio,
                                              vpoc_distance_pct=vpoc_distance_pct,
                                              mtf_atr_ratio=mtf_atr_ratio,
                                              trend_exhaustion=trend_exhaustion,
                                              vix_slope=vix_slope,
                                              rs_alpha=rs_alpha,
                                              pcr=flow.get('put_call_ratio') if flow else 0.70,
                                              vwap_slope=vwap_slope,
                                              orb_regime_code=orb_regime_code,
                                              atr_headroom=atr_headroom,
                                              sector_ret_pct=sector_ret_pct,
                                              sector_aligned=1 if sector_ret_pct >= 0.0 else 0) if TRACKER_ENABLED else None

            if r_units < MIN_R_UNITS:
                print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] FILTERED: {ticker} LONG - R-Units too low ({r_units:.2f}R < {MIN_R_UNITS}R)")
                last_alerted[ticker] = current_time
                return

            # If this is a validated Second Spring / Secondary Test on an open position:
            if open_second_spring:
                if r_units < 0.55:
                    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 2nd Spring Pyramid Bypassed: {ticker} target too tight ({r_units:.2f}R < 0.55R)")
                    last_alerted[ticker] = current_time
                    return

                auto_executed = False
                auto_reason = None
                try:
                    from auto_buyer import execute_autonomous_second_spring_buy
                    auto_executed, auto_reason = execute_autonomous_second_spring_buy(
                        open_second_spring['id'], ticker, price, ml_conf, timeframe=interval
                    )
                except Exception as e:
                    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 2nd Spring Auto-Buy error: {e}")

                if TELEGRAM_ENABLED and not auto_executed:
                    tg_trade_alert(ticker, "LONG (2nd SPRING / TEST)", price, open_second_spring['initial_stop_loss'], open_second_spring['take_profit'], regime, interval, flow, open_second_spring['id'], ml_confidence=ml_conf, auto_reason=auto_reason)
                last_alerted[ticker] = current_time
                return

            trade_id = None
            if TRACKER_ENABLED:
                q = get_public_quotes(ticker) if OPTIONS_ENABLED else {'bid_ask_ratio': None, 'spread_width_pct': None}
                trade_id = log_alert(ticker, "LONG", price, sl, tp, regime, interval,
                          flow.get('put_call_ratio') if flow else None,
                          flow.get('net_sentiment') if flow else None,
                          bars_in_regime,
                          vwap_distance=vwap_distance,
                          hour_of_day=hour_of_day,
                          spy_bullish=spy_bullish,
                          atr_expansion=atr_expansion,
                          bid_ask_ratio=q.get('bid_ask_ratio'),
                          spread_width_pct=q.get('spread_width_pct'),
                          implied_volatility=flow.get('atm_iv') if flow else None,
                          ml_confidence=ml_conf,
                          effort_vs_result=effort_vs_result,
                          test_vol_ratio=test_vol_ratio,
                          days_to_rebalance=inst.get('days_to_rebalance', 45),
                          is_triple_witching=inst.get('is_triple_witching', 0),
                          dealer_gamma_regime=inst.get('dealer_gamma_regime', 0),
                          gamma_wall_dist_pct=inst.get('gamma_wall_dist_pct', 0.0),
                          moc_surge_score=inst.get('moc_surge_score', 0.0),
                          institutional_block_ratio=inst.get('institutional_block_ratio', 1.0),
                          optimal_target_r=opt_target_r,
                          cvd_delta_ratio=cvd_delta_ratio,
                          rvol_tod=rvol_tod,
                          penetration_depth_pct=penetration_depth_pct,
                          candle_clv=candle_clv,
                          consumed_atr=consumed_atr,
                          st_volume_ratio=st_volume_ratio,
                          vpoc_distance_pct=vpoc_distance_pct,
                          mtf_atr_ratio=mtf_atr_ratio,
                          trend_exhaustion=trend_exhaustion,
                          vix_slope=vix_slope,
                          rs_alpha=rs_alpha,
                          vwap_slope=vwap_slope,
                          orb_regime_code=orb_regime_code,
                          atr_headroom=atr_headroom,
                          sector_ret_pct=sector_ret_pct,
                          sector_aligned=1 if sector_ret_pct >= 0.0 else 0)
            
            auto_executed = False
            auto_reason = None
            if trade_id:
                try:
                    from auto_buyer import execute_autonomous_spring_buy
                    auto_executed, auto_reason = execute_autonomous_spring_buy(trade_id, ticker, price, sl, tp, ml_conf, timeframe=interval, target_r=r_units)
                except Exception as e:
                    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Auto-buyer hook error: {e}")

            if TELEGRAM_ENABLED and not auto_executed:
                tg_trade_alert(ticker, "LONG (SPRING)", price, sl, tp, regime, interval, flow, trade_id, ml_confidence=ml_conf, auto_reason=auto_reason)
                if TRACKER_ENABLED and trade_id: mark_trade_alerted(trade_id)
            elif TRACKER_ENABLED and trade_id:
                mark_trade_alerted(trade_id)
            last_alerted[ticker] = current_time
            
        elif is_utad and (current_time - last_alerted.get(ticker, 0) > 900):
            if TRACKER_ENABLED and (has_open_alerted_trade(ticker, interval) or has_open_trade(ticker, interval)):
                print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] BLOCKED DUPLICATE: {ticker} already has active open trade on {interval}")
                last_alerted[ticker] = current_time
                return

            # Short Timing Gate 1: Morning Expansion Shield (no shorts before 10:30 AM EST)
            if interval == "5m" and (now.hour == 9 or (now.hour == 10 and now.minute < 30)):
                print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] MORNING EXPANSION SHIELD: {ticker} short suppressed before 10:30 AM EST")
                return

            # Short Timing Gate 2: Lunch Lull Shield (no shorts 12:30 - 1:30 PM EST)
            if interval == "5m" and ((now.hour == 12 and now.minute >= 30) or (now.hour == 13 and now.minute < 30)):
                print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] LUNCH LULL SHIELD: {ticker} short suppressed during 12:30-1:30 PM EST")
                return

            # Short VWAP Gate: Never short runaway parabolic stocks > 2.2% above VWAP
            if vwap_distance > 2.2:
                print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] VWAP RUNAWAY SHIELD: {ticker} short suppressed (VWAP Dist: {vwap_distance:+.2f}% > +2.2%)")
                return

            # Short Macro Market Gate: In a Bullish SPY regime, only short if stock shows relative weakness below VWAP
            if spy_bullish and vwap_distance > 0.0:
                print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] MACRO TIDE SHIELD: {ticker} short suppressed - SPY is Bullish and stock is above VWAP ({vwap_distance:+.2f}%)")
                return

            flow = None
            if OPTIONS_ENABLED:
                flow = get_options_flow(ticker)
                if flow.get('put_call_ratio', 0) < 0.7:
                    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] BLOCKED: {ticker} SHORT - P/C: {flow.get('put_call_ratio')}")
                    last_alerted[ticker] = current_time
                    return
                    
            price = closes[curr]
            # Structural Invalidation Stop Loss: Anchored strictly at the physical UTAD peak
            sl = max(utad_peak, highs[curr]) * (1.0 + SL_BUFFER)
            risk = abs(sl - price)
            if risk <= 0.001:
                return

            regime = "DISTRIBUTION (LPSY Confirmed)" if u9[curr] and u14[curr] else "MIXED"
            
            # --- Extract All 4 Institutional Tactics ---
            inst = compute_all_institutional_features(ticker, price, vols[curr], rel_vol=rel_vol[curr],
                                                      effort_vs_result=effort_vs_result, options_flow=flow,
                                                      current_time=now) if INST_ENGINE_ENABLED else {}

            # --- ML Dynamic Sweet-Spot Target Optimization [1.05R to 1.35R] ---
            if ML_TARGET_OPT_ENABLED:
                opt_target_r = calculate_optimal_target_r(
                    atr_expansion=atr_expansion,
                    test_vol_ratio=test_vol_ratio,
                    dealer_gamma_regime=inst.get('dealer_gamma_regime', 0),
                    gamma_wall_dist_pct=inst.get('gamma_wall_dist_pct', 0.0),
                    institutional_block_ratio=inst.get('institutional_block_ratio', 1.0),
                    effort_vs_result=effort_vs_result,
                    cvd_delta_ratio=cvd_delta_ratio,
                    rvol_tod=rvol_tod,
                    penetration_depth_pct=0.0,
                    candle_clv=candle_clv,
                    consumed_atr=consumed_atr,
                    st_volume_ratio=st_volume_ratio,
                    vpoc_distance_pct=vpoc_distance_pct,
                    mtf_atr_ratio=mtf_atr_ratio,
                    trend_exhaustion=trend_exhaustion,
                    vix_slope=vix_slope,
                    rs_alpha=rs_alpha,
                    pcr=flow.get('put_call_ratio') if flow else 0.70,
                    vwap_slope=vwap_slope,
                    orb_regime_code=orb_regime_code,
                    atr_headroom=atr_headroom,
                    sector_ret_pct=sector_ret_pct,
                    sector_aligned=1 if sector_ret_pct <= 0.0 else 0
                )
            else:
                opt_target_r = 1.15
                
            tp = price - (opt_target_r * risk)
            if flow and flow.get('gamma_wall', 0) < price and flow.get('gamma_wall', 0) > 0:
                gw = flow.get('gamma_wall', 0)
                gw_r = (price - gw) / risk if risk > 0 else 0
                if 1.05 <= gw_r <= 1.35:
                    tp = gw
                    opt_target_r = round(gw_r, 2)
                    
            reward = abs(price - tp)
            r_units = (reward / risk) if risk > 0 else 0

            # Calculate Real ML Model Win Probability with All Institutional Features
            ml_conf = calculate_ml_confidence(bars_in_regime, vwap_distance, atr_expansion, hour_of_day, "SHORT",
                                              effort_vs_result=effort_vs_result, test_vol_ratio=test_vol_ratio,
                                              days_to_rebalance=inst.get('days_to_rebalance', 45),
                                              is_triple_witching=inst.get('is_triple_witching', 0),
                                              dealer_gamma_regime=inst.get('dealer_gamma_regime', 0),
                                              gamma_wall_dist_pct=inst.get('gamma_wall_dist_pct', 0.0),
                                              moc_surge_score=inst.get('moc_surge_score', 0.0),
                                              institutional_block_ratio=inst.get('institutional_block_ratio', 1.0),
                                              cvd_delta_ratio=cvd_delta_ratio,
                                              rvol_tod=rvol_tod,
                                              penetration_depth_pct=0.0,
                                              candle_clv=candle_clv,
                                              consumed_atr=consumed_atr,
                                              st_volume_ratio=st_volume_ratio,
                                              vpoc_distance_pct=vpoc_distance_pct,
                                              mtf_atr_ratio=mtf_atr_ratio,
                                              trend_exhaustion=trend_exhaustion,
                                              vix_slope=vix_slope,
                                              rs_alpha=rs_alpha,
                                              pcr=flow.get('put_call_ratio') if flow else 0.70,
                                              vwap_slope=vwap_slope,
                                              orb_regime_code=orb_regime_code,
                                              atr_headroom=atr_headroom,
                                              sector_ret_pct=sector_ret_pct,
                                              sector_aligned=1 if sector_ret_pct <= 0.0 else 0) if TRACKER_ENABLED else None

            if r_units < MIN_R_UNITS:
                print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] FILTERED: {ticker} SHORT - R-Units too low ({r_units:.2f}R < {MIN_R_UNITS}R)")
                last_alerted[ticker] = current_time
                return

            trade_id = None
            if TRACKER_ENABLED:
                q = get_public_quotes(ticker) if OPTIONS_ENABLED else {'bid_ask_ratio': None, 'spread_width_pct': None}
                trade_id = log_alert(ticker, "SHORT", price, sl, tp, regime, interval,
                          flow.get('put_call_ratio') if flow else None,
                          flow.get('net_sentiment') if flow else None,
                          bars_in_regime,
                          vwap_distance=vwap_distance,
                          hour_of_day=hour_of_day,
                          spy_bullish=spy_bullish,
                          atr_expansion=atr_expansion,
                          bid_ask_ratio=q.get('bid_ask_ratio'),
                          spread_width_pct=q.get('spread_width_pct'),
                          implied_volatility=flow.get('atm_iv') if flow else None,
                          ml_confidence=ml_conf,
                          effort_vs_result=effort_vs_result,
                          test_vol_ratio=test_vol_ratio,
                          days_to_rebalance=inst.get('days_to_rebalance', 45),
                          is_triple_witching=inst.get('is_triple_witching', 0),
                          dealer_gamma_regime=inst.get('dealer_gamma_regime', 0),
                          gamma_wall_dist_pct=inst.get('gamma_wall_dist_pct', 0.0),
                          moc_surge_score=inst.get('moc_surge_score', 0.0),
                          institutional_block_ratio=inst.get('institutional_block_ratio', 1.0),
                          optimal_target_r=opt_target_r,
                          cvd_delta_ratio=cvd_delta_ratio,
                          rvol_tod=rvol_tod,
                          penetration_depth_pct=0.0,
                          candle_clv=candle_clv,
                          consumed_atr=consumed_atr,
                          st_volume_ratio=st_volume_ratio,
                          vpoc_distance_pct=vpoc_distance_pct,
                          mtf_atr_ratio=mtf_atr_ratio,
                          trend_exhaustion=trend_exhaustion,
                          vix_slope=vix_slope,
                          rs_alpha=rs_alpha,
                          vwap_slope=vwap_slope,
                          orb_regime_code=orb_regime_code,
                          atr_headroom=atr_headroom,
                          sector_ret_pct=sector_ret_pct,
                          sector_aligned=1 if sector_ret_pct <= 0.0 else 0)
            
            if TELEGRAM_ENABLED:
                tg_trade_alert(ticker, "SHORT (UTAD)", price, sl, tp, regime, interval, flow, trade_id, ml_confidence=ml_conf)
                if TRACKER_ENABLED and trade_id: mark_trade_alerted(trade_id)
            last_alerted[ticker] = current_time
    except Exception as e:
        pass

# ===================================================================
# PUBLIC.COM LIVE MARKET SCANNER (Primary Data Feed)
# ===================================================================
async def scan_market_public(interval, lookback):
    if interval == '5m':
        from priority_queue import get_active_5m_tickers
        scan_tickers = get_active_5m_tickers(TICKERS)
        tier_label = f"Dynamic Fast Queue ({len(scan_tickers)} tickers)"
    else:
        scan_tickers = TICKERS
        tier_label = f"Full Universe ({len(scan_tickers)} tickers)"

    print(f"\n[{get_market_now().strftime('%H:%M:%S')}] [PUBLIC.COM LIVE] Streaming {tier_label} on {interval}...")
    try:
        spy_bullish, spy_30m_ret, vix_slope = get_macro_context()
    except Exception:
        spy_bullish = await get_spy_trend_public()
        spy_30m_ret = 0.0
        vix_slope = 0.0
    now = get_market_now()
    hour_of_day = now.hour + now.minute / 60.0
    
    count = 0
    async for ticker, df in stream_ticker_bars(scan_tickers, interval=interval, delay_ms=0.08):
        count += 1
        evaluate_ticker_data(ticker, df, interval, lookback, spy_bullish, hour_of_day, now, spy_30m_ret, vix_slope)

    try:
        from priority_queue import flush_priority_queue
        flush_priority_queue()
    except Exception:
        pass

    print(f"[{get_market_now().strftime('%H:%M:%S')}] [PUBLIC.COM LIVE] Finished {count} tickers on {interval}.")

# Fallback bulk scanner (yfinance)
def scan_market(interval, period, lookback):
    if PUBLIC_DATA_ENABLED:
        import asyncio
        asyncio.run(scan_market_public(interval, lookback))
        return

    if interval == '5m':
        from priority_queue import get_active_5m_tickers
        scan_tickers = get_active_5m_tickers(TICKERS)
    else:
        scan_tickers = TICKERS
        
    print(f"\n[{get_market_now().strftime('%H:%M:%S')}] [FALLBACK YFINANCE] Scanning {len(scan_tickers)} tickers on {interval}...")
    data = yf.download(scan_tickers, period=period, interval=interval, group_by='ticker', progress=False)
    try:
        spy_bullish, spy_30m_ret, vix_slope = get_macro_context()
    except Exception:
        spy_bullish = get_spy_trend()
        spy_30m_ret = 0.0
        vix_slope = 0.0
    now = get_market_now()
    hour_of_day = now.hour + now.minute / 60.0
    for ticker in scan_tickers:
        try:
            df = data[ticker].dropna() if len(scan_tickers) > 1 else data.dropna()
            evaluate_ticker_data(ticker, df, interval, lookback, spy_bullish, hour_of_day, now, spy_30m_ret, vix_slope)
        except: pass

    try:
        from priority_queue import flush_priority_queue
        flush_priority_queue()
    except Exception:
        pass


last_report_date = None
reports_sent = {"morning": False, "lunch": False, "power": False}

def send_market_report(session_name):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Generating {session_name} Report...")
    scan_tickers = TICKERS[:25]
    data = yf.download(scan_tickers, period="10d", interval="1h", group_by='ticker', progress=False)
    
    exhausted = []
    for ticker in scan_tickers:
        try:
            df = data[ticker].dropna() if len(TICKERS) > 1 else data.dropna()
            if df.empty or len(df) < 50: continue
            
            if isinstance(df.columns, pd.MultiIndex):
                highs = df['High'].iloc[:, 0].values.astype(float)
                lows = df['Low'].iloc[:, 0].values.astype(float)
                closes = df['Close'].iloc[:, 0].values.astype(float)
            else:
                highs = df['High'].values.astype(float)
                lows = df['Low'].values.astype(float)
                closes = df['Close'].values.astype(float)
            
            u9 = get_supertrend(highs, lows, closes, 9, 9.0)
            u14 = get_supertrend(highs, lows, closes, 14, 14.0)
            
            is_bull = u9[-1] and u14[-1]
            is_bear = not u9[-1] and not u14[-1]
            bars = 0
            if is_bull or is_bear:
                for i in range(len(u9)-1, -1, -1):
                    if (is_bull and u9[i] and u14[i]) or (is_bear and not u9[i] and not u14[i]): bars += 1
                    else: break
                    
            if bars >= 20:
                regime = "BULLISH" if is_bull else "BEARISH"
                target = "UTAD (Short)" if is_bull else "SPRING (Long)"
                exhausted.append((ticker, regime, bars, target))
        except: pass
            
    exhausted.sort(key=lambda x: x[2], reverse=True)
    
    body = f"WYCKOFF MARKET RADAR: {session_name}\n"
    body += "------------------------------------------------------\n"
    body += "Most structurally exhausted stocks to stalk:\n\n"
    
    if not exhausted:
        body += "No stocks showing significant exhaustion (>= 20 bars).\n"
    else:
        for t in exhausted[:5]:
            body += f"- {t[0]}: {t[1]} Regime ({t[2]} bars exhausted). Stalk for {t[3]}.\n"
            
    body += "\nReminder: Wait for the Micro Spark (1,1) to confirm the entry!\n"
    
    msg = MIMEMultipart()
    msg['From'] = GMAIL_USER
    msg['To'] = DESTINATION_EMAIL
    msg['Subject'] = f"WYCKOFF RADAR: {session_name} Update"
    msg.attach(MIMEText(body, 'plain', 'utf-8'))
    
    try:
        # server = smtplib.SMTP('smtp.gmail.com', 587)
        # server.starttls()()
        # server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        # server.sendmail(GMAIL_USER, DESTINATION_EMAIL, msg.as_string())
        # server.quit()
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {session_name} REPORT SENT!")
    except Exception as e:
        print(f"Error sending report: {e}")
    
    if TELEGRAM_ENABLED:
        try:
            tg_radar(exhausted[:5], session_name)
            print(f"[{get_market_now().strftime('%H:%M:%S')}] Telegram Radar sent for {session_name}!")
        except Exception as e:
            print(f"Error sending Telegram radar: {e}")

def send_daily_recap():
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Generating Daily Recap...")
    scan_tickers = TICKERS[:25]
    data = yf.download(scan_tickers, period="10d", interval="1h", group_by='ticker', progress=False)
    
    exhausted = []
    for ticker in scan_tickers:
        try:
            df = data[ticker].dropna() if len(TICKERS) > 1 else data.dropna()
            if df.empty or len(df) < 50: continue
            
            if isinstance(df.columns, pd.MultiIndex):
                highs = df['High'].iloc[:, 0].values.astype(float)
                lows = df['Low'].iloc[:, 0].values.astype(float)
                closes = df['Close'].iloc[:, 0].values.astype(float)
            else:
                highs = df['High'].values.astype(float)
                lows = df['Low'].values.astype(float)
                closes = df['Close'].values.astype(float)
            
            u9 = get_supertrend(highs, lows, closes, 9, 9.0)
            u14 = get_supertrend(highs, lows, closes, 14, 14.0)
            
            is_bull = u9[-1] and u14[-1]
            is_bear = not u9[-1] and not u14[-1]
            bars = 0
            if is_bull or is_bear:
                for i in range(len(u9)-1, -1, -1):
                    if (is_bull and u9[i] and u14[i]) or (is_bear and not u9[i] and not u14[i]): bars += 1
                    else: break
                    
            if bars >= 20:
                regime = "BULLISH" if is_bull else "BEARISH"
                target = "UTAD (Short)" if is_bull else "SPRING (Long)"
                exhausted.append((ticker, regime, bars, target))
        except: pass
            
    exhausted.sort(key=lambda x: x[2], reverse=True)
    
    body = "END OF DAY WYCKOFF RECAP & WATCHLIST FOR TOMORROW\n"
    body += "=================================================\n\n"
    
    if not exhausted:
        body += "No major exhaustion setups identified. Market is mixed.\n"
    else:
        body += "TOP COILED SETUPS TO WATCH:\n"
        for i, t in enumerate(exhausted[:5]):
            body += f"{i+1}. {t[0]} - {t[2]} bars deep in a {t[1]} regime. Stalking for a {t[3]}.\n"
            
    body += "\nGAME PLAN: Do not front-run! Wait for the Micro Supertrend confirmation.\n"
    
    msg = MIMEMultipart()
    msg['From'] = GMAIL_USER
    msg['To'] = DESTINATION_EMAIL
    msg['Subject'] = "WYCKOFF: Daily Recap & Tomorrow's Watchlist"
    msg.attach(MIMEText(body, 'plain', 'utf-8'))
    
    try:
        # server = smtplib.SMTP('smtp.gmail.com', 587)
        # server.starttls()()
        # server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        # server.sendmail(GMAIL_USER, DESTINATION_EMAIL, msg.as_string())
        # server.quit()
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Daily Recap Sent!")
    except Exception as e:
        print(f"Error sending recap: {e}")

def send_ai_progress_report(trigger_reason="Daily Post-Market Evolution"):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Running AI Evolution Engine...")
    try:
        from wyckoff_ml_engine import train_and_upgrade_model
        success, report = train_and_upgrade_model(trigger_reason)
        if success and TELEGRAM_ENABLED:
            from telegram_notifier import send_message
            send_message(report)
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] AI Evolution Report sent to Telegram!")
    except Exception as e:
        print(f"Error running AI evolution engine: {e}")

if __name__ == "__main__":
    print("========================================")
    print("WYCKOFF LIVE ALERT BOT v11.0 (HYBRID)")
    print("========================================")
    print(f"Targeting: {len(TICKERS)} Mega-Cap Stocks")
    print(f"Data: yfinance (bulk) + Public.com (execution)")
    print(f"Intervals: 5m, 15m, 1h, 1d")
    print("Bot is now running. Press Ctrl+C to stop.\n")
    
    # Send initial report on boot
    if TELEGRAM_ENABLED:
        from telegram_notifier import send_message
        send_message("Wyckoff Bot v11.0 (Hybrid) initialized. Scanning all timeframes.")
    

    try:
        from exit_guardian import start_guardian_daemon
        start_guardian_daemon()
    except Exception as e:
        print(f"Error launching Exit Guardian daemon: {e}")

    # Launch Engine 2: Background Multi-Timeframe (15m/1h/1d) & Broad 5M Universe Sweeper via Public.com API
    # Keeps Engine 1 (5M Priority Queue Scanner) 100% non-blocking with zero Yahoo Finance IP rate-limit errors.
    def start_broad_5m_sweeper():
        import threading
        def sweeper_loop():
            print("[Engine 2] Background Multi-TF (15m/1h/1d) & Broad 5M Public.com Sweeper daemon started (616 stocks).")
            sweep_cursor = 0
            while True:
                try:
                    now = get_market_now()
                    is_weekday = now.weekday() < 5
                    is_scanning_hours = is_weekday and (7 <= now.hour < 17)
                    if is_scanning_hours:
                        # A. 15m wide-net sweep (full 616 tickers) on 15m candle closes (:00, :15, :30, :45) or initial boot
                        current_quarter = (now.minute // 15) * 15
                        is_15m_due = (now.minute % 15 == 0 and not reports_sent.get(f"m15_{current_quarter}", False)) or not reports_sent.get("m15_init", False)
                        if is_15m_due:
                            reports_sent[f"m15_{current_quarter}"] = True
                            reports_sent["m15_init"] = True
                            for m in [0, 15, 30, 45]:
                                if m != current_quarter:
                                    reports_sent[f"m15_{m}"] = False
                            scan_market('15m', '20d', 80)

                        # B. 1h scanned at the top of each hour (between :00 and :10)
                        now = get_market_now()
                        if now.minute < 10 and not reports_sent.get("hourly_1h_scanned", False):
                            reports_sent["hourly_1h_scanned"] = True
                            scan_market('1h', '60d', 100)
                        elif now.minute >= 10:
                            reports_sent["hourly_1h_scanned"] = False

                        # C. 1d scanned 3x a day: Open (9:35am), Midday (1:00pm), Close (3:45pm)
                        now = get_market_now()
                        is_open_scan = (now.hour == 9 and 35 <= now.minute < 45 and not reports_sent.get("daily_open", False))
                        is_mid_scan  = (now.hour == 13 and now.minute < 15 and not reports_sent.get("daily_mid", False))
                        is_close_scan = (now.hour == 15 and 45 <= now.minute < 58 and not reports_sent.get("daily_close", False))
                        if is_open_scan:
                            reports_sent["daily_open"] = True
                            scan_market('1d', '2y', 100)
                        elif is_mid_scan:
                            reports_sent["daily_mid"] = True
                            scan_market('1d', '2y', 100)
                        elif is_close_scan:
                            reports_sent["daily_close"] = True
                            scan_market('1d', '2y', 100)

                        # D. Continuous 25-ticker rolling 5m chunk sweep via Public.com to promote boundary testers
                        batch = TICKERS[sweep_cursor:sweep_cursor + 25]
                        sweep_cursor = (sweep_cursor + 25) % max(1, len(TICKERS))
                        spy_bullish, spy_30m_ret, vix_slope = get_macro_context()
                        now = get_market_now()
                        hour_of_day = now.hour + now.minute / 60.0
                        for sym in batch:
                            try:
                                df = get_public_bars_sync(sym, '5m')
                                if not df.empty:
                                    evaluate_ticker_data(sym, df, '5m', 200, spy_bullish, hour_of_day, now, spy_30m_ret, vix_slope)
                                time.sleep(0.12)
                            except Exception:
                                pass
                        try:
                            from priority_queue import flush_priority_queue
                            flush_priority_queue()
                        except Exception:
                            pass
                        time.sleep(2.0)
                    else:
                        time.sleep(60)
                except Exception as ex:
                    print(f"[Engine 2 Sweeper Notice]: {ex}")
                    time.sleep(15)

        t = threading.Thread(target=sweeper_loop, daemon=True, name="Broad5MSweeperThread")
        t.start()

    try:
        start_broad_5m_sweeper()
    except Exception as e_sw:
        print(f"Error launching Broad 5M Sweeper: {e_sw}")

    # Immediate start of live scanner without blocking boot
    print(f"[{get_market_now().strftime('%H:%M:%S')}] Entering live market scanning loop (Engine 1: Non-Blocking 20s 5M Priority Cadence)...")
    
    while True:
        check_telegram_commands()
        
        now = get_market_now()
        # Market Hours & Pre-Market Setup Tracking:
        # Pre-Market 5M Fast Queue setup tracking runs Mon-Fri 7:00 AM - 5:00 PM EST
        is_weekday = now.weekday() < 5
        is_scanning_hours = is_weekday and (7 <= now.hour < 17)
        is_regular_market = is_weekday and ((now.hour > 9 or (now.hour == 9 and now.minute >= 30)) and (now.hour < 16))

        if is_scanning_hours:
            # Engine 1: 5m Fast Priority Queue scanned EVERY cycle (~20-25s) without ever blocking on 15m/1h/1d sweeps!
            scan_market('5m', '5d', 200)
        else:
            print(f"[{now.strftime('%H:%M:%S')}] Outside scanning hours (Mon-Fri 7:00am-5:00pm EST). Scan paused.")
        
        if TELEGRAM_ENABLED:
            try:
                from telegram_notifier import check_callbacks
                check_callbacks()
            except Exception as e:
                pass
                
        if TRACKER_ENABLED:
            try:
                # Feature 3: Auto-Sync with Public.com live portfolio
                newly_active = sync_public_positions()
                for t_id, sym in newly_active:
                    sync_msg = f"🔗 Public.com Auto-Sync: Detected live position in {sym}!\nMarked ACTIVE in Wyckoff Tracker. Exit monitoring is now live."
                    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {sync_msg}")
                    if TELEGRAM_ENABLED:
                        from telegram_notifier import send_message
                        send_message(sync_msg)

                closed = check_open_trades()
                for t in closed:
                    # Feature 1: Breakeven Stop Ratchet event
                    if t.get('is_breakeven'):
                        b_msg = f"🛡️ BREAKEVEN RATCHET: {t.get('ticker')} is up +{t.get('current_r', 0):.1f}R!\nStop Loss moved to Entry (${t.get('entry_price', 0):.2f}). Risk is now $0.00!"
                        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {b_msg}")
                        if (t.get('user_active') == 1 or t.get('telegram_alerted') == 1) and TELEGRAM_ENABLED:
                            from telegram_notifier import send_message
                            send_message(b_msg)
                        continue

                    msg = f"Trade Closed: {t.get('ticker', '?')} -> {t.get('outcome', '?')} ({t.get('pnl_r', 0):+.1f}R)"
                    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}")
                    
                    # Note: Live broker trades (user_active=1) are handled exclusively by exit_guardian
                    # which manages 5-tier scaling, breakeven stop, and the 15% uncapped runner into EOD flatten.
                    if (t.get('user_active') == 1 or t.get('telegram_alerted') == 1) and TELEGRAM_ENABLED:
                        from telegram_notifier import send_message, TELEGRAM_CHAT_ID_DAY
                        outcome_str = t.get('outcome', 'CLOSED')
                        pnl_val = t.get('pnl_r', 0) or 0.0
                        if outcome_str == 'CLOSED':
                            emoji = "🌅"
                            alert_msg = f"{emoji} EOD FLATTEN: {t.get('ticker')} closed at session end.\nReturn: {pnl_val:+.2f}R Units"
                        else:
                            emoji = "🎉" if outcome_str == 'WIN' else ("🛡️" if outcome_str == 'BREAKEVEN' else "🚨")
                            alert_msg = f"{emoji} PAPER EXIT ALERT: {t.get('ticker')} ({outcome_str})\nReturn: {pnl_val:+.2f}R Units"
                        send_message(alert_msg)
                        if TELEGRAM_CHAT_ID_DAY:
                            send_message(alert_msg, chat_id=TELEGRAM_CHAT_ID_DAY)
            except: pass
        
        now = get_market_now()
        current_date = now.date()
        
        if last_report_date != current_date:
            reports_sent = {
                "morning": False, "lunch": False, "power": False, "ai": False, "recap": False,
                "daily_open": False, "daily_mid": False, "daily_close": False, "hourly_1h_scanned": False
            }
            last_report_date = current_date
            
        if now.hour == 9 and 15 <= now.minute < 30 and not reports_sent["morning"]:
            send_market_report("Pre-Market (9:15 AM)")
            reports_sent["morning"] = True
        elif now.hour == 12 and 30 <= now.minute < 45 and not reports_sent["lunch"]:
            send_market_report("Mid-Day (12:30 PM)")
            reports_sent["lunch"] = True
        elif now.hour == 14 and 45 <= now.minute < 59 and not reports_sent["power"]:
            send_market_report("Power Hour (2:45 PM)")
            reports_sent["power"] = True
        elif now.hour == 16 and 15 <= now.minute < 30 and not reports_sent.get("ai", False):
            send_ai_progress_report()
            reports_sent["ai"] = True
        elif now.hour == 16 and 30 <= now.minute < 45 and not reports_sent.get("recap", False):
            send_daily_recap()
            reports_sent["recap"] = True
            
        # Engine 1 Cadence: 20 seconds during active scanning hours (7:00 AM - 5:00 PM EST), 300s outside
        if is_scanning_hours:
            time.sleep(20)
        else:
            time.sleep(300)
