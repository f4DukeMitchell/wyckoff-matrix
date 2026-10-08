import os
import time
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

# Allowed Auto-Buy Timeframes: Strictly 5m Day Trades Only (No swings or higher TF holds)
AUTO_BUY_ALLOWED_TIMEFRAMES = ["5m"]

# Optimal Statistical Window: 10:00 AM to 3:30 PM EST (Skip 9:30-10:00 AM opening whipsaw & 12:30-1:30 PM lunch chop)
AUTO_BUY_START_TIME = datetime.time(10, 0)
AUTO_BUY_END_TIME = datetime.time(15, 30)
LUNCH_START_TIME = datetime.time(12, 30)
LUNCH_END_TIME = datetime.time(13, 30)

def get_market_time():
    if ET_TZ:
        return datetime.datetime.now(ET_TZ)
    return datetime.datetime.now()

def get_open_trade_for_second_spring(ticker, current_price):
    """
    Checks if an existing open trade on ticker is eligible for a 2X Second Spring add.
    Conditions:
    1. Active open trade in DB (outcome = 'OPEN', user_active = 1)
    2. second_spring_added == 0 (strictly max 1 addition, capping at 2x sizing)
    3. partial_tier_done == 0 (still in base accumulation, hasn't started scaling out)
    4. current_price > initial_stop_loss (structural invalidation line respected)
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("""
            SELECT id, ticker, entry_price, initial_stop_loss, stop_loss, initial_shares, 
                   partial_tier_done, second_spring_added, take_profit
            FROM alerts
            WHERE ticker = ? AND outcome = 'OPEN' AND user_active = 1
            ORDER BY id DESC LIMIT 1
        """, (ticker.upper(),))
        row = c.fetchone()
        conn.close()
        if not row:
            return None
        
        trade = dict(row)
        init_sl = float(trade.get('initial_stop_loss') or trade.get('stop_loss') or 0.0)
        tier = int(trade.get('partial_tier_done') or 0)
        already_added = int(trade.get('second_spring_added') or 0)

        if already_added == 0 and tier == 0 and current_price > init_sl:
            return trade
        return None
    except Exception as e:
        print(f"Error checking second spring candidate: {e}")
        return None

def check_auto_buy_eligibility(ticker, ml_conf, timeframe="5m"):
    """
    Evaluates whether an incoming Wyckoff Spring signal satisfies all institutional guardrails.
    Returns (eligible: bool, reason: str)
    """
    if not AUTO_BUY_ENABLED:
        return False, "AUTO_BUY_DISABLED"

    # 0. Timeframe Guardrail (Strictly 5m day trades only; exclude 15m, 1h, 1d swings)
    if (timeframe or "").lower() not in AUTO_BUY_ALLOWED_TIMEFRAMES:
        return False, f"TIMEFRAME_RESTRICTED ({timeframe} != 5m day trade)"

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

    # 6. Check for active open trade vs clean re-entry
    c.execute("""
        SELECT id FROM alerts 
        WHERE ticker = ? AND outcome = 'OPEN' AND user_active = 1
    """, (ticker.upper(),))
    open_trade = c.fetchone()
    if open_trade:
        conn.close()
        return False, f"OPEN_TRADE_EXISTS (#{open_trade[0]})"

    # Clean Re-entry Guardrail: Check daily loss limit on this ticker
    # If ticker took 2 full stop-outs today, suppress to avoid whipsaw chop
    today_str = now.strftime("%Y-%m-%d")
    c.execute("""
        SELECT COUNT(id) FROM alerts
        WHERE ticker = ? AND user_active = 1 AND timestamp LIKE ?
          AND outcome IN ('STOPPED', 'LOSS') AND (pnl_r <= -0.5 OR exit_price < entry_price)
    """, (ticker.upper(), f"{today_str}%"))
    losses_today = (c.fetchone() or (0,))[0]
    conn.close()

    if losses_today >= 2:
        return False, f"DAILY_LOSS_LIMIT_REACHED ({ticker.upper()} has {losses_today} losses today)"

    return True, "ELIGIBLE"

def execute_autonomous_second_spring_buy(parent_trade_id, ticker, price, ml_conf, timeframe="5m"):
    """
    Executes a high-conviction 2X Second Spring (Secondary Test) addition:
    1. Verifies timing & ML confidence guardrails.
    2. Places fractional 1% BUY on Public.com to double the position to 2%.
    3. Blends entry price and synchronizes initial_shares.
    4. UNIFIED STRUCTURAL STOP: Strictly preserves original initial_stop_loss.
    5. Dispatches Telegram notification.
    """
    if not AUTO_BUY_ENABLED:
        return False, "AUTO_BUY_DISABLED"

    if (timeframe or "").lower() not in AUTO_BUY_ALLOWED_TIMEFRAMES:
        return False, f"TIMEFRAME_RESTRICTED ({timeframe})"

    now = get_market_time()
    current_t = now.time()

    if current_t < AUTO_BUY_START_TIME:
        return False, "WAITING_FOR_10AM_CONFIRMATION"

    if current_t > AUTO_BUY_END_TIME:
        return False, "AFTER_3:30PM_CUTOFF"

    if LUNCH_START_TIME <= current_t <= LUNCH_END_TIME:
        return False, "LUNCH_LULL_SUPPRESSION"

    if ml_conf is not None and ml_conf < AUTO_BUY_MIN_ML_CONF:
        return False, f"ML_CONF_TOO_LOW ({ml_conf:.1f}% < {AUTO_BUY_MIN_ML_CONF}%)"

    # Fetch parent trade
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT id, ticker, entry_price, initial_stop_loss, stop_loss, initial_shares, 
               partial_tier_done, second_spring_added
        FROM alerts WHERE id = ?
    """, (parent_trade_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return False, "PARENT_TRADE_NOT_FOUND"

    trade = dict(row)
    if int(trade.get('second_spring_added') or 0) > 0:
        conn.close()
        return False, "SECOND_SPRING_ALREADY_ADDED"

    structural_stop = float(trade.get('initial_stop_loss') or trade.get('stop_loss') or 0.0)
    old_entry = float(trade.get('entry_price') or price)
    old_shares = float(trade.get('initial_shares') or 0.0)

    # Determine allocation (standard 1% tranche to 2X position)
    if AUTO_BUY_ALLOC_MODE == "PERCENT_1PCT":
        dollar_alloc = calculate_test_allocation(0.01)
    else:
        dollar_alloc = AUTO_BUY_FIXED_AMOUNT

    try:
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ⚡ EXECUTING 2X SECOND SPRING BUY: {ticker} (${dollar_alloc:.2f}) on Public.com...")
        order_res = execute_dollar_buy(ticker.upper(), dollar_alloc)
        status = order_res.get('status', 'SUBMITTED')
        order_uuid = order_res.get('order_id', 'N/A')

        # Allow broker 1.5s to settle fill
        time.sleep(1.5)

        # Sync live position from broker
        new_total_shares = old_shares + (dollar_alloc / price)
        blended_entry = old_entry
        try:
            from public_executor import get_live_positions
            broker_pos = get_live_positions()
            for p in broker_pos:
                if p['ticker'].upper() == ticker.upper():
                    new_total_shares = float(p['quantity'])
                    blended_entry = float(p['entry_price'])
                    break
        except Exception as pe:
            print(f"Error fetching live position post-buy: {pe}")
            # Fallback calculation
            add_shares = dollar_alloc / price
            new_total_shares = old_shares + add_shares
            blended_entry = ((old_entry * old_shares) + dollar_alloc) / new_total_shares if new_total_shares > 0 else price

        # Update parent record in DB:
        # Note: initial_stop_loss and stop_loss are strictly KEPT at structural_stop!
        c.execute("""
            UPDATE alerts SET 
                second_spring_added = 1,
                initial_shares = ?,
                entry_price = ?,
                stop_loss = ?,
                initial_stop_loss = ?
            WHERE id = ?
        """, (round(new_total_shares, 5), round(blended_entry, 4), structural_stop, structural_stop, parent_trade_id))
        conn.commit()
        conn.close()

        # Telegram Alert
        conf_str = f" | ML Edge: {ml_conf:.1f}%" if ml_conf is not None else ""
        msg = (
            f"⚡ 2X SECOND SPRING PYRAMID EXECUTED!\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Symbol: {ticker.upper()} (Tranche #2 Added)\n"
            f"Timeframe: {timeframe}\n"
            f"Added Capital: ${dollar_alloc:.2f} ({status})\n"
            f"New Blended Entry: ${blended_entry:.2f}\n"
            f"Total Shares: {new_total_shares:.4f} (2X Position)\n"
            f"🔒 Unified Structural Stop: ${structural_stop:.2f} (LOCKED - Untouched!)\n"
            f"Order UUID: {order_uuid}{conf_str}\n\n"
            f"🛡️ Exit Guardian Synchronized:\n"
            f"• Both tranches share the exact same invalidation line.\n"
            f"• Sizing doubled to 2% max allocation.\n"
            f"• 10% partial ladder scaled to bank double cash at targets!"
        )
        send_message(msg)
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ✅ 2X Second Spring Success: {ticker} (${dollar_alloc:.2f})")
        return True, "EXECUTED_SECOND_SPRING"

    except Exception as e:
        err_msg = f"❌ 2X Second Spring Buy FAILED for {ticker}: {str(e)}"
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {err_msg}")
        send_message(err_msg)
        if 'conn' in locals():
            conn.close()
        return False, str(e)

def execute_autonomous_spring_buy(trade_id, ticker, price, sl, tp, ml_conf, timeframe="5m"):
    """
    Submits a market fractional BUY to Public.com, marks the trade active in DB,
    and dispatches instant notifications.
    """
    eligible, reason = check_auto_buy_eligibility(ticker, ml_conf, timeframe=timeframe)
    if not eligible:
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Auto-Buy Bypassed for {ticker} ({timeframe}): {reason}")
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

        # Allow broker 1.5s to settle fill and sync initial_shares
        time.sleep(1.5)
        try:
            from public_executor import get_live_positions
            broker_pos = get_live_positions()
            for p in broker_pos:
                if p['ticker'].upper() == ticker.upper():
                    qty = float(p['quantity'])
                    conn = sqlite3.connect(DB_PATH)
                    c = conn.cursor()
                    c.execute("UPDATE alerts SET initial_shares = ? WHERE id = ?", (qty, trade_id))
                    conn.commit()
                    conn.close()
                    break
        except Exception as pe:
            print(f"Initial shares sync notice: {pe}")

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
