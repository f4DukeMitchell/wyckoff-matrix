import os
import sqlite3
import datetime
try:
    import zoneinfo
    ET_TZ = zoneinfo.ZoneInfo("America/New_York")
except Exception:
    ET_TZ = None

from public_executor import execute_dollar_buy, calculate_test_allocation
from telegram_notifier import send_message

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wyckoff_trades.db")

# --- AUTONOMOUS BUY SETTINGS ---
AUTO_BUY_ENABLED = os.getenv("AUTO_BUY_ENABLED", "true").lower() in ("true", "1", "yes")
AUTO_BUY_ALLOC_MODE = os.getenv("AUTO_BUY_ALLOC_MODE", "FIXED_TEST")  # "FIXED_TEST" ($20) or "PERCENT_1PCT" (~$20.88)
AUTO_BUY_FIXED_AMOUNT = float(os.getenv("AUTO_BUY_FIXED_AMOUNT", "20.00"))
AUTO_BUY_MAX_CONCURRENT = int(os.getenv("AUTO_BUY_MAX_CONCURRENT", "5"))
AUTO_BUY_MIN_ML_CONF = float(os.getenv("AUTO_BUY_MIN_ML_CONF", "60.0"))

# Optimal Statistical Window: 10:00 AM to 3:30 PM EST (Skip 9:30-10:00 AM opening whipsaw & 12:30-1:30 PM lunch chop)
AUTO_BUY_START_TIME = datetime.time(10, 0)
AUTO_BUY_END_TIME = datetime.time(15, 30)
LUNCH_START_TIME = datetime.time(12, 30)
LUNCH_END_TIME = datetime.time(13, 30)

def get_market_time():
    if ET_TZ:
        return datetime.datetime.now(ET_TZ)
    return datetime.datetime.now()

def check_auto_buy_eligibility(ticker, ml_conf):
    """
    Evaluates whether an incoming Wyckoff Spring signal satisfies all institutional guardrails.
    Returns (eligible: bool, reason: str)
    """
    if not AUTO_BUY_ENABLED:
        return False, "AUTO_BUY_DISABLED"

    now = get_market_time()
    current_t = now.time()

    # 1. Opening Whipsaw Gate (Must be >= 10:00 AM EST)
    if current_t < AUTO_BUY_START_TIME:
        return False, f"WAITING_FOR_10AM_CONFIRMATION (Current: {current_t.strftime('%H:%M:%S')})"

    # 2. End-of-Day Cutoff (No entries after 3:30 PM EST ahead of 3:55 PM flatten)
    if current_t > AUTO_BUY_END_TIME:
        return False, f"AFTER_3:30PM_CUTOFF (Current: {current_t.strftime('%H:%M:%S')})"

    # 3. Lunch Lull Suppressor (12:30 PM - 1:30 PM EST has 6.2% win rate)
    if LUNCH_START_TIME <= current_t <= LUNCH_END_TIME:
        return False, "LUNCH_LULL_SUPPRESSION (12:30-1:30 PM EST)"

    # 4. ML Confidence Gate
    if ml_conf is not None and ml_conf < AUTO_BUY_MIN_ML_CONF:
        return False, f"ML_CONF_TOO_LOW ({ml_conf:.1f}% < {AUTO_BUY_MIN_ML_CONF}%)"

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # 5. Max Concurrent Active Positions (Max 5)
    c.execute("SELECT COUNT(id) FROM alerts WHERE outcome = 'OPEN' AND user_active = 1")
    active_count = (c.fetchone() or (0,))[0]
    if active_count >= AUTO_BUY_MAX_CONCURRENT:
        conn.close()
        return False, f"MAX_POSITIONS_REACHED ({active_count}/{AUTO_BUY_MAX_CONCURRENT})"

    # 6. No Duplicate Tickers in same trading session
    today_str = now.strftime("%Y-%m-%d")
    c.execute("""
        SELECT COUNT(id) FROM alerts 
        WHERE ticker = ? AND user_active = 1 AND timestamp LIKE ?
    """, (ticker.upper(), f"{today_str}%"))
    same_ticker_count = (c.fetchone() or (0,))[0]
    conn.close()

    if same_ticker_count > 0:
        return False, f"ALREADY_ACTIVE_TODAY ({ticker.upper()})"

    return True, "ELIGIBLE"

def execute_autonomous_spring_buy(trade_id, ticker, price, sl, tp, ml_conf, timeframe="5m"):
    """
    Submits a market fractional BUY to Public.com, marks the trade active in DB,
    and dispatches instant notifications.
    """
    eligible, reason = check_auto_buy_eligibility(ticker, ml_conf)
    if not eligible:
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Auto-Buy Bypassed for {ticker}: {reason}")
        return False, reason

    # Determine allocation
    if AUTO_BUY_ALLOC_MODE == "PERCENT_1PCT":
        dollar_alloc = calculate_test_allocation(0.01)
    else:
        dollar_alloc = AUTO_BUY_FIXED_AMOUNT

    try:
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ⚡ EXECUTING AUTONOMOUS BUY: {ticker} (${dollar_alloc:.2f}) on Public.com...")
        order_res = execute_dollar_buy(ticker.upper(), dollar_alloc)
        
        # Mark active in database
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE alerts SET user_active = 1 WHERE id = ?", (trade_id,))
        conn.commit()
        conn.close()

        status = order_res.get('status', 'SUBMITTED')
        order_uuid = order_res.get('order_id', 'N/A')

        # Telegram Alert
        conf_str = f" | ML Edge: {ml_conf:.1f}%" if ml_conf is not None else ""
        msg = (
            f"⚡ AUTONOMOUS BUY EXECUTED!\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Symbol: {ticker.upper()} (LONG)\n"
            f"Timeframe: {timeframe}\n"
            f"Amount: ${dollar_alloc:.2f} ({status})\n"
            f"Entry: ${price:.2f}\n"
            f"Initial Stop: ${sl:.2f}\n"
            f"Target: ${tp:.2f}\n"
            f"Order UUID: {order_uuid}{conf_str}\n\n"
            f"🛡️ Exit Guardian 6-Tier Matrix LIVE:\n"
            f"• +0.20R: Bank 10% (Stop stays -1.0R to breathe)\n"
            f"• +0.40R: Bank 10% (Stop trails -0.50R)\n"
            f"• +0.65R: Bank 10% (Stop to Entry $0.00 Breakeven)\n"
            f"• +1.00R: Bank 25% (Stop to +0.50R guaranteed win)\n"
            f"• +1.30R: Bank 25% (Stop to +0.85R sweet spot)\n"
            f"• 20% Runner trails 0.25R into 3:55 PM EST Flatten"
        )
        send_message(msg)
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ✅ Auto-Buy Success: {ticker} (${dollar_alloc:.2f})")
        return True, "EXECUTED"
    except Exception as e:
        err_msg = f"❌ Autonomous Buy FAILED for {ticker}: {str(e)}"
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {err_msg}")
        send_message(err_msg)
        return False, str(e)
