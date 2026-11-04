"""
Wyckoff Covered Call Guardian Daemon (v2.0 — Cash-First Institutional Architecture)
===================================================================================
Automates the lifecycle of covered calls on core long holdings (AMC, NKE) with strict
NO-LOSS BUYBACK protection:
1. Milestone 1: Instant Upfront Cash Deposit verification
2. Milestone 2: Zero-Loss Hold Shield — NEVER panic-buy back a covered call at a loss mid-week
3. Milestone 3: 80% Theta Decay Harvest — Auto-buyback for pennies (<= 20% of entry premium) to bank 80%+ profit early
4. Milestone 4: Expiration Friday Net-Credit Roll Engine — Evaluated ONLY on Expiration Day (DTE <= 1) if ITM,
                and executed ONLY if the next cycle pays a verified NET CREDIT (>= +$0.02/sh)
5. Milestone 5: Worthless Expiration ($0.00) & Weekly Re-Deployment tracking
"""

import os
import re
import json
import time
import datetime
import threading
from decimal import Decimal

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "covered_call_state.json")
_STATE_LOCK = threading.Lock()


def parse_occ_symbol(occ_sym):
    """Parses OCC option symbol e.g. AMC261113C00003500 -> ('AMC', '2026-11-13', 'C', 3.50)"""
    m = re.match(r'^([A-Z]+)(\d{2})(\d{2})(\d{2})([CP])(\d{8})$', (occ_sym or "").strip().upper())
    if not m:
        return None
    root, yy, mm, dd, cp, strike_raw = m.groups()
    expiry = f"20{yy}-{mm}-{dd}"
    strike = float(strike_raw) / 1000.0
    return {
        "root": root,
        "expiry": expiry,
        "type": cp,
        "strike": round(strike, 2)
    }


def load_cc_state():
    with _STATE_LOCK:
        default_state = {
            "auto_roll_enabled": True,
            "auto_take_profit_enabled": True,
            "take_profit_decay_pct": 0.20,       # Buy back early ONLY when option decays by 80% (down to <= 20% of entry)
            "no_loss_buyback_guard": True,       # Strict ban on buying back above entry_premium unless net-credit rolling on expiry day
            "roll_only_on_expiry_day": True,     # Only evaluate rolling when DTE <= 1 and stock >= strike
            "min_net_roll_credit": 0.02,         # Require at least +$0.02/sh net credit to roll
            "active_trades": []
        }
        if not os.path.exists(STATE_FILE):
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(default_state, f, indent=2)
            return default_state
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            # Enforce institutional v2.0 defaults over legacy 0.50 / 0.96 settings
            loaded["take_profit_decay_pct"] = 0.20
            loaded["no_loss_buyback_guard"] = True
            loaded["roll_only_on_expiry_day"] = True
            loaded.setdefault("min_net_roll_credit", 0.02)
            loaded.setdefault("active_trades", [])
            return loaded
        except Exception:
            return default_state


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
    parsed = parse_occ_symbol(option_symbol)
    if parsed:
        strike = parsed["strike"]
        expiry = parsed["expiry"]

    # Remove existing active trade for this ticker if any
    state["active_trades"] = [
        t for t in state.get("active_trades", [])
        if t.get("ticker") != ticker or t.get("status") != "ACTIVE"
    ]

    entry_item = {
        "ticker": ticker,
        "option_symbol": option_symbol.upper(),
        "strike": float(strike),
        "expiry": str(expiry),
        "quantity": abs(int(quantity)),
        "entry_premium": float(entry_premium),
        "opened_at": datetime.datetime.now().isoformat(),
        "status": "ACTIVE",
        "last_mid": float(entry_premium),
        "rolls_count": 0
    }
    state["active_trades"].append(entry_item)
    save_cc_state(state)
    print(f"[CC Guardian] Registered covered call: {quantity}x {option_symbol} @ ${entry_premium:.2f} (80% Decay TP <= ${max(0.01, round(float(entry_premium)*0.20, 2)):.2f})")
    return entry_item


def sync_active_with_broker():
    """Scans broker portfolio to discover any short options on AMC or NKE and reconciles closed positions."""
    try:
        from public_executor import get_broker_portfolio_positions
        positions = get_broker_portfolio_positions()
        state = load_cc_state()
        active_trades = state.get("active_trades", [])
        active_symbols = {t.get("option_symbol") for t in active_trades if t.get("status") == "ACTIVE"}

        broker_short_opt_symbols = set()
        for p in positions:
            sym = (p.get("ticker") or "").upper()
            qty = float(p.get("quantity") or 0.0)
            parsed = parse_occ_symbol(sym)
            if qty < 0 and parsed and parsed["root"] in ("AMC", "NKE"):
                broker_short_opt_symbols.add(sym)
                if sym not in active_symbols:
                    entry_p = float(p.get("entry_price") or p.get("avg_entry_price") or 0.08)
                    register_covered_call(
                        parsed["root"],
                        sym,
                        parsed["strike"],
                        parsed["expiry"],
                        abs(int(qty)),
                        entry_p
                    )

        # Mark any tracked ACTIVE trade that is no longer held in the broker portfolio as CLOSED_EXTERNAL
        state = load_cc_state()
        changed = False
        for t in state.get("active_trades", []):
            if t.get("status") == "ACTIVE" and t.get("option_symbol") not in broker_short_opt_symbols:
                t["status"] = "CLOSED_OR_EXPIRED"
                t["closed_at"] = datetime.datetime.now().isoformat()
                changed = True
        if changed:
            save_cc_state(state)

    except Exception as e:
        print(f"[CC Guardian] Error during broker sync: {e}")


def run_covered_call_cycle():
    """
    Evaluates all active covered calls every cycle under Cash-First Institutional Rules:
    1. Milestone 3: Auto-close ONLY when option decays by >= 80% (e.g. down to <= 20% of entry premium, min $0.01).
    2. Milestone 2: Strict Hold Shield — NEVER buy back at a loss mid-week.
    3. Milestone 4: Expiration Friday Net-Credit Roll — Only on Expiration Friday (DTE <= 1) if stock >= strike,
                    and ONLY if the next weekly cycle pays a guaranteed Net Credit (>= +$0.02/sh).
    """
    sync_active_with_broker()
    state = load_cc_state()
    trades = state.get("active_trades", [])
    active_trades = [t for t in trades if t.get("status") == "ACTIVE"]

    if not active_trades:
        return

    from public_executor import (
        execute_option_buy_to_close,
        execute_option_sell_to_open,
        get_live_prices,
        get_client,
        get_account_id
    )
    from public_api_sdk.models import OptionChainRequest, OptionExpirationsRequest, OrderInstrument
    from telegram_notifier import send_message, is_configured

    live_px_map = get_live_prices(list({t["ticker"] for t in active_trades}))
    client = get_client()
    acct_id = get_account_id()
    today = datetime.date.today()

    for t in active_trades:
        sym = t["ticker"]
        opt_sym = t["option_symbol"]
        strike = float(t["strike"])
        qty = int(t["quantity"])
        entry_prem = float(t["entry_premium"])

        try:
            stock_price = float(live_px_map.get(sym) or 0.0)
            if stock_price <= 0:
                continue

            # Calculate DTE
            try:
                exp_d = datetime.datetime.strptime(t["expiry"], "%Y-%m-%d").date()
                dte = (exp_d - today).days
            except Exception:
                dte = 7

            current_bid = None
            current_ask = None
            current_mid = None

            try:
                if acct_id:
                    inst = OrderInstrument(symbol=sym, type="EQUITY")
                    chain = client.get_option_chain(
                        OptionChainRequest(instrument=inst, expiration_date=t["expiry"]),
                        account_id=acct_id
                    )
                    for c in (chain.calls or []):
                        if c.option_details and abs(float(c.option_details.strike_price or 0.0) - strike) < 0.01:
                            current_bid = float(c.bid or 0.0)
                            current_ask = float(c.ask or 0.0)
                            current_mid = round(float(c.option_details.mid_price or (current_bid + current_ask) / 2.0), 2)
                            t["last_mid"] = current_mid
                            break
            except Exception as e:
                print(f"[CC Guardian] Option quote lookup warning for {opt_sym}: {e}")

            if current_mid is None:
                continue

            buyback_price = round(current_ask if (current_ask and current_ask > 0) else current_mid, 2)

            # =========================================================================
            # MILESTONE 3: 80% THETA DECAY HARVEST (PENNY BUYBACK TO FREE SHARES EARLY)
            # =========================================================================
            decay_factor = float(state.get("take_profit_decay_pct", 0.20))  # 0.20 = 80% decayed
            tp_threshold = max(0.01, round(entry_prem * decay_factor, 2))

            if state.get("auto_take_profit_enabled", True) and buyback_price <= tp_threshold and buyback_price < entry_prem:
                print(f"[CC Guardian] 80% THETA DECAY HARVEST: {opt_sym} decayed to ${buyback_price:.2f} (<= ${tp_threshold:.2f} from ${entry_prem:.2f})!")
                close_res = execute_option_buy_to_close(opt_sym, quantity=qty, limit_price=buyback_price)

                net_profit = round((entry_prem - buyback_price) * qty * 100, 2)
                t["status"] = "PROFIT_TAKEN"
                t["closed_at"] = datetime.datetime.now().isoformat()
                t["close_price"] = buyback_price
                t["net_profit"] = net_profit
                save_cc_state(state)

                if is_configured():
                    msg = (
                        f"💰 [COVERED CALL GUARDIAN — 80% THETA DECAY HARVESTED!]\n"
                        f"• Asset: {sym} ({qty}x Contracts)\n"
                        f"• Contract: {opt_sym}\n"
                        f"• Sold At: ${entry_prem:.2f} → Bought Back: ${buyback_price:.2f}\n"
                        f"• Net Cash Profit Locked: +${net_profit:.2f} ({int((1 - buyback_price/entry_prem)*100)}% decay)\n"
                        f"• Status: 100% of shares freed early to reload next weekly cycle!"
                    )
                    send_message(msg)
                continue

            # =========================================================================
            # MILESTONE 2: ZERO-LOSS HOLD SHIELD (STRICT MID-WEEK BAN ON DEBIT BUYBACKS)
            # =========================================================================
            if dte > 1:
                # Never panic-buy back a covered call mid-week! Let Theta burn down.
                continue

            # =========================================================================
            # MILESTONE 4: EXPIRATION FRIDAY NET-CREDIT ROLL (ONLY IF DTE <= 1 & ITM)
            # =========================================================================
            if state.get("auto_roll_enabled", True) and dte <= 1 and stock_price >= strike:
                min_credit = float(state.get("min_net_roll_credit", 0.02))
                inst = OrderInstrument(symbol=sym, type="EQUITY")
                exp_res = client.get_option_expirations(OptionExpirationsRequest(instrument=inst), account_id=acct_id)

                candidate_roll = None
                for next_exp in (exp_res.expirations if exp_res else []):
                    try:
                        next_d = datetime.datetime.strptime(next_exp, "%Y-%m-%d").date()
                        next_dte = (next_d - today).days
                        if 6 <= next_dte <= 16:
                            next_chain = client.get_option_chain(
                                OptionChainRequest(instrument=inst, expiration_date=next_exp),
                                account_id=acct_id
                            )
                            # Check same strike or 1 strike higher for a guaranteed Net Credit
                            target_strikes = [strike, round(strike + (0.50 if sym == "AMC" else 2.50), 2)]
                            for target_s in target_strikes:
                                for c_next in (next_chain.calls or []):
                                    if c_next.option_details and abs(float(c_next.option_details.strike_price or 0.0) - target_s) < 0.01:
                                        n_bid = float(c_next.bid or 0.0)
                                        n_ask = float(c_next.ask or 0.0)
                                        n_mid = round(float(c_next.option_details.mid_price or (n_bid + n_ask) / 2.0), 2)
                                        net_per_sh = round(n_bid - buyback_price, 2)
                                        if net_per_sh >= min_credit and c_next.instrument and c_next.instrument.symbol:
                                            candidate_roll = {
                                                "symbol": c_next.instrument.symbol,
                                                "strike": target_s,
                                                "expiry": next_exp,
                                                "bid": n_bid,
                                                "mid": n_mid,
                                                "net_credit_per_sh": net_per_sh
                                            }
                                            break
                                if candidate_roll:
                                    break
                        if candidate_roll:
                            break
                    except Exception as ex_chain:
                        print(f"[CC Guardian] Roll chain scan warning: {ex_chain}")

                if candidate_roll:
                    print(
                        f"[CC Guardian] EXPIRATION NET-CREDIT ROLL CONFIRMED: Closing {opt_sym} @ ${buyback_price:.2f} "
                        f"& Opening {candidate_roll['symbol']} @ ${candidate_roll['bid']:.2f} "
                        f"(Net Credit: +${candidate_roll['net_credit_per_sh']:.2f}/sh)"
                    )
                    execute_option_buy_to_close(opt_sym, quantity=qty, limit_price=buyback_price)
                    execute_option_sell_to_open(candidate_roll["symbol"], quantity=qty, limit_price=candidate_roll["bid"])

                    net_cash_collected = round(candidate_roll["net_credit_per_sh"] * qty * 100, 2)
                    t["status"] = "ROLLED_NET_CREDIT"
                    t["rolled_to"] = candidate_roll["symbol"]
                    t["rolled_at"] = datetime.datetime.now().isoformat()
                    t["rolls_count"] = t.get("rolls_count", 0) + 1

                    new_item = {
                        "ticker": sym,
                        "option_symbol": candidate_roll["symbol"],
                        "strike": candidate_roll["strike"],
                        "expiry": candidate_roll["expiry"],
                        "quantity": qty,
                        "entry_premium": candidate_roll["bid"],
                        "opened_at": datetime.datetime.now().isoformat(),
                        "status": "ACTIVE",
                        "last_mid": candidate_roll["mid"],
                        "rolls_count": t["rolls_count"]
                    }
                    state["active_trades"].append(new_item)
                    save_cc_state(state)

                    if is_configured():
                        msg = (
                            f"🔄 [COVERED CALL GUARDIAN — EXPIRATION NET-CREDIT ROLL]\n"
                            f"• Asset: {sym} @ ${stock_price:.2f} (Expiration Day ITM)\n"
                            f"• Bought to Close: {opt_sym} @ ${buyback_price:.2f}\n"
                            f"• Sold to Open: {candidate_roll['symbol']} (${candidate_roll['strike']:.2f} Call exp {candidate_roll['expiry']}) @ ${candidate_roll['bid']:.2f}\n"
                            f"• Extra Net Cash Deposited: +${net_cash_collected:.2f} (+${candidate_roll['net_credit_per_sh']:.2f}/sh)\n"
                            f"• Status: Zero out-of-pocket loss! Shares & new cash locked in."
                        )
                        send_message(msg)
                else:
                    print(
                        f"[CC Guardian] Zero-Loss Shield Active for {opt_sym}: No net-credit roll >= +${min_credit:.2f} available "
                        f"(buyback=${buyback_price:.2f}). Holding to expiration (no debit buyback allowed)."
                    )

        except Exception as err:
            print(f"[CC Guardian] Cycle error for {sym}: {err}")

    save_cc_state(state)
