import os
import time
import threading
import sqlite3
import datetime
from decimal import Decimal
import yfinance as yf

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wyckoff_trades.db")

def get_est_now_str():
    return datetime.datetime.now().strftime("%H:%M:%S")

def send_tg(msg: str):
    try:
        from telegram_notifier import send_message, is_configured
        if is_configured():
            send_message(msg)
    except Exception as e:
        print(f"[{get_est_now_str()}] [Guardian] Telegram error: {e}")

LAST_EOD_FLATTEN_DATE = None

def check_eod_flatten(trades, live_prices, conn):
    """
    Automated 3:55 PM EST EOD Flatten Rule:
    Closes all active algo trades at 3:55 PM EST during regular market days
    to ensure 100% cash conversion and zero overnight risk.
    """
    global LAST_EOD_FLATTEN_DATE
    try:
        import zoneinfo
        now_est = datetime.datetime.now(zoneinfo.ZoneInfo("America/New_York"))
    except Exception:
        now_est = datetime.datetime.now()

    current_date = now_est.date()
    is_weekday = now_est.weekday() < 5
    # Trigger strictly within the 3:55 PM - 4:00 PM EST window
    is_eod_window = is_weekday and (now_est.hour == 15 and 55 <= now_est.minute < 60)

    if not is_eod_window or not trades:
        return False

    c = conn.cursor()
    summary_lines = []
    for t in trades:
        trade_id = t['id']
        sym = t['ticker']
        direction = (t.get('direction') or 'LONG').upper()
        entry = float(t.get('entry_price') or 0.0)
        init_sl = float(t.get('initial_stop_loss') or t.get('stop_loss') or entry * 0.985)
        init_risk = abs(entry - init_sl) or (entry * 0.015)
        price = live_prices.get(sym) or entry

        try:
            from public_executor import execute_exit_position
            execute_exit_position(sym, direction=direction)
        except Exception as ex:
            print(f"[{get_est_now_str()}] [Guardian] EOD exit error on {sym}: {ex}")

        is_long = direction == 'LONG'
        curr_gain = (price - entry) if is_long else (entry - price)
        curr_r = round(curr_gain / init_risk, 2)
        outcome = 'WIN' if curr_r > 0 else ('BREAKEVEN' if curr_r == 0 else 'LOSS')

        c.execute("""
            UPDATE alerts 
            SET outcome = ?, exit_price = ?, pnl_r = ?, ghost_status = 'EOD_FLATTEN'
            WHERE id = ?
        """, (outcome, price, curr_r, trade_id))

        summary_lines.append(f"• {sym} ({direction}): Closed @ ${price:.2f} ({curr_r:+.2f}R, {outcome})")

    conn.commit()
    LAST_EOD_FLATTEN_DATE = current_date

    if summary_lines:
        eod_msg = (
            f"🌅 [GUARDIAN] 3:55 PM EOD FLATTEN EXECUTED!\n"
            f"All active algo positions closed at market:\n"
            + "\n".join(summary_lines) +
            "\n\n🛡️ 100% Cash Secured. Zero overnight gap risk."
        )
        print(f"[{get_est_now_str()}] {eod_msg}")
        send_tg(eod_msg)

    return True

def flatten_all_algo_trades(reason="MANUAL_TERMINAL_TRIGGER"):
    """Manually flattens all open algo positions immediately."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM alerts WHERE outcome = 'OPEN' AND user_active = 1")
    trades = [dict(r) for r in c.fetchall()]
    if not trades:
        conn.close()
        return {"success": True, "closed_count": 0, "message": "No active algo trades open."}

    tickers = list(set([t['ticker'] for t in trades]))
    try:
        from public_executor import get_live_prices, execute_exit_position
        live_prices = get_live_prices(tickers)
    except Exception:
        live_prices = {}

    closed = []
    for t in trades:
        sym = t['ticker']
        direction = t.get('direction', 'LONG')
        price = live_prices.get(sym) or float(t.get('entry_price', 0))
        entry = float(t.get('entry_price', 0))
        init_sl = float(t.get('initial_stop_loss') or t.get('stop_loss') or entry * 0.985)
        init_risk = abs(entry - init_sl) or (entry * 0.015)
        curr_gain = (price - entry) if direction.upper() == 'LONG' else (entry - price)
        curr_r = round(curr_gain / init_risk, 2)
        outcome = 'WIN' if curr_r > 0 else ('BREAKEVEN' if curr_r == 0 else 'LOSS')

        try:
            execute_exit_position(sym, direction=direction)
        except Exception as e:
            print(f"Flatten error for {sym}: {e}")

        c.execute("""
            UPDATE alerts 
            SET outcome = ?, exit_price = ?, pnl_r = ?, ghost_status = ? 
            WHERE id = ?
        """, (outcome, price, curr_r, reason, t['id']))
        closed.append({"ticker": sym, "pnl_r": curr_r, "price": price})

    conn.commit()
    conn.close()

    lines = [f"• {c_item['ticker']}: {c_item['pnl_r']:+.2f}R @ ${c_item['price']:.2f}" for c_item in closed]
    msg = f"🚨 [MANUAL FLATTEN] Closed {len(closed)} open algo trades at market:\n" + "\n".join(lines)
    send_tg(msg)
    return {"success": True, "closed_count": len(closed), "trades": closed}

def run_guardian_cycle():
    """
    Evaluates all active user positions every 10 seconds:
    - Rule 1: Breakeven defense ratchet at +0.75R (Stop Loss -> Entry).
    - Rule 2: TP1 Partial Scale at +1.05R (Sell 70% of shares on Public.com, lock 0.30R trail).
    - Rule 3: Uncapped Runner (Remaining 30% trails 0.30R behind highest peak R).
    - Rule 4: Stop loss protection (-1.00R initial stop).
    """
    if not os.path.exists(DB_PATH):
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Query all active positions (both algo user_active=1 and open alerts)
    c.execute("""
        SELECT * FROM alerts 
        WHERE outcome = 'OPEN' AND user_active = 1
        ORDER BY id DESC
    """)
    trades = [dict(r) for r in c.fetchall()]
    if not trades:
        conn.close()
        return

    # Batch fetch live prices for active tickers
    tickers = list(set([t['ticker'] for t in trades if t.get('ticker')]))
    live_prices = {}
    
    # 1. Try public quotes first if available
    try:
        from public_executor import get_live_prices
        live_prices = get_live_prices(tickers)
    except Exception:
        pass

    # 2. Fallback to yfinance for any missing quotes
    missing = [sym for sym in tickers if sym not in live_prices or not live_prices[sym]]
    if missing:
        try:
            data = yf.download(missing, period="1d", interval="1m", progress=False)
            if not data.empty and 'Close' in data:
                last_row = data['Close'].iloc[-1]
                for sym in missing:
                    val = last_row.get(sym) if hasattr(last_row, 'get') else (last_row if len(missing) == 1 else None)
                    if val is not None and not pd.isna(val):
                        live_prices[sym] = float(val)
        except Exception:
            pass

    # Check if 3:55 PM EST EOD Flatten applies
    if check_eod_flatten(trades, live_prices, conn):
        conn.close()
        return

    for t in trades:
        trade_id = t['id']
        sym = t['ticker']
        direction = (t.get('direction') or 'LONG').upper()
        is_long = direction == 'LONG'
        entry = float(t.get('entry_price') or 0.0)
        curr_sl = float(t.get('stop_loss') or 0.0)
        init_sl = float(t.get('initial_stop_loss') or curr_sl or entry * 0.985)
        price = live_prices.get(sym)

        if not price or entry <= 0:
            continue

        # Initial risk calculation (1R)
        init_risk = abs(entry - init_sl)
        if init_risk <= 0.0001:
            init_risk = entry * 0.015

        # Current R multiple
        curr_gain = (price - entry) if is_long else (entry - price)
        curr_r = curr_gain / init_risk

        # Peak high R
        peak_r = max(float(t.get('peak_high_r') or 0.0), curr_r)
        c.execute("UPDATE alerts SET peak_high_r = ? WHERE id = ?", (peak_r, trade_id))

        be_set = bool(t.get('breakeven_set'))
        tier = int(t.get('partial_tier_done') or 0)
        init_shares = float(t.get('initial_shares') or 0.0)

        # Record initial shares from Public.com holding if not yet saved or if user added to position before Tier 1
        try:
            from public_executor import get_client, get_account_id
            port = get_client().get_portfolio(get_account_id())
            for p in (port.positions or []):
                if hasattr(p, 'instrument') and p.instrument.symbol.upper() == sym.upper():
                    live_qty = float(p.quantity or 0.0)
                    if live_qty > 0 and (init_shares <= 0 or (tier == 0 and live_qty > init_shares)):
                        init_shares = live_qty
                        c.execute("UPDATE alerts SET initial_shares = ? WHERE id = ?", (init_shares, trade_id))
                        conn.commit()
                        t['initial_shares'] = init_shares
                    break
        except Exception:
            pass

        # -------------------------------------------------------------
        # TIER 1: +0.20R (Sell 10%, Keep Stop at -1.0R Initial Stop)
        # -------------------------------------------------------------
        if curr_r >= 0.20 and tier < 1:
            sell_qty = round(init_shares * 0.10, 5) if init_shares > 0 else 0.0
            if sell_qty > 0:
                try:
                    from public_executor import execute_exit_position
                    execute_exit_position(sym, quantity=sell_qty, direction=direction)
                except Exception as ex:
                    print(f"[{get_est_now_str()}] [Guardian] Tier 1 partial sell error for {sym}: {ex}")

            c.execute("UPDATE alerts SET partial_tier_done = 1 WHERE id = ?", (trade_id,))
            conn.commit()
            tier = 1
            t['partial_tier_done'] = 1
            t1_msg = (
                f"🎯 [GUARDIAN] TIER 1 HIT: {sym} reaches +{curr_r:.2f}R!\n"
                f"• Action: Banked 10% ({sell_qty} shares) @ ${price:.2f}.\n"
                f"• Defense: Initial Stop remains at ${init_sl:.2f} (-1.0R) to let trade breathe."
            )
            print(f"[{get_est_now_str()}] {t1_msg}")
            send_tg(t1_msg)

        # -------------------------------------------------------------
        # TIER 2: +0.40R (Sell 10%, Trail Stop to -0.50R)
        # -------------------------------------------------------------
        if curr_r >= 0.40 and tier < 2:
            sell_qty = round(init_shares * 0.10, 5) if init_shares > 0 else 0.0
            if sell_qty > 0:
                try:
                    from public_executor import execute_exit_position
                    execute_exit_position(sym, quantity=sell_qty, direction=direction)
                except Exception as ex:
                    print(f"[{get_est_now_str()}] [Guardian] Tier 2 partial sell error for {sym}: {ex}")

            t2_sl = (entry - (0.50 * init_risk)) if is_long else (entry + (0.50 * init_risk))
            c.execute("UPDATE alerts SET partial_tier_done = 2, stop_loss = ? WHERE id = ?", (t2_sl, trade_id))
            conn.commit()
            tier = 2
            t['partial_tier_done'] = 2
            t['stop_loss'] = t2_sl
            curr_sl = t2_sl
            t2_msg = (
                f"🎯 [GUARDIAN] TIER 2 HIT: {sym} reaches +{curr_r:.2f}R!\n"
                f"• Action: Banked 10% ({sell_qty} shares, 20% total).\n"
                f"• Defense: Stop Loss trailed to ${t2_sl:.2f} (-0.50R, risk cut in half)."
            )
            print(f"[{get_est_now_str()}] {t2_msg}")
            send_tg(t2_msg)

        # -------------------------------------------------------------
        # TIER 3: +0.65R (Sell 10%, Move Stop to Entry $0.00 Breakeven)
        # -------------------------------------------------------------
        if curr_r >= 0.65 and tier < 3:
            sell_qty = round(init_shares * 0.10, 5) if init_shares > 0 else 0.0
            if sell_qty > 0:
                try:
                    from public_executor import execute_exit_position
                    execute_exit_position(sym, quantity=sell_qty, direction=direction)
                except Exception as ex:
                    print(f"[{get_est_now_str()}] [Guardian] Tier 3 partial sell error for {sym}: {ex}")

            c.execute("UPDATE alerts SET partial_tier_done = 3, stop_loss = ?, breakeven_set = 1 WHERE id = ?", (entry, trade_id))
            conn.commit()
            tier = 3
            t['partial_tier_done'] = 3
            t['stop_loss'] = entry
            t['breakeven_set'] = 1
            curr_sl = entry
            be_set = True
            t3_msg = (
                f"🛡️ [GUARDIAN] TIER 3 BREAKEVEN LOCK: {sym} reaches +{curr_r:.2f}R!\n"
                f"• Action: Banked 10% ({sell_qty} shares, 30% total).\n"
                f"• Defense: Stop moved to Entry (${entry:.2f}). Dollar risk is now $0.00 (Free Trade)!"
            )
            print(f"[{get_est_now_str()}] {t3_msg}")
            send_tg(t3_msg)

        # -------------------------------------------------------------
        # TIER 4: +1.00R (Sell 25%, Trail Stop to +0.50R Guaranteed Profit)
        # -------------------------------------------------------------
        if curr_r >= 1.00 and tier < 4:
            sell_qty = round(init_shares * 0.25, 5) if init_shares > 0 else 0.0
            if sell_qty > 0:
                try:
                    from public_executor import execute_exit_position
                    execute_exit_position(sym, quantity=sell_qty, direction=direction)
                except Exception as ex:
                    print(f"[{get_est_now_str()}] [Guardian] Tier 4 partial sell error for {sym}: {ex}")

            t4_sl = (entry + (0.50 * init_risk)) if is_long else (entry - (0.50 * init_risk))
            c.execute("UPDATE alerts SET partial_tier_done = 4, stop_loss = ? WHERE id = ?", (t4_sl, trade_id))
            conn.commit()
            tier = 4
            t['partial_tier_done'] = 4
            t['stop_loss'] = t4_sl
            curr_sl = t4_sl
            t4_msg = (
                f"💰 [GUARDIAN] TIER 4 CORE PAYDAY: {sym} reaches +{curr_r:.2f}R!\n"
                f"• Action: Banked 25% ({sell_qty} shares, 55% total).\n"
                f"• Defense: Stop Loss locked in at ${t4_sl:.2f} (+0.50R guaranteed win on remainder)."
            )
            print(f"[{get_est_now_str()}] {t4_msg}")
            send_tg(t4_msg)

        # -------------------------------------------------------------
        # TIER 5: +1.30R (Sell 25%, Trail Stop to +0.85R Range Ceiling)
        # -------------------------------------------------------------
        if curr_r >= 1.30 and tier < 5:
            sell_qty = round(init_shares * 0.25, 5) if init_shares > 0 else 0.0
            if sell_qty > 0:
                try:
                    from public_executor import execute_exit_position
                    execute_exit_position(sym, quantity=sell_qty, direction=direction)
                except Exception as ex:
                    print(f"[{get_est_now_str()}] [Guardian] Tier 5 partial sell error for {sym}: {ex}")

            t5_sl = (entry + (0.85 * init_risk)) if is_long else (entry - (0.85 * init_risk))
            c.execute("""
                UPDATE alerts 
                SET partial_tier_done = 5, partial_exit_done = 1,
                    partial_exit_price = ?, partial_pnl_r = ?, stop_loss = ? 
                WHERE id = ?
            """, (price, round(curr_r, 2), t5_sl, trade_id))
            conn.commit()
            tier = 5
            t['partial_tier_done'] = 5
            t['partial_exit_done'] = 1
            t['stop_loss'] = t5_sl
            curr_sl = t5_sl
            t5_msg = (
                f"🚀 [GUARDIAN] TIER 5 CEILING HIT: {sym} reaches +{curr_r:.2f}R!\n"
                f"• Action: Banked 25% ({sell_qty} shares, 80% total banked!).\n"
                f"• Runner: Final 20% moonbag trailing 0.25R below peak into 3:55 PM EOD flatten."
            )
            print(f"[{get_est_now_str()}] {t5_msg}")
            send_tg(t5_msg)

        # -------------------------------------------------------------
        # TIER 6: UNCAPPED 20% RUNNER (Trailing 0.25R below Peak High)
        # -------------------------------------------------------------
        if tier >= 5:
            trail_stop_r = max(0.85, peak_r - 0.25)
            runner_sl = (entry + (trail_stop_r * init_risk)) if is_long else (entry - (trail_stop_r * init_risk))

            if is_long and runner_sl > curr_sl:
                c.execute("UPDATE alerts SET stop_loss = ?, trailing_stop_price = ? WHERE id = ?", (runner_sl, runner_sl, trade_id))
                conn.commit()
                curr_sl = runner_sl
            elif not is_long and runner_sl < curr_sl:
                c.execute("UPDATE alerts SET stop_loss = ?, trailing_stop_price = ? WHERE id = ?", (runner_sl, runner_sl, trade_id))
                conn.commit()
                curr_sl = runner_sl

            # Check if runner touched trailing stop
            is_trail_stopped = (price <= curr_sl) if is_long else (price >= curr_sl)
            if is_trail_stopped:
                try:
                    from public_executor import execute_exit_position
                    execute_exit_position(sym, direction=direction)
                except Exception as ex:
                    print(f"[{get_est_now_str()}] [Guardian] Runner exit error for {sym}: {ex}")

                final_r = round(curr_r, 2)
                c.execute("UPDATE alerts SET outcome = 'WIN', exit_price = ?, pnl_r = ? WHERE id = ?", (price, final_r, trade_id))
                conn.commit()
                runner_exit_msg = (
                    f"🎯 [GUARDIAN] RUNNER TRAIL STOP HIT: {sym} closed at ${price:.2f} (+{final_r:+.2f}R)!\n"
                    f"Position 100% closed. 80% banked in tiers, runner exited at +{final_r:+.2f}R."
                )
                print(f"[{get_est_now_str()}] {runner_exit_msg}")
                send_tg(runner_exit_msg)
                continue

        # -------------------------------------------------------------
        # STEP 4: STOP LOSS / TRAILING STOP EXIT (TIERS 0-4)
        # -------------------------------------------------------------
        if tier < 5:
            is_stopped = (price <= curr_sl) if is_long else (price >= curr_sl)
            if is_stopped:
                try:
                    from public_executor import execute_exit_position
                    execute_exit_position(sym, direction=direction)
                except Exception as ex:
                    print(f"[{get_est_now_str()}] [Guardian] Stop exit error for {sym}: {ex}")

                final_r = round(curr_r, 2)
                if final_r > 0.05 or tier >= 3:
                    outcome = 'WIN'
                elif abs(final_r) <= 0.05 or be_set:
                    outcome = 'BREAKEVEN'
                else:
                    outcome = 'LOSS'

                c.execute("""
                    UPDATE alerts 
                    SET outcome = ?, exit_price = ?, pnl_r = ?, ghost_status = 'MONITORING'
                    WHERE id = ?
                """, (outcome, price, final_r, trade_id))
                conn.commit()

                stop_msg = (
                    f"🛑 [GUARDIAN] POSITION EXITED: {sym} hit stop at ${price:.2f} ({outcome}, {final_r:+.2f}R).\n"
                    f"• Tier reached: {tier}/5. Remainder liquidated on Public.com."
                )
                print(f"[{get_est_now_str()}] {stop_msg}")
                send_tg(stop_msg)

    conn.commit()
    conn.close()

def guardian_loop():
    """Continuous 10-second monitoring daemon."""
    print(f"[{get_est_now_str()}] [Guardian] 10-Second Auto-Exit Guardian daemon started.")
    while True:
        try:
            run_guardian_cycle()
        except Exception as e:
            print(f"[{get_est_now_str()}] [Guardian] Error in cycle: {e}")
        time.sleep(10)

_GUARDIAN_THREAD = None

def start_guardian_daemon():
    """Starts the Guardian loop in a dedicated background thread."""
    global _GUARDIAN_THREAD
    if _GUARDIAN_THREAD is None or not _GUARDIAN_THREAD.is_alive():
        _GUARDIAN_THREAD = threading.Thread(target=guardian_loop, daemon=True)
        _GUARDIAN_THREAD.start()
        print(f"[{get_est_now_str()}] [Guardian] Auto-Exit Guardian thread launched.")

if __name__ == "__main__":
    start_guardian_daemon()
    while True:
        time.sleep(1)
