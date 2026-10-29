import os
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
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

from data_paths import DB_PATH  # runtime DB lives outside the git tree (survives git reset --hard)

# --- AUTONOMOUS BUY SETTINGS ---
import config_manager

def get_config():
    return config_manager.load_config()

# Calibrated ML confidence floor. With Frank's regularized Random Forest (41.0% base WIN rate),
# high-quality Spring setups produce calibrated probabilities between 25.0% and 45.0%.
# Configurable dynamically via auto_buyer_config.json / min_ml_conf (default 25.0%).
AUTO_BUY_MIN_ML_CONF = float(os.getenv("AUTO_BUY_MIN_ML_CONF", "25.0"))

# Allowed Auto-Buy Timeframes: 5m Day Trades & 15m Momentum Springs
AUTO_BUY_ALLOWED_TIMEFRAMES = ["5m", "15m"]

# Window: 9:40 AM to 3:58 PM EST (Skip 9:30-9:40 AM open order imbalance; capture expansion through afternoon closing cross)
AUTO_BUY_START_TIME = datetime.time(9, 40)
AUTO_BUY_END_TIME = datetime.time(15, 58)
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
        tp = float(trade.get('take_profit') or 0.0)
        entry = float(trade.get('entry_price') or 0.0)
        init_risk = abs(entry - init_sl)
        target_r = (abs(tp - entry) / init_risk) if (init_risk > 0.001 and tp > 0) else 1.0

        # Conditional Pyramiding Rule: Pyramid 2nd Spring down to 0.65R target expectancy
        if target_r < 0.65:
            return None

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
    cfg = get_config()
    if not cfg.get("auto_buy_enabled", True):
        return False, "AUTO_BUY_DISABLED"

    # 0. Timeframe Guardrail (5m, 15m, and 1m day trades allowed)
    allowed_tf = cfg.get("allowed_timeframes", AUTO_BUY_ALLOWED_TIMEFRAMES)
    if (timeframe or "").lower() not in [t.lower() for t in allowed_tf]:
        return False, f"TIMEFRAME_RESTRICTED ({timeframe} not in {allowed_tf})"

    now = get_market_time()
    current_t = now.time()

    # 1. Opening Expansion Gate (Must be >= 9:40 AM EST)
    if current_t < AUTO_BUY_START_TIME:
        return False, f"WAITING_FOR_940AM_CONFIRMATION (Current: {current_t.strftime('%H:%M:%S')})"

    # 2. End-of-Day Cutoff (Active through 3:58 PM EST ahead of market close)
    if current_t > AUTO_BUY_END_TIME:
        return False, f"AFTER_3:58PM_CUTOFF (Current: {current_t.strftime('%H:%M:%S')})"

    # 3. Time-of-day lunch block removed: empirical data shows Longs hold 66.7% win rate during lunch
    # All setups evaluated strictly by structural Wyckoff quality and ML confidence floor

    # 4. ML Confidence Gate (Calibrated to Frank's Regularized Forest: default 25.0% floor)
    min_ml_floor = float(cfg.get("min_ml_conf", AUTO_BUY_MIN_ML_CONF))
    if ml_conf is not None and ml_conf < min_ml_floor:
        return False, f"ML_CONF_TOO_LOW ({ml_conf:.1f}% < {min_ml_floor:.1f}%)"

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # 5. Max Concurrent Active Positions (Up to 25 positions / 25% of portfolio)
    max_concurrent = int(cfg.get("max_concurrent", 3))
    c.execute("SELECT COUNT(id) FROM alerts WHERE outcome = 'OPEN' AND user_active = 1")
    active_count = (c.fetchone() or (0,))[0]
    if active_count >= max_concurrent:
        conn.close()
        return False, f"MAX_POSITIONS_REACHED ({active_count}/{max_concurrent})"

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

    # 7. Frank Proving Grounds (Default: SHADOW MODE - Zero Interference with Live Trades)
    try:
        from frank import evaluate_setup
        from config_manager import load_config
        cfg = load_config()
        frank_mode = str(cfg.get("frank_mode", "SHADOW")).upper()

        setup_payload = {
            "ticker": ticker.upper(),
            "direction": "LONG",
            "timeframe": timeframe,
            "ml_confidence": ml_conf
        }
        ai_verdict = evaluate_setup(setup_payload)
        verdict = ai_verdict.get("verdict", "PROCEED")
        conf = ai_verdict.get("ai_confidence", 70)
        rationale = ai_verdict.get("rationale", "")

        # Record Shadow Telemetry for Frank's Proving Grounds Scorecard
        try:
            conn_frank = sqlite3.connect(DB_PATH)
            c_frank = conn_frank.cursor()
            c_frank.execute("""
                CREATE TABLE IF NOT EXISTS frank_shadow_audits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    ticker TEXT,
                    direction TEXT,
                    timeframe TEXT,
                    ml_confidence REAL,
                    frank_verdict TEXT,
                    frank_confidence REAL,
                    frank_rationale TEXT,
                    trade_blocked INTEGER DEFAULT 0
                )
            """)
            is_blocked = 1 if (frank_mode == "ENFORCE" and verdict == "VETO") else 0
            c_frank.execute("""
                INSERT INTO frank_shadow_audits 
                (timestamp, ticker, direction, timeframe, ml_confidence, frank_verdict, frank_confidence, frank_rationale, trade_blocked)
                VALUES (datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?)
            """, (ticker.upper(), "LONG", timeframe, ml_conf, verdict, conf, rationale, is_blocked))
            conn_frank.commit()
            conn_frank.close()
        except Exception as audit_db_err:
            print(f"[Frank Telemetry] DB log notice: {audit_db_err}")

        # Strict Proving Rule: In SHADOW mode, Frank NEVER blocks or interferes with real executions
        if frank_mode == "ENFORCE" and verdict == "VETO":
            return False, f"FRANK_VETO: {rationale}"
        else:
            print(f"[Frank Shadow Proving Ground] {ticker.upper()} -> Recommendation: {verdict} ({conf}% conf) | {rationale} | Trade NOT blocked (Shadow Mode Active).")
    except Exception as ai_err:
        print(f"[Auto Buyer] Frank check bypassed: {ai_err}")

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
    cfg = get_config()
    if not cfg.get("auto_buy_enabled", True):
        return False, "AUTO_BUY_DISABLED"

    allowed_tf = cfg.get("allowed_timeframes", AUTO_BUY_ALLOWED_TIMEFRAMES)
    if (timeframe or "").lower() not in [t.lower() for t in allowed_tf]:
        return False, f"TIMEFRAME_RESTRICTED ({timeframe} not in {allowed_tf})"

    now = get_market_time()
    current_t = now.time()

    if current_t < AUTO_BUY_START_TIME:
        return False, "WAITING_FOR_940AM_CONFIRMATION"

    if current_t > AUTO_BUY_END_TIME:
        return False, "AFTER_3:58PM_CUTOFF"


    if ml_conf is not None and ml_conf < AUTO_BUY_MIN_ML_CONF:
        return False, f"ML_CONF_TOO_LOW ({ml_conf:.1f}% < {AUTO_BUY_MIN_ML_CONF}%)"

    # Fetch parent trade
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT id, ticker, entry_price, initial_stop_loss, stop_loss, initial_shares, 
               partial_tier_done, second_spring_added, take_profit
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

    # Determine allocation based on user configuration
    from public_executor import get_account_capital_summary
    cap_summary = get_account_capital_summary()
    bp = cap_summary.get('buying_power', 0.0)

    init_risk = abs(old_entry - structural_stop)
    tp = float(trade.get('take_profit') or 0.0)
    target_r = (abs(tp - old_entry) / init_risk) if (init_risk > 0.001 and tp > 0) else 1.0
    sizing = config_manager.calculate_sizing_allocation(bp, target_r=target_r)
    dollar_alloc = sizing['final_alloc']

    try:
        print(f"[{get_market_time().strftime('%H:%M:%S')}] ⚡ EXECUTING 2X SECOND SPRING BUY: {ticker} (${dollar_alloc:.2f}) on Public.com...")
        order_res = execute_dollar_buy(ticker.upper(), dollar_alloc)
        status = order_res.get('status', 'SUBMITTED')
        order_uuid = order_res.get('order_id', 'N/A')

        # Allow broker 1.5s to settle fill
        time.sleep(1.5)

        # Sync live position from broker
        new_total_shares = old_shares + (dollar_alloc / price)
        blended_entry = old_entry
        try:
            from public_executor import get_broker_portfolio_positions
            broker_pos = get_broker_portfolio_positions()
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
            f"⚡ [LIVE AUTO-BUY EXECUTED - 2X PYRAMID] {ticker.upper()}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Execution Status: 🟢 LIVE PUBLIC.COM BROKER FILL\n"
            f"Symbol: {ticker.upper()} (Tranche #2 Added)\n"
            f"Timeframe: {timeframe}\n"
            f"Added Capital: ${dollar_alloc:.2f} ({status})\n"
            f"New Blended Entry: ${blended_entry:.2f}\n"
            f"Total Shares: {new_total_shares:.4f} (2X Position)\n"
            f"🔒 Unified Structural Stop: ${structural_stop:.2f} (LOCKED - Untouched!)\n"
            f"Order UUID: {order_uuid}{conf_str}\n\n"
            f"🛡️ Exit Guardian Synchronized:\n"
            f"• Both tranches share the exact same invalidation line.\n"
            f"• Target-Relative partial ladder scaled to bank double cash at targets!"
        )
        send_message(msg, force=True)
        try:
            from telegram_notifier import TELEGRAM_CHAT_ID_DAY
            if TELEGRAM_CHAT_ID_DAY:
                send_message(msg, chat_id=TELEGRAM_CHAT_ID_DAY, force=True)
        except Exception:
            pass
        print(f"[{get_market_time().strftime('%H:%M:%S')}] ✅ 2X Second Spring Success: {ticker} (${dollar_alloc:.2f})")
        return True, "EXECUTED_SECOND_SPRING"

    except Exception as e:
        err_msg = f"❌ 2X Second Spring Buy FAILED for {ticker}: {str(e)}"
        print(f"[{get_market_time().strftime('%H:%M:%S')}] {err_msg}")
        send_message(err_msg, force=True)
        if 'conn' in locals():
            conn.close()
        return False, str(e)


def rebalance_and_calculate_dynamic_trim(ticker: str, price: float, bp: float, cfg: dict):
    """
    Dynamic 3-Slot Architecture (100% -> 50% -> 33%) with Breakeven Guarding:
    - 0 Open Trades: Deploy 95% of available BP (~100% allocation, reserving 5% buffer).
    - 1 Open Trade: Target 50% allocation each.
      * If Trade 1 is Breakeven Guarded (Tier 2+ or BE set): Trim 50% of Trade 1, fund Trade 2.
      * If Trade 1 is NOT guarded: Protect Trade 1! Do not chop into a pullback.
        Fund Trade 2 with remaining cash (if >= $50), else wait for Trade 1 safety.
    - 2 Open Trades: Target 33.3% allocation each.
      * Trim guarded trades to 33.3%, fund Trade 3.
    - >= 3 Open Trades: Hard rejection (MAX_POSITIONS_REACHED 3/3).
    """
    from public_executor import get_account_capital_summary, execute_exit_position
    import time
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT id, ticker, entry_price, initial_shares, breakeven_set, partial_tier_done, trailing_stop_price 
        FROM alerts 
        WHERE outcome = 'OPEN' AND user_active = 1
    """)
    open_trades = c.fetchall()
    conn.close()
    
    active_count = len(open_trades)
    max_concurrent = int(cfg.get("max_concurrent", 3))
    
    if active_count >= max_concurrent:
        return False, f"MAX_POSITIONS_REACHED ({active_count}/{max_concurrent})", 0.0, None
        
    cap_summary = get_account_capital_summary()
    live_bp = float(cap_summary.get('buying_power', bp))
    
    # CASE 1: 0 Open Trades (Slot 1 receives 100% buying power)
    if active_count == 0:
        dollar_alloc = max(10.0, round(live_bp * 0.95, 2))
        return True, "SLOT_1_FULL_CAPITAL", dollar_alloc, "100% Capital Deployment"
        
    # CASE 2: 1 Open Trade (Target 50% allocation each)
    elif active_count == 1:
        t1_id, t1_ticker, t1_entry, t1_shares, t1_be, t1_tier, t1_trail = open_trades[0]
        t1_entry = float(t1_entry or 0.0)
        t1_shares = float(t1_shares or 0.0)
        t1_be = int(t1_be or 0)
        t1_tier = int(t1_tier or 0)
        t1_trail = float(t1_trail or 0.0)
        
        # Check Breakeven Guarding: has Trade 1 eliminated downside risk?
        is_guarded = (t1_be == 1) or (t1_tier >= 2) or (t1_trail >= t1_entry and t1_trail > 0)
        
        if is_guarded and t1_shares > 0:
            sell_qty = round(t1_shares * 0.50, 4)
            print(f"[DynamicTrim] Trimming 50% of guarded {t1_ticker} ({sell_qty} shs) to fund Trade 2 ({ticker})...")
            try:
                execute_exit_position(t1_ticker, quantity=sell_qty, direction='LONG')
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute("UPDATE alerts SET initial_shares = ? WHERE id = ?", (round(t1_shares - sell_qty, 4), t1_id))
                conn.commit()
                conn.close()
            except Exception as ex:
                print(f"[DynamicTrim] Warning trimming {t1_ticker}: {ex}")
                
            time.sleep(1.5)
            cap_summary = get_account_capital_summary()
            live_bp = float(cap_summary.get('buying_power', live_bp))
            dollar_alloc = max(10.0, round(live_bp * 0.95, 2))
            note = f"Trimmed 50% of guarded runner {t1_ticker} ({sell_qty} shs) to rebalance 50/50"
            return True, "SLOT_2_REBALANCED_50", dollar_alloc, note
        else:
            # Protect Trade 1 from being chopped in a pullback
            if live_bp >= 50.0:
                dollar_alloc = round(live_bp * 0.95, 2)
                return True, "SLOT_2_REMAINING_CASH", dollar_alloc, f"Protected {t1_ticker} from pullback trim (used liquid cash)"
            else:
                return False, f"TRADE_1_UNGUARDED_PROTECTED ({t1_ticker} still in initial test)", 0.0, None
                
    # CASE 3: 2 Open Trades (Target 33.3% allocation each)
    elif active_count == 2:
        if live_bp >= 300.0:
            dollar_alloc = min(round(live_bp * 0.95, 2), 960.0)
            return True, "SLOT_3_ALLOCATION", dollar_alloc, "Funded Slot 3 from liquid cash"
        else:
            trimmed = False
            for tid, tticker, tentry, tshares, tbe, ttier, ttrail in open_trades:
                tentry = float(tentry or 0.0)
                tshares = float(tshares or 0.0)
                tbe = int(tbe or 0)
                ttier = int(ttier or 0)
                ttrail = float(ttrail or 0.0)
                if ((tbe == 1) or (ttier >= 2) or (ttrail >= tentry and ttrail > 0)) and tshares > 0:
                    sell_qty = round(tshares * 0.33, 4)
                    if sell_qty > 0:
                        try:
                            execute_exit_position(tticker, quantity=sell_qty, direction='LONG')
                            conn = sqlite3.connect(DB_PATH)
                            c = conn.cursor()
                            c.execute("UPDATE alerts SET initial_shares = ? WHERE id = ?", (round(tshares - sell_qty, 4), tid))
                            conn.commit()
                            conn.close()
                            trimmed = True
                        except Exception as ex:
                            print(f"[DynamicTrim] Warning trimming {tticker}: {ex}")
            if trimmed:
                time.sleep(1.5)
                cap_summary = get_account_capital_summary()
                live_bp = float(cap_summary.get('buying_power', live_bp))
                dollar_alloc = max(10.0, round(live_bp * 0.95, 2))
                return True, "SLOT_3_REBALANCED_33", dollar_alloc, "Trimmed guarded runners to equalize 3 slots"
            else:
                return False, "TRADES_UNGUARDED_PROTECTED (Existing positions not yet at Breakeven)", 0.0, None

    return False, "DYNAMIC_TRIM_UNKNOWN", 0.0, None

def execute_autonomous_spring_buy(trade_id, ticker, price, sl, tp, ml_conf, timeframe="5m", target_r=None):
    """
    Submits a market fractional BUY to Public.com, marks the trade active in DB,
    and dispatches instant notifications with dynamic Target-Relative Ladder.
    """
    eligible, reason = check_auto_buy_eligibility(ticker, ml_conf, timeframe=timeframe)
    if not eligible:
        print(f"[{get_market_time().strftime('%H:%M:%S')}] Auto-Buy Bypassed for {ticker} ({timeframe}): {reason}")
        return False, reason

    # Determine allocation based on user configuration and target R
    from public_executor import get_account_capital_summary
    cap_summary = get_account_capital_summary()
    bp = cap_summary.get('buying_power', 0.0)

    init_risk = abs(price - sl)
    calc_target_r = target_r if target_r is not None else (abs(tp - price) / init_risk if (init_risk > 0.001 and tp > 0) else 1.15)
    calc_target_r = max(0.65, calc_target_r)

    cfg = get_config()
    alloc_mode = cfg.get("alloc_mode", "DYNAMIC_TRIM")
    min_ml_floor = float(cfg.get("min_ml_conf", AUTO_BUY_MIN_ML_CONF))
    
    rebalance_note = None
    if alloc_mode == "DYNAMIC_TRIM":
        ok, reason, dollar_alloc, rebalance_note = rebalance_and_calculate_dynamic_trim(ticker, price, bp, cfg)
        if not ok:
            print(f"[{get_market_time().strftime('%H:%M:%S')}] Dynamic Trim Bypassed for {ticker}: {reason}")
            return False, reason
        base_alloc = dollar_alloc
        mult = 1.00
        sizing = {"target_weighted": False, "alloc_mode": "DYNAMIC_TRIM"}
    else:
        sizing = config_manager.calculate_sizing_allocation(bp, target_r=calc_target_r)
        dollar_alloc = sizing['final_alloc']
        base_alloc = sizing['base_alloc']
        mult = sizing['multiplier']

    t1_r = round(calc_target_r * 0.20, 2)
    t2_r = round(calc_target_r * 0.35, 2)
    t3_r = round(calc_target_r * 0.50, 2)
    t4_r = round(calc_target_r * 0.85, 2)
    t5_r = round(calc_target_r * 1.00, 2)
    t1_risk_cap = round(min(0.55, max(0.35, calc_target_r * 0.50)), 2)

    try:
        print(f"[{get_market_time().strftime('%H:%M:%S')}] ⚡ EXECUTING AUTONOMOUS BUY: {ticker} (${dollar_alloc:.2f}) on Public.com...")
        order_res = execute_dollar_buy(ticker.upper(), dollar_alloc)
        
        # Mark active in database and store optimal_target_r
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE alerts SET user_active = 1, optimal_target_r = ? WHERE id = ?", (round(calc_target_r, 2), trade_id))
        conn.commit()
        conn.close()

        status = order_res.get('status', 'SUBMITTED')
        order_uuid = order_res.get('order_id', 'N/A')

        # Allow broker 1.5s to settle fill and sync initial_shares
        time.sleep(1.5)
        try:
            from public_executor import get_broker_portfolio_positions
            broker_pos = get_broker_portfolio_positions()
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
        mult_str = f" (Base: ${base_alloc:.2f} × {mult:.2f}x)" if sizing.get('target_weighted') else ""
        rebalance_str = f"Dynamic Rebalance: {rebalance_note}\n" if rebalance_note else ""
        msg = (
            f"⚡ [LIVE AUTO-BUY EXECUTED] {ticker.upper()}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Execution Status: 🟢 LIVE PUBLIC.COM BROKER FILL (ML >= {min_ml_floor:.0f}% Edge)\n"
            f"Symbol: {ticker.upper()} (LONG)\n"
            f"Timeframe: {timeframe}\n"
            f"Allocation: ${dollar_alloc:.2f}{mult_str} ({status})\n" f"{rebalance_str}"
            f"Entry: ${price:.2f}\n"
            f"Initial Stop: ${sl:.2f}\n"
            f"Wyckoff Target: ${tp:.2f} (+{calc_target_r:.2f}R)\n"
            f"Order UUID: {order_uuid}{conf_str}\n\n"
            f"🛡️ Elastic Risk/Reward Envelope (+{calc_target_r:.2f}R Target):\n"
            f"• Tier 1 (+{t1_r:+.2f}R | 20%): Bank 10% (Risk capped at -{t1_risk_cap:.2f}R)\n"
            f"• Tier 2 (+{t2_r:+.2f}R | 35%): Bank 10% (Stop trails -0.20R micro-risk)\n"
            f"• Tier 3 (+{t3_r:+.2f}R | 50%): Bank 15% (Stop to BREAKEVEN $0.00 Free Trade)\n"
            f"• Tier 4 (+{t4_r:+.2f}R | 85%): Bank 25% (Front-Run Resistance)\n"
            f"• Tier 5 (+{t5_r:+.2f}R | 100%): Bank 25% (Full Target Tag)\n"
            f"• Tier 6 (Runner 15%): Trailed into 3:58 PM EST Flatten"
        )
        send_message(msg, force=True)
        try:
            from telegram_notifier import TELEGRAM_CHAT_ID_DAY
            if TELEGRAM_CHAT_ID_DAY:
                send_message(msg, chat_id=TELEGRAM_CHAT_ID_DAY, force=True)
        except Exception:
            pass
        print(f"[{get_market_time().strftime('%H:%M:%S')}] ✅ Auto-Buy Success: {ticker} (${dollar_alloc:.2f})")
        return True, "EXECUTED"
    except Exception as e:
        err_msg = f"❌ Autonomous Buy FAILED for {ticker}: {str(e)}"
        print(f"[{get_market_time().strftime('%H:%M:%S')}] {err_msg}")
        send_message(err_msg, force=True)
        return False, str(e)
