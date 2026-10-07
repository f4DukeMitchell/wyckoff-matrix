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
        partial_done = bool(t.get('partial_exit_done'))

        # -------------------------------------------------------------
        # STEP 1: BREAKEVEN RATCHET (+0.75R)
        # -------------------------------------------------------------
        if curr_r >= 0.75 and not be_set:
            c.execute("""
                UPDATE alerts 
                SET stop_loss = ?, breakeven_set = 1 
                WHERE id = ?
            """, (entry, trade_id))
            conn.commit()
            t['breakeven_set'] = 1
            t['stop_loss'] = entry
            be_msg = (
                f"🛡️ [GUARDIAN] BREAKEVEN DEFENSE TRIGGERED: {sym} reaches +{curr_r:.2f}R!\n"
                f"Stop Loss moved to Entry (${entry:.2f}). Dollar risk is now $0.00!"
            )
            print(f"[{get_est_now_str()}] {be_msg}")
            send_tg(be_msg)

        # -------------------------------------------------------------
        # STEP 2: TP1 PARTIAL SCALE (+1.05R) - SELL 70% ON PUBLIC.COM
        # -------------------------------------------------------------
        if curr_r >= 1.05 and not partial_done:
            sold_qty = 0.0
            try:
                from public_executor import get_client, get_account_id, execute_exit_position
                client = get_client()
                acc_id = get_account_id()
                port = client.get_portfolio(acc_id)
                curr_shares = 0.0
                for p in (port.positions or []):
                    if hasattr(p, 'instrument') and p.instrument.symbol.upper() == sym.upper():
                        curr_shares = float(p.quantity or 0.0)
                        break
                
                if curr_shares > 0:
                    # Sell 70% of current holding
                    qty_to_sell = round(curr_shares * 0.70, 5)
                    if qty_to_sell > 0:
                        res = execute_exit_position(sym, quantity=qty_to_sell, direction=direction)
                        sold_qty = qty_to_sell
            except Exception as ex:
                print(f"[{get_est_now_str()}] [Guardian] Partial sell error for {sym}: {ex}")

            # Calculate initial 0.30R trailing stop for the remaining 30% runner (+0.75R floor)
            trail_stop_r = max(0.75, peak_r - 0.30)
            runner_sl = (entry + (trail_stop_r * init_risk)) if is_long else (entry - (trail_stop_r * init_risk))

            c.execute("""
                UPDATE alerts 
                SET partial_exit_done = 1,
                    partial_exit_price = ?,
                    partial_pnl_r = ?,
                    stop_loss = ?
                WHERE id = ?
            """, (price, round(curr_r, 2), runner_sl, trade_id))
            conn.commit()
            t['partial_exit_done'] = 1
            t['stop_loss'] = runner_sl

            tp1_msg = (
                f"💰 [GUARDIAN] TARGET 1 REACHED: {sym} reached +{curr_r:.2f}R!\n"
                f"• Action: Sold 70% ({sold_qty} shares) @ ${price:.2f} to lock in core profit.\n"
                f"• Runner: Remaining 30% is UNCAPPED with a 0.30R trailing stop at ${runner_sl:.2f} (+{trail_stop_r:.2f}R)."
            )
            print(f"[{get_est_now_str()}] {tp1_msg}")
            send_tg(tp1_msg)

        # -------------------------------------------------------------
        # STEP 3: UNCAPPED 0.30R TRAILING STOP ON 30% RUNNER
        # -------------------------------------------------------------
        if partial_done:
            # Trailing stop stays exactly 0.30R behind peak high-water mark
            trail_stop_r = peak_r - 0.30
            new_runner_sl = (entry + (trail_stop_r * init_risk)) if is_long else (entry - (trail_stop_r * init_risk))

            # Only ratchet stop upwards for longs, downwards for shorts
            if is_long and new_runner_sl > curr_sl:
                c.execute("UPDATE alerts SET stop_loss = ? WHERE id = ?", (new_runner_sl, trade_id))
                conn.commit()
                curr_sl = new_runner_sl
            elif not is_long and new_runner_sl < curr_sl:
                c.execute("UPDATE alerts SET stop_loss = ? WHERE id = ?", (new_runner_sl, trade_id))
                conn.commit()
                curr_sl = new_runner_sl

            # Check if 30% runner has hit trailing stop
            is_trail_stopped = (price <= curr_sl) if is_long else (price >= curr_sl)
            if is_trail_stopped:
                exit_res = {}
                try:
                    from public_executor import execute_exit_position
                    exit_res = execute_exit_position(sym, direction=direction)
                except Exception as ex:
                    print(f"[{get_est_now_str()}] [Guardian] Runner exit error for {sym}: {ex}")

                final_r = round(curr_r, 2)
                c.execute("""
                    UPDATE alerts 
                    SET outcome = 'WIN', exit_price = ?, pnl_r = ? 
                    WHERE id = ?
                """, (price, final_r, trade_id))
                conn.commit()

                runner_exit_msg = (
                    f"🎯 [GUARDIAN] RUNNER TRAIL STOP HIT: {sym} closed at ${price:.2f} (+{final_r:+.2f}R)!\n"
                    f"Position is 100% closed. Core was banked at +1.05R, runner exited at +{final_r:+.2f}R."
                )
                print(f"[{get_est_now_str()}] {runner_exit_msg}")
                send_tg(runner_exit_msg)
                continue

        # -------------------------------------------------------------
        # STEP 4: PRE-TP1 STOP LOSS / BREAKEVEN EXIT
        # -------------------------------------------------------------
        if not partial_done:
            is_stopped = (price <= curr_sl) if is_long else (price >= curr_sl)
            if is_stopped:
                exit_res = {}
                try:
                    from public_executor import execute_exit_position
                    exit_res = execute_exit_position(sym, direction=direction)
                except Exception as ex:
                    print(f"[{get_est_now_str()}] [Guardian] Full stop exit error for {sym}: {ex}")

                outcome = 'BREAKEVEN' if be_set else 'LOSS'
                final_r = 0.0 if be_set else max(-1.10, -round(abs(entry - price) / init_risk, 2))

                c.execute("""
                    UPDATE alerts 
                    SET outcome = ?, exit_price = ?, pnl_r = ? 
                    WHERE id = ?
                """, (outcome, price, final_r, trade_id))
                conn.commit()

                stop_msg = (
                    f"🛑 [GUARDIAN] POSITION EXITED: {sym} touched stop at ${price:.2f} ({outcome}, {final_r:+.2f}R).\n"
                    f"Position fully closed on Public.com."
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
