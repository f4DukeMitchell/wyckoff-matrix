import time
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
    {"interval": "15m", "period": "20d", "lookback": 150},
    {"interval": "1h", "period": "60d", "lookback": 100},
    {"interval": "1d", "period": "2y", "lookback": 100}
]
SL_BUFFER = 0.01
MIN_R_UNITS = 1.05  # Proven Sweet-Spot Floor: Rejects negative-expectancy sub-1.0R setups
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

def get_spy_trend():
    """Get SPY macro trend using 9-period SuperTrend"""
    try:
        spy = yf.download("SPY", period="60d", interval="1h", progress=False)
        if spy.empty: return True  # default bullish
        highs = spy['High'].values.flatten()
        lows = spy['Low'].values.flatten()
        closes = spy['Close'].values.flatten()
        u9 = get_supertrend(highs, lows, closes, 9, 9.0)
        return bool(u9[-1])  # True = SPY bullish
    except:
        return True

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

def evaluate_ticker_data(ticker, df, interval, lookback, spy_bullish, hour_of_day, now):
    """
    Core Wyckoff detection and trade evaluation on a ticker DataFrame.
    Shared by both Public.com streaming scan and fallback bulk scans.
    """
    try:
        if df.empty or len(df) < lookback:
            return
            
        if isinstance(df.columns, pd.MultiIndex):
            highs = df['High'].iloc[:, 0].values.astype(float)
            lows = df['Low'].iloc[:, 0].values.astype(float)
            closes = df['Close'].iloc[:, 0].values.astype(float)
            vols = df['Volume'].iloc[:, 0].values.astype(float)
        else:
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
        
        range_high = pd.Series(highs).rolling(lookback, min_periods=20).max().shift(1).values
        range_low = pd.Series(lows).rolling(lookback, min_periods=20).min().shift(1).values
        
        if pd.isna(range_high[curr]): return
        
        c_below = (lows[curr] < range_low[curr]) or (lows[curr-1] < range_low[curr-1])
        c_above = (highs[curr] > range_high[curr]) or (highs[curr-1] > range_high[curr-1])
        vol_dry = rel_vol[curr] < VOL_LIMIT
        
        is_spring = c_below and u1[curr] and not u1[curr-1] and not u9[curr] and vol_dry
        is_utad = c_above and not u1[curr] and u1[curr-1] and u9[curr] and vol_dry
        
        current_time = time.time()
        
        if is_spring and (current_time - last_alerted.get(ticker, 0) > 900):
            if TRACKER_ENABLED and (has_open_alerted_trade(ticker, interval) or has_open_trade(ticker, interval)):
                print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] BLOCKED DUPLICATE: {ticker} already has active open trade on {interval}")
                last_alerted[ticker] = current_time
                return

            if now.hour == 9 and 30 <= now.minute < 45 and interval == "5m":
                print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] OPENING SHIELD: {ticker} 5m Spring suppressed during 9:30-9:45 AM")
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

            regime = "BEARISH (Seeking Reversal)" if not u9[curr] and not u14[curr] else "MIXED"
            
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
                    effort_vs_result=effort_vs_result
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
                                              institutional_block_ratio=inst.get('institutional_block_ratio', 1.0)) if TRACKER_ENABLED else None

            if r_units < MIN_R_UNITS:
                print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] FILTERED: {ticker} LONG - R-Units too low ({r_units:.2f}R < {MIN_R_UNITS}R)")
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
                          optimal_target_r=opt_target_r)
            
            if TELEGRAM_ENABLED:
                tg_trade_alert(ticker, "LONG (SPRING)", price, sl, tp, regime, interval, flow, trade_id, ml_confidence=ml_conf)
                if TRACKER_ENABLED and trade_id: mark_trade_alerted(trade_id)
            last_alerted[ticker] = current_time
            
        elif is_utad and (current_time - last_alerted.get(ticker, 0) > 900):
            if TRACKER_ENABLED and (has_open_alerted_trade(ticker, interval) or has_open_trade(ticker, interval)):
                print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] BLOCKED DUPLICATE: {ticker} already has active open trade on {interval}")
                last_alerted[ticker] = current_time
                return

            if now.hour == 9 and 30 <= now.minute < 45 and interval == "5m":
                print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] OPENING SHIELD: {ticker} 5m UTAD suppressed during 9:30-9:45 AM")
                return

            flow = None
            if OPTIONS_ENABLED:
                flow = get_options_flow(ticker)
                if flow.get('put_call_ratio', 0) < 0.7:
                    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] BLOCKED: {ticker} SHORT - P/C: {flow.get('put_call_ratio')}")
                    last_alerted[ticker] = current_time
                    return
                    
            price = closes[curr]
            sl = max(highs[curr], highs[curr-1]) * (1.0 + SL_BUFFER)
            risk = abs(sl - price)
            if risk <= 0.001:
                return

            regime = "BULLISH (Seeking Reversal)" if u9[curr] and u14[curr] else "MIXED"
            
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
                    effort_vs_result=effort_vs_result
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
                                              institutional_block_ratio=inst.get('institutional_block_ratio', 1.0)) if TRACKER_ENABLED else None

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
                          optimal_target_r=opt_target_r)
            
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
    print(f"\n[{get_market_now().strftime('%H:%M:%S')}] [PUBLIC.COM LIVE] Streaming {len(TICKERS)} tickers on {interval}...")
    spy_bullish = await get_spy_trend_public()
    now = get_market_now()
    hour_of_day = now.hour + now.minute / 60.0
    
    count = 0
    async for ticker, df in stream_ticker_bars(TICKERS, interval=interval, delay_ms=0.08):
        count += 1
        evaluate_ticker_data(ticker, df, interval, lookback, spy_bullish, hour_of_day, now)
    print(f"[{get_market_now().strftime('%H:%M:%S')}] [PUBLIC.COM LIVE] Finished {count} tickers on {interval}.")

# Fallback bulk scanner (yfinance)
def scan_market(interval, period, lookback):
    if PUBLIC_DATA_ENABLED:
        import asyncio
        asyncio.run(scan_market_public(interval, lookback))
        return
        
    print(f"\n[{get_market_now().strftime('%H:%M:%S')}] [FALLBACK YFINANCE] Scanning {len(TICKERS)} tickers on {interval}...")
    data = yf.download(TICKERS, period=period, interval=interval, group_by='ticker', progress=False)
    spy_bullish = get_spy_trend()
    now = get_market_now()
    hour_of_day = now.hour + now.minute / 60.0
    for ticker in TICKERS:
        try:
            df = data[ticker].dropna() if len(TICKERS) > 1 else data.dropna()
            evaluate_ticker_data(ticker, df, interval, lookback, spy_bullish, hour_of_day, now)
        except: pass


last_report_date = None
reports_sent = {"morning": False, "lunch": False, "power": False}

def send_market_report(session_name):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Generating {session_name} Report...")
    data = yf.download(TICKERS, period="10d", interval="1h", group_by='ticker', progress=False)
    
    exhausted = []
    for ticker in TICKERS:
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
    data = yf.download(TICKERS, period="10d", interval="1h", group_by='ticker', progress=False)
    
    exhausted = []
    for ticker in TICKERS:
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
    send_market_report("On-Demand")
    
    while True:
        check_telegram_commands()
        
        now = get_market_now()
        # Market Hours check (Mon-Fri 9:30 AM - 4:00 PM EST)
        is_weekday = now.weekday() < 5
        market_open = (now.hour > 9 or (now.hour == 9 and now.minute >= 30)) and (now.hour < 16)
        is_market_hours = is_weekday and market_open

        if is_market_hours:
            # 1. 5m and 15m scanned EVERY cycle (real-time broker stream)
            scan_market('5m', '5d', 200)
            scan_market('15m', '20d', 150)

            # 2. 1h scanned at the top of each hour (between :00 and :10)
            if now.minute < 10 and not reports_sent.get("hourly_1h_scanned", False):
                scan_market('1h', '60d', 100)
                reports_sent["hourly_1h_scanned"] = True
            elif now.minute >= 10:
                reports_sent["hourly_1h_scanned"] = False

            # 3. 1d scanned 3x a day: Open (9:35am), Midday (1:00pm), Close (3:45pm)
            is_open_scan = (now.hour == 9 and 35 <= now.minute < 45 and not reports_sent.get("daily_open", False))
            is_mid_scan  = (now.hour == 13 and now.minute < 15 and not reports_sent.get("daily_mid", False))
            is_close_scan = (now.hour == 15 and 45 <= now.minute < 58 and not reports_sent.get("daily_close", False))

            if is_open_scan:
                scan_market('1d', '2y', 100)
                reports_sent["daily_open"] = True
            elif is_mid_scan:
                scan_market('1d', '2y', 100)
                reports_sent["daily_mid"] = True
            elif is_close_scan:
                scan_market('1d', '2y', 100)
                reports_sent["daily_close"] = True
        else:
            print(f"[{now.strftime('%H:%M:%S')}] Outside market hours (Mon-Fri 9:30am-4:00pm EST). Scan paused.")
        
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
                    
                    # Auto-execute broker exit if user was active in this trade
                    broker_exit_info = ""
                    if t.get('user_active') == 1:
                        try:
                            from public_executor import execute_exit_sell
                            sym = t.get('ticker')
                            direction = t.get('direction')
                            sell_res = execute_exit_sell(sym, direction=direction)
                            if sell_res.get('status') == 'SUBMITTED':
                                action = "Covered" if direction == 'SHORT' else "Sold"
                                broker_exit_info = f"\n🔄 Public.com Broker Exit Order Sent: {action} {sell_res.get('quantity', 'all')} shares."
                        except Exception as e:
                            broker_exit_info = f"\n⚠️ Broker Exit Error: {e}"

                    if (t.get('user_active') == 1 or t.get('telegram_alerted') == 1) and TELEGRAM_ENABLED:
                        from telegram_notifier import send_message
                        emoji = "🎉" if t.get('outcome') == 'WIN' else ("🛡️" if t.get('outcome') == 'BREAKEVEN' else "🚨")
                        alert_msg = f"{emoji} EXIT ALERT: {t.get('ticker')} has hit its {t.get('outcome')} target!\nReturn: {t.get('pnl_r', 0):+.1f}R Units{broker_exit_info}"
                        send_message(alert_msg)
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
            
        time.sleep(300)
