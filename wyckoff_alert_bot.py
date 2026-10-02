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

# --- UPGRADE MODULES ---
try:
    from trade_tracker import log_alert, check_open_trades
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
MIN_R_UNITS = 1.0
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
        server.quit()
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

# ===================================================================
# HYBRID SCAN: yfinance bulk download + institutional feature tracking
# ===================================================================
def scan_market(interval, period, lookback):
    print(f"\n[{datetime.datetime.now().strftime('%H:%M:%S')}] Scanning {len(TICKERS)} tickers on {interval}...")
    
    # BULK DOWNLOAD via yfinance (fast, handles 510 tickers in one call)
    data = yf.download(TICKERS, period=period, interval=interval, group_by='ticker', progress=False)
    
    # Get SPY macro trend (cached per scan cycle)
    spy_bullish = get_spy_trend()
    now = datetime.datetime.now()
    hour_of_day = now.hour + now.minute / 60.0
    
    for ticker in TICKERS:
        try:
            df = data[ticker].dropna() if len(TICKERS) > 1 else data.dropna()
            if df.empty or len(df) < lookback:
                continue
            
            # Flatten MultiIndex if present
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
            
            # --- Institutional Features ---
            vwap = calc_vwap(highs, lows, closes, vols)
            vwap_distance = (closes[-1] - vwap[-1]) / vwap[-1] * 100 if vwap[-1] > 0 else 0
            
            atr_exp = calc_atr_expansion(highs, lows, closes)
            atr_expansion = float(atr_exp[-1])
            
            vol_sma = pd.Series(vols).rolling(20, min_periods=1).mean().values
            with np.errstate(divide='ignore', invalid='ignore'):
                rel_vol = np.where(vol_sma > 0, vols / vol_sma, 1.0)
            
            range_high = pd.Series(highs).rolling(lookback, min_periods=20).max().shift(1).values
            range_low = pd.Series(lows).rolling(lookback, min_periods=20).min().shift(1).values
            
            curr = len(df) - 1
            if pd.isna(range_high[curr]): continue
            
            c_below = (lows[curr] < range_low[curr]) or (lows[curr-1] < range_low[curr-1])
            c_above = (highs[curr] > range_high[curr]) or (highs[curr-1] > range_high[curr-1])
            vol_dry = rel_vol[curr] < VOL_LIMIT
            
            is_spring = c_below and u1[curr] and not u1[curr-1] and not u9[curr] and vol_dry
            is_utad = c_above and not u1[curr] and u1[curr-1] and u9[curr] and vol_dry
            
            current_time = time.time()
            
            if is_spring and (current_time - last_alerted.get(ticker, 0) > 900):
                flow = None
                if OPTIONS_ENABLED:
                    flow = get_options_flow(ticker)
                    if flow.get('put_call_ratio', 0) > 1.3:
                        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] BLOCKED: {ticker} LONG - P/C: {flow.get('put_call_ratio')}")
                        last_alerted[ticker] = current_time
                        continue
                
                price = closes[curr]
                sl = min(lows[curr], lows[curr-1]) * (1.0 - SL_BUFFER)
                tp = range_low[curr] + ((range_high[curr] - range_low[curr]) * 0.5)
                
                if flow and flow.get('gamma_wall', 0) > price and flow.get('gamma_wall', 0) < range_high[curr]:
                    tp = flow.get('gamma_wall', 0)
                    
                risk = abs(price - sl)
                reward = abs(tp - price)
                r_units = (reward / risk) if risk > 0 else 0
                if r_units < MIN_R_UNITS:
                    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] BLOCKED: {ticker} LONG - R-Units too low ({r_units:.2f}R)")
                    last_alerted[ticker] = current_time
                    continue
                    
                regime = "BEARISH (Seeking Reversal)" if not u9[curr] and not u14[curr] else "MIXED"
                
                # send_email_alert(ticker, "LONG (SPRING)", price, sl, tp, regime, flow, interval)
                if TRACKER_ENABLED:
                    q = get_public_quotes(ticker) if OPTIONS_ENABLED else {'bid_ask_ratio': None, 'spread_width_pct': None}
                    log_alert(ticker, "LONG", price, sl, tp, regime, interval,
                              flow.get('put_call_ratio') if flow else None,
                              flow.get('net_sentiment') if flow else None,
                              bars_in_regime,
                              vwap_distance=vwap_distance,
                              hour_of_day=hour_of_day,
                              spy_bullish=spy_bullish,
                              atr_expansion=atr_expansion,
                              bid_ask_ratio=q.get('bid_ask_ratio'),
                              spread_width_pct=q.get('spread_width_pct'),
                              implied_volatility=flow.get('atm_iv') if flow else None)
                if TELEGRAM_ENABLED: tg_trade_alert(ticker, "LONG (SPRING)", price, sl, tp, regime, interval, flow)
                last_alerted[ticker] = current_time
                
            elif is_utad and (current_time - last_alerted.get(ticker, 0) > 900):
                flow = None
                if OPTIONS_ENABLED:
                    flow = get_options_flow(ticker)
                    if flow.get('put_call_ratio', 0) < 0.7:
                        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] BLOCKED: {ticker} SHORT - P/C: {flow.get('put_call_ratio')}")
                        last_alerted[ticker] = current_time
                        continue
                        
                price = closes[curr]
                sl = max(highs[curr], highs[curr-1]) * (1.0 + SL_BUFFER)
                tp = range_high[curr] - ((range_high[curr] - range_low[curr]) * 0.5)
                
                if flow and flow.get('gamma_wall', 0) < price and flow.get('gamma_wall', 0) > range_low[curr]:
                    tp = flow.get('gamma_wall', 0)
                    
                risk = abs(sl - price)
                reward = abs(price - tp)
                r_units = (reward / risk) if risk > 0 else 0
                if r_units < MIN_R_UNITS:
                    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] BLOCKED: {ticker} SHORT - R-Units too low ({r_units:.2f}R)")
                    last_alerted[ticker] = current_time
                    continue
                    
                regime = "BULLISH (Seeking Reversal)" if u9[curr] and u14[curr] else "MIXED"
                
                # send_email_alert(ticker, "SHORT (UTAD)", price, sl, tp, regime, flow, interval)
                if TRACKER_ENABLED:
                    q = get_public_quotes(ticker) if OPTIONS_ENABLED else {'bid_ask_ratio': None, 'spread_width_pct': None}
                    log_alert(ticker, "SHORT", price, sl, tp, regime, interval,
                              flow.get('put_call_ratio') if flow else None,
                              flow.get('net_sentiment') if flow else None,
                              bars_in_regime,
                              vwap_distance=vwap_distance,
                              hour_of_day=hour_of_day,
                              spy_bullish=spy_bullish,
                              atr_expansion=atr_expansion,
                              bid_ask_ratio=q.get('bid_ask_ratio'),
                              spread_width_pct=q.get('spread_width_pct'),
                              implied_volatility=flow.get('atm_iv') if flow else None)
                if TELEGRAM_ENABLED: tg_trade_alert(ticker, "SHORT (UTAD)", price, sl, tp, regime, interval, flow)
                last_alerted[ticker] = current_time
                
        except Exception as e:
            pass

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
        server.quit()
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {session_name} REPORT SENT!")
    except Exception as e:
        print(f"Error sending report: {e}")
    
    if TELEGRAM_ENABLED and exhausted:
        try:
            tg_radar(exhausted[:5], session_name)
        except: pass

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
        server.quit()
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Daily Recap Sent!")
    except Exception as e:
        print(f"Error sending recap: {e}")

def send_ai_progress_report():
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Running Daily AI Report...")
    try:
        result = subprocess.run(["python", "wyckoff_ml_engine.py"], capture_output=True, text=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        output = result.stdout
        
        if "WHAT CAUSES WYCKOFF TRADES TO FAIL?" in output:
            ai_text = output.split("WHAT CAUSES WYCKOFF TRADES TO FAIL? (FEATURE IMPORTANCE)")[1]
        else:
            ai_text = "\n" + output
            
        body = "DAILY WYCKOFF AI PROGRESS REPORT\n"
        body += "--------------------------------------\n"
        body += "The ML model just re-trained on the latest data.\n\n"
        body += "WHAT CAUSES WYCKOFF TRADES TO FAIL?" + ai_text
        
        msg = MIMEMultipart()
        msg['From'] = GMAIL_USER
        msg['To'] = DESTINATION_EMAIL
        msg['Subject'] = "WYCKOFF AI: Daily Progress Report"
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        
        # server = smtplib.SMTP('smtp.gmail.com', 587)
        # server.starttls()()
        # server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        # server.sendmail(GMAIL_USER, DESTINATION_EMAIL, msg.as_string())
        server.quit()
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] AI Progress Report Sent!")
    except Exception as e:
        print(f"Error sending AI report: {e}")

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
        for tf in TIMEFRAMES:
            scan_market(tf['interval'], tf['period'], tf['lookback'])
            time.sleep(2)
        
        if TRACKER_ENABLED:
            try:
                closed = check_open_trades()
                for t in closed:
                    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Trade Closed: {t.get('ticker', '?')} -> {t.get('outcome', '?')} ({t.get('pnl_r', 0):+.1f}R)")
            except: pass
        
        now = datetime.datetime.now()
        current_date = now.date()
        
        if last_report_date != current_date:
            reports_sent = {"morning": False, "lunch": False, "power": False, "ai": False, "recap": False}
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
