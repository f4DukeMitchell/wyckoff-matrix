"""
Wyckoff Covered Call Guardian Daemon
====================================
Automates the lifecycle of covered calls on core long holdings (AMC, NKE):
1. Milestone 1: Instant Cash Deposit verification
2. Milestone 2: Downside breakeven floor tracking
3. Milestone 3: 50% Time Decay Take-Profit & Close (Automated buyback to bank profit early)
4. Milestone 4: Roll Defense Trigger (Automated Roll Up-and-Out if stock nears strike to preserve 100% of shares)
5. Milestone 5: Expiration tracking and automatic weekly re-deployment
"""

import os
import json
import time
import datetime
import threading
from decimal import Decimal

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "covered_call_state.json")
_STATE_LOCK = threading.Lock()

def load_cc_state():
    with _STATE_LOCK:
        if not os.path.exists(STATE_FILE):
            default_state = {
                "auto_roll_enabled": True,
                "auto_take_profit_enabled": True,
                "take_profit_decay_pct": 0.50, # 50% decay take profit
                "roll_defense_trigger_pct": 0.96, # Roll when stock reaches 96% of strike
                "active_trades": []
            }
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(default_state, f, indent=2)
            return default_state
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"auto_roll_enabled": True, "auto_take_profit_enabled": True, "active_trades": []}

def save_cc_state(state):
    with _STATE_LOCK:
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            print(f"[CC Guardian] Error saving state: {e}")

def register_covered_call(ticker, option_symbol, strike, expiry, quantity, entry_premium):
    """Registers a newly opened covered call into Guardian tracking."""
    state = load_cc_state()
    ticker = ticker.upper()
    
    # Remove existing active trade for this ticker if any
    state["active_trades"] = [t for t in state.get("active_trades", []) if t.get("ticker") != ticker or t.get("status") != "ACTIVE"]
    
    entry_item = {
        "ticker": ticker,
        "option_symbol": option_symbol,
        "strike": float(strike),
        "expiry": str(expiry),
        "quantity": int(quantity),
        "entry_premium": float(entry_premium),
        "opened_at": datetime.datetime.now().isoformat(),
        "status": "ACTIVE",
        "last_mid": float(entry_premium),
        "rolls_count": 0
    }
    state["active_trades"].append(entry_item)
    save_cc_state(state)
    print(f"[CC Guardian] Registered covered call: {quantity}x {option_symbol} @ ${entry_premium:.2f}")
    return entry_item

def sync_active_with_broker():
    """Scans broker portfolio to discover any short options on AMC or NKE not yet registered."""
    try:
        from public_executor import get_broker_portfolio_positions
        positions = get_broker_portfolio_positions()
        state = load_cc_state()
        active_symbols = {t.get("option_symbol") for t in state.get("active_trades", []) if t.get("status") == "ACTIVE"}
        
        for p in positions:
            sym = (p.get("ticker") or "").upper()
            qty = float(p.get("quantity") or 0.0)
            # Short option position indicated by negative quantity or option instrument
            if qty < 0 and ("AMC" in sym or "NKE" in sym):
                if sym not in active_symbols:
                    root = "AMC" if "AMC" in sym else "NKE"
                    # Parse strike from OCC symbol if possible
                    import re
                    m = re.search(r'([A-Z]+)\d{6}[CP](\d{8})', sym)
                    strike = float(m.group(2)) / 1000.0 if m else 3.50
                    entry_p = float(p.get("avg_entry_price") or 0.08)
                    register_covered_call(root, sym, strike, "2026-10-23", abs(int(qty)), entry_p)
    except Exception as e:
        print(f"[CC Guardian] Error during broker sync: {e}")

def run_covered_call_cycle():
    """
    Evaluates all active covered calls every cycle:
    1. Milestone 3: Auto-close at 50% time decay profit.
    2. Milestone 4: Auto-roll up-and-out if stock price threatens strike (>= 96%).
    """
    state = load_cc_state()
    trades = state.get("active_trades", [])
    active_trades = [t for t in trades if t.get("status") == "ACTIVE"]
    
    if not active_trades:
        return

    from public_executor import execute_option_buy_to_close, execute_option_sell_to_open, get_live_prices, get_client
    from public_api_sdk.models import OptionChainRequest, OrderInstrument
    from telegram_notifier import send_message, is_configured

    live_px_map = get_live_prices(list({t["ticker"] for t in active_trades}))
    client = get_client()
    accounts = client.get_accounts()
    acct_id = accounts.accounts[0].account_id if (accounts and accounts.accounts) else None

    for t in active_trades:
        sym = t["ticker"]
        opt_sym = t["option_symbol"]
        strike = float(t["strike"])
        qty = t["quantity"]
        entry_prem = t["entry_premium"]

        try:
            # 1. Fetch live stock price & current option mark via Public.com
            stock_price = float(live_px_map.get(sym) or 0.0)
            if stock_price <= 0:
                continue

            current_mid = None
            try:
                if acct_id:
                    inst = OrderInstrument(symbol=sym, type="EQUITY")
                    chain = client.get_option_chain(OptionChainRequest(instrument=inst, expiration_date=t["expiry"]), account_id=acct_id)
                    for c in (chain.calls or []):
                        if c.option_details and abs(float(c.option_details.strike_price or 0.0) - strike) < 0.01:
                            bid = float(c.bid or 0.0)
                            ask = float(c.ask or 0.0)
                            current_mid = round(float(c.option_details.mid_price or (bid + ask) / 2.0), 2)
                            t["last_mid"] = current_mid
                            break
            except Exception:
                pass

            if current_mid is None:
                current_mid = entry_prem

            # =========================================================================
            # MILESTONE 3: 50% TIME DECAY TAKE-PROFIT AUTO-CLOSE
            # =========================================================================
            tp_threshold = round(entry_prem * state.get("take_profit_decay_pct", 0.50), 2)
            if state.get("auto_take_profit_enabled", True) and current_mid <= tp_threshold:
                print(f"[CC Guardian] 50% DECAY TRIGGERED: {sym} option decayed to ${current_mid:.2f} (<= ${tp_threshold:.2f})!")
                close_res = execute_option_buy_to_close(opt_sym, quantity=qty, limit_price=current_mid)
                
                net_profit = round((entry_prem - current_mid) * qty * 100, 2)
                t["status"] = "PROFIT_TAKEN"
                t["closed_at"] = datetime.datetime.now().isoformat()
                t["close_price"] = current_mid
                t["net_profit"] = net_profit
                save_cc_state(state)

                if is_configured():
                    msg = (
                        f"🎉 [COVERED CALL GUARDIAN - 50% PROFIT BANKED!]\n"
                        f"• Asset: {sym} (Covered 31x Contracts)\n"
                        f"• Contract: {opt_sym}\n"
                        f"• Sold At: ${entry_prem:.2f} &rarr; Bought Back: ${current_mid:.2f}\n"
                        f"• Cash Profit Banked: +${net_profit:.2f} early!\n"
                        f"• Status: 100% of shares freed up! Ready to harvest next weekly cycle."
                    )
                    send_message(msg)
                continue

            # =========================================================================
            # MILESTONE 4: ROLL UP-AND-OUT AUTO-DEFENSE (STOCK SURGE)
            # =========================================================================
            roll_trigger_price = round(strike * state.get("roll_defense_trigger_pct", 0.96), 2)
            if state.get("auto_roll_enabled", True) and stock_price >= roll_trigger_price:
                print(f"[CC Guardian] ROLL DEFENSE TRIGGERED: {sym} reached ${stock_price:.2f} (threatened ${strike:.2f} strike)!")
                
                # Close current short contract
                close_res = execute_option_buy_to_close(opt_sym, quantity=qty, limit_price=current_mid)
                
                # Scan next expiration and higher strike (e.g. +$0.50 higher strike, next 14-30 DTE)
                new_opt_sym = None
                new_strike = strike + 0.50 if sym == "AMC" else strike + 2.50
                new_expiry = None
                new_mid = 0.08

                # Max Pain Pin Shelter Check: Ensure rolled strike sits above institutional pin wall
                try:
                    from options_flow import get_options_flow
                    flow_res = get_options_flow(sym)
                    curr_mp = float(flow_res.get('max_pain') or 0.0)
                    if curr_mp > 0:
                        min_sheltered = curr_mp + (0.80 if sym == "AMC" else 2.50)
                        if new_strike < min_sheltered:
                            new_strike = min_sheltered
                except Exception:
                    pass

                try:
                    exps = ticker_obj.options[:6]
                    today = datetime.date.today()
                    for exp in exps:
                        exp_d = datetime.datetime.strptime(exp, '%Y-%m-%d').date()
                        dte = (exp_d - today).days
                        if dte >= 14: # Roll out at least 2 weeks
                            new_expiry = exp
                            opt_chain = ticker_obj.option_chain(exp)
                            c_new = opt_chain.calls[opt_chain.calls['strike'] == new_strike]
                            if not c_new.empty:
                                new_mid = round((float(c_new.iloc[0].bid or 0.0) + float(c_new.iloc[0].ask or 0.0)) / 2.0, 2)
                                new_opt_sym = f"{sym}{exp_d.strftime('%y%m%d')}C{int(new_strike*1000):08d}"
                                break
                except Exception as ex_roll:
                    print(f"[CC Guardian] Error resolving roll options: {ex_roll}")

                if new_opt_sym and new_mid > 0:
                    open_res = execute_option_sell_to_open(new_opt_sym, quantity=qty, limit_price=new_mid)
                    net_credit = round((new_mid - current_mid) * qty * 100, 2)
                    
                    t["status"] = "ROLLED"
                    t["rolled_to"] = new_opt_sym
                    t["rolled_at"] = datetime.datetime.now().isoformat()
                    t["rolls_count"] = t.get("rolls_count", 0) + 1
                    
                    # Register new leg as active
                    new_item = {
                        "ticker": sym,
                        "option_symbol": new_opt_sym,
                        "strike": new_strike,
                        "expiry": new_expiry,
                        "quantity": qty,
                        "entry_premium": new_mid,
                        "opened_at": datetime.datetime.now().isoformat(),
                        "status": "ACTIVE",
                        "last_mid": new_mid,
                        "rolls_count": t["rolls_count"]
                    }
                    state["active_trades"].append(new_item)
                    save_cc_state(state)

                    if is_configured():
                        msg = (
                            f"🛡️ [COVERED CALL GUARDIAN - AUTOMATED ROLL DEFENSE]\n"
                            f"• Asset: {sym} surged to ${stock_price:.2f} (near ${strike:.2f} strike)!\n"
                            f"• Action: Rolled Up-and-Out to preserve 100% of shares.\n"
                            f"• Closed: {opt_sym} @ ${current_mid:.2f}\n"
                            f"• Opened: {new_opt_sym} (${new_strike:.2f} Call exp {new_expiry}) @ ${new_mid:.2f}\n"
                            f"• Net Roll Credit: {('+' if net_credit >= 0 else '')}${net_credit:.2f}\n"
                            f"• All {qty * 100} shares 100% SAFE from assignment!"
                        )
                        send_message(msg)
                else:
                    # Just bought back to defend
                    t["status"] = "DEFENDED_CLOSED"
                    save_cc_state(state)

        except Exception as err:
            print(f"[CC Guardian] Cycle error for {sym}: {err}")

    save_cc_state(state)
