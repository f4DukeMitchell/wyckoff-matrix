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
    from options_flow import get_options_flow
    OPTIONS_ENABLED = True
except:
    OPTIONS_ENABLED = False

# ==========================================
# ðŸ›‘ USER CONFIGURATION REQUIRED ðŸ›‘
# ==========================================
GMAIL_USER = "f4dukemitchell@gmail.com"           # <-- Replace with your Gmail address
GMAIL_APP_PASSWORD = "aakv dgpp wfwx nhua"      # <-- Replace with your 16-character App Password
DESTINATION_EMAIL = "matt.smith@pga.com"    # <-- Where you want the alerts sent (can be the same as above)

import json
import os

try:
    with open("all_tickers.json", "r") as f:
        TICKERS = json.load(f)
except:
    TICKERS = ["AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "BRK-B", "LLY", "AVGO", "JPM", "V"]
INTERVAL = "5m"
PERIOD = "5d"
LOOKBACK = 200        # Intraday Phase B lookback
SL_BUFFER = 0.01      # 1% stop loss buffer for intraday volatility
VOL_LIMIT = 1.2       # Intraday volume threshold
# ==========================================

# Dictionary to prevent spamming the same alert multiple times in a row
last_alerted = {ticker: 0 for ticker in TICKERS}

def send_email_alert(ticker, action, price, sl, tp, regime, options_flow=None):
    subject = f"WYCKOFF ALERT: {action} on {ticker}"
    body = f"""
    Wyckoff Institutional Terminal Alert
    ------------------------------------
    TICKER: {ticker} ({INTERVAL})
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
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        text = msg.as_string()
        server.sendmail(GMAIL_USER, DESTINATION_EMAIL, text)
        server.quit()
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] âœ‰ï¸ EMAIL SENT: {action} on {ticker}")
    except Exception as e:
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] âŒ ERROR SENDING EMAIL. Did you enter your 16-character App Password correctly? Error: {e}")

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

def scan_market():
    print(f"\n[{datetime.datetime.now().strftime('%H:%M:%S')}] ðŸ“¡ Scanning {len(TICKERS)} tickers for Phase C exhaustion...")
    
    # Bulk download is faster and prevents rate limits
    data = yf.download(TICKERS, period=PERIOD, interval=INTERVAL, group_by='ticker', progress=False)
    
    for ticker in TICKERS:
        try:
            df = data[ticker].dropna() if len(TICKERS) > 1 else data.dropna()
            if df.empty or len(df) < LOOKBACK:
                continue
                
            highs = df['High'].values
            lows = df['Low'].values
            closes = df['Close'].values
            vols = df['Volume'].values
            
            u1 = get_supertrend(highs, lows, closes, 1, 1.0)
            u9 = get_supertrend(highs, lows, closes, 9, 9.0)
            u14 = get_supertrend(highs, lows, closes, 14, 14.0)
            
            vol_sma = pd.Series(vols).rolling(20, min_periods=1).mean().values
            rel_vol = np.where(vol_sma > 0, vols / vol_sma, 1.0)
            
            range_high = pd.Series(highs).rolling(LOOKBACK, min_periods=20).max().shift(1).values
            range_low = pd.Series(lows).rolling(LOOKBACK, min_periods=20).min().shift(1).values
            
            curr = len(df) - 1
            if pd.isna(range_high[curr]): continue
            
            c_below = (lows[curr] < range_low[curr]) or (lows[curr-1] < range_low[curr-1])
            c_above = (highs[curr] > range_high[curr]) or (highs[curr-1] > range_high[curr-1])
            vol_dry = rel_vol[curr] < VOL_LIMIT
            
            is_spring = c_below and u1[curr] and not u1[curr-1] and not u9[curr] and vol_dry
            is_utad = c_above and not u1[curr] and u1[curr-1] and u9[curr] and vol_dry
            
            current_time = time.time()
            
                        # If signal fired AND we haven't alerted this ticker in the last 15 minutes (900 seconds)
            if is_spring and (current_time - last_alerted[ticker] > 900):
                flow = None
                if OPTIONS_ENABLED:
                    flow = get_options_flow(ticker)
                    if flow.get('put_call_ratio', 0) > 1.3:
                        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] BLOCKED: {ticker} LONG (SPRING) - Options flow is heavily BEARISH (P/C: {flow.get('put_call_ratio')})")
                        last_alerted[ticker] = current_time
                        continue
                
                price = closes[curr]
                sl = min(lows[curr], lows[curr-1]) * (1.0 - SL_BUFFER)
                tp = range_low[curr] + ((range_high[curr] - range_low[curr]) * 0.5)
                
                if flow and flow.get('gamma_wall', 0) > price and flow.get('gamma_wall', 0) < range_high[curr]:
                    tp = flow.get('gamma_wall', 0)
                    
                regime = "BEARISH (Seeking Reversal)" if not u9[curr] and not u14[curr] else "MIXED"
                
                send_email_alert(ticker, "LONG (SPRING)", price, sl, tp, regime, flow)
                if TRACKER_ENABLED: log_alert(ticker, "LONG", price, sl, tp, regime, flow.get('put_call_ratio') if flow else None, flow.get('net_sentiment') if flow else None)
                if TELEGRAM_ENABLED: tg_trade_alert(ticker, "LONG (SPRING)", price, sl, tp, regime, flow)
                last_alerted[ticker] = current_time
                
            elif is_utad and (current_time - last_alerted[ticker] > 900):
                flow = None
                if OPTIONS_ENABLED:
                    flow = get_options_flow(ticker)
                    if flow.get('put_call_ratio', 0) < 0.7:
                        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] BLOCKED: {ticker} SHORT (UTAD) - Options flow is heavily BULLISH (P/C: {flow.get('put_call_ratio')})")
                        last_alerted[ticker] = current_time
                        continue
                        
                price = closes[curr]
                sl = max(highs[curr], highs[curr-1]) * (1.0 + SL_BUFFER)
                tp = range_high[curr] - ((range_high[curr] - range_low[curr]) * 0.5)
                
                if flow and flow.get('gamma_wall', 0) < price and flow.get('gamma_wall', 0) > range_low[curr]:
                    tp = flow.get('gamma_wall', 0)
                    
                regime = "BULLISH (Seeking Reversal)" if u9[curr] and u14[curr] else "MIXED"
                
                send_email_alert(ticker, "SHORT (UTAD)", price, sl, tp, regime, flow)
                if TRACKER_ENABLED: log_alert(ticker, "SHORT", price, sl, tp, regime, flow.get('put_call_ratio') if flow else None, flow.get('net_sentiment') if flow else None)
                if TELEGRAM_ENABLED: tg_trade_alert(ticker, "SHORT (UTAD)", price, sl, tp, regime, flow)
                last_alerted[ticker] = current_time
                
        except Exception as e:
            pass # Silently skip errors on individual tickers to keep the loop alive

last_report_date = None
reports_sent = {"morning": False, "lunch": False, "power": False}

def send_market_report(session_name):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ðŸ“ Generating {session_name} Report...")
    data = yf.download(TICKERS, period="10d", interval=INTERVAL, group_by='ticker', progress=False)
    
    exhausted = []
    for ticker in TICKERS:
        try:
            df = data[ticker].dropna() if len(TICKERS) > 1 else data.dropna()
            if df.empty or len(df) < LOOKBACK: continue
            
            highs, lows, closes = df['High'].values, df['Low'].values, df['Close'].values
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
    
    body = f"ðŸ“Š WYCKOFF MARKET RADAR: {session_name}\n"
    body += "------------------------------------------------------\n"
    body += "Here are the most structurally exhausted stocks to stalk right now:\n\n"
    
    if not exhausted:
        body += "No stocks are currently showing significant exhaustion (>= 20 bars).\n"
    else:
        for t in exhausted[:5]:
            body += f"- {t[0]}: {t[1]} Regime ({t[2]} bars exhausted). Stalk for {t[3]}.\n"
            
    body += "\nReminder: Wait for the Micro Spark (1,1) to confirm the entry!\n"
    
    msg = MIMEMultipart()
    msg['From'] = GMAIL_USER
    msg['To'] = DESTINATION_EMAIL
    msg['Subject'] = f"ðŸ“Š WYCKOFF RADAR: {session_name} Update"
    msg.attach(MIMEText(body, 'plain', 'utf-8'))
    
    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, DESTINATION_EMAIL, msg.as_string())
        server.quit()
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] âœ‰ï¸ {session_name} REPORT SENT!")
    except Exception as e:
        print(f"Error sending report: {e}")

def send_daily_recap():
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Generating Daily Recap & Tomorrow's Watchlist...")
    data = yf.download(TICKERS, period="10d", interval=INTERVAL, group_by='ticker', progress=False)
    
    exhausted = []
    for ticker in TICKERS:
        try:
            df = data[ticker].dropna() if len(TICKERS) > 1 else data.dropna()
            if df.empty or len(df) < LOOKBACK: continue
            
            highs, lows, closes = df['High'].values, df['Low'].values, df['Close'].values
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
    body += "The market has closed. Here is your Wyckoff alignment for tomorrow's open:\n\n"
    
    if not exhausted:
        body += "No major structural exhaustion setups identified for tomorrow yet. The market is mixed.\n"
    else:
        body += "TOP COILED SETUPS TO WATCH AT TOMORROW'S OPEN:\n"
        for i, t in enumerate(exhausted[:5]):
            body += f"{i+1}. {t[0]} - {t[2]} bars deep in a {t[1]} regime. Stalking for a {t[3]}.\n"
            
    body += "\nGAME PLAN: Do not front-run! Wait for the Micro Supertrend to flash green/red as confirmation of the trap before entering.\n"
    
    msg = MIMEMultipart()
    msg['From'] = GMAIL_USER
    msg['To'] = DESTINATION_EMAIL
    msg['Subject'] = "WYCKOFF: Daily Recap & Tomorrow's Watchlist"
    msg.attach(MIMEText(body, 'plain', 'utf-8'))
    
    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, DESTINATION_EMAIL, msg.as_string())
        server.quit()
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Daily Recap Sent!")
    except Exception as e:
        print(f"Error sending recap: {e}")

def send_ai_progress_report():
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Running Daily AI Report via Subprocess...")
    try:
        result = subprocess.run(["python", "wyckoff_ml_engine.py"], capture_output=True, text=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        output = result.stdout
        
        if "WHAT CAUSES WYCKOFF TRADES TO FAIL?" in output:
            ai_text = output.split("WHAT CAUSES WYCKOFF TRADES TO FAIL? (FEATURE IMPORTANCE)")[1]
        else:
            ai_text = "\n" + output
            
        body = "DAILY WYCKOFF AI PROGRESS REPORT\n"
        body += "--------------------------------------\n"
        body += "The Machine Learning model just re-trained itself on the latest 60 days of market data.\n\n"
        body += "WHAT CAUSES WYCKOFF TRADES TO FAIL?" + ai_text
        
        msg = MIMEMultipart()
        msg['From'] = GMAIL_USER
        msg['To'] = DESTINATION_EMAIL
        msg['Subject'] = "WYCKOFF AI: Daily Progress Report"
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, DESTINATION_EMAIL, msg.as_string())
        server.quit()
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] AI Progress Report Sent!")
    except Exception as e:
        print(f"Error sending AI report: {e}")

if __name__ == "__main__":
    print("========================================")
    print("ðŸ¦… WYCKOFF LIVE ALERT BOT INITIALIZED ðŸ¦…")
    print("========================================")
    print(f"Targeting: {len(TICKERS)} Mega-Cap Stocks")
    print(f"Interval: {INTERVAL}")
    print("Bot is now running in the background. Press Ctrl+C to stop.\n")
    
    while True:
        scan_market()
        
        # Check if any open trades have hit TP or SL
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
            
        # 9:15 AM
        if now.hour == 9 and 15 <= now.minute < 30 and not reports_sent["morning"]:
            send_market_report("Pre-Market (9:15 AM)")
            reports_sent["morning"] = True
        # 12:30 PM
        elif now.hour == 12 and 30 <= now.minute < 45 and not reports_sent["lunch"]:
            send_market_report("Mid-Day (12:30 PM)")
            reports_sent["lunch"] = True
        # 2:45 PM
        elif now.hour == 14 and 45 <= now.minute < 59 and not reports_sent["power"]:
            send_market_report("Power Hour (2:45 PM)")
            reports_sent["power"] = True
        # 4:15 PM (Post-Market AI Training)
        elif now.hour == 16 and 15 <= now.minute < 30 and not reports_sent.get("ai", False):
            send_ai_progress_report()
            reports_sent["ai"] = True
        # 4:30 PM (Daily Recap & Tomorrow's Watchlist)
        elif now.hour == 16 and 30 <= now.minute < 45 and not reports_sent.get("recap", False):
            send_daily_recap()
            reports_sent["recap"] = True
            
        time.sleep(300)


