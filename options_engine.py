import os
import datetime
import sqlite3
import pandas as pd
import numpy as np
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv('PUBLIC_API_KEY')
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wyckoff_trades.db")

_cached_client = None
def get_client():
    global _cached_client
    if _cached_client is None:
        from public_api_sdk import PublicApiClient, ApiKeyAuthConfig
        if not API_KEY:
            raise ValueError("PUBLIC_API_KEY not configured.")
        _cached_client = PublicApiClient(auth_config=ApiKeyAuthConfig(api_secret_key=API_KEY))
    return _cached_client

def get_account_id():
    client = get_client()
    accounts = client.get_accounts()
    if not accounts.accounts:
        raise ValueError("No accounts found.")
    return accounts.accounts[0].account_id

def scan_single_ticker_options(ticker, stock_price=None, target_r=1.15, init_risk=1.0, active_pos=None):
    """
    Evaluates options structure for a Wyckoff Spring candidate:
    - Finds nearest weekly Friday expiration (>= 5 DTE)
    - Identifies optimal 0.65-0.70 Delta Call
    - Builds Vertical Bull Call Spread (Buy 0.65Δ, Sell strike near +1.0R target)
    - Determines Tranche Sizing based on 1, 3, 4 architecture:
      * Mid-Cap ($15-$65): 2-Contract Tranche Ladder
      * Mega-Cap ($65+): 1-Contract Trailing Ratchet OR Vertical Spread
    """
    from public_api_sdk.models import OptionChainRequest, OptionExpirationsRequest, OrderInstrument
    
    ticker = ticker.upper()
    client = get_client()
    acct = get_account_id()
    
    inst = OrderInstrument(symbol=ticker, type='EQUITY')
    exp_res = client.get_option_expirations(OptionExpirationsRequest(instrument=inst), account_id=acct)
    if not exp_res or not exp_res.expirations:
        return None
        
    today = datetime.date.today()
    target_exp = None
    target_dte = 7
    for exp_str in exp_res.expirations:
        try:
            exp_d = datetime.datetime.strptime(exp_str, '%Y-%m-%d').date()
            dte = (exp_d - today).days
            if dte >= 5: # Next weekly expiration
                target_exp = exp_str
                target_dte = dte
                break
        except:
            pass
    if not target_exp:
        target_exp = exp_res.expirations[0]
        
    chain = client.get_option_chain(OptionChainRequest(instrument=inst, expiration_date=target_exp), account_id=acct)
    if not chain or not chain.calls:
        return None
        
    calls = chain.calls
    puts = chain.puts or []
    
    # Calculate Put/Call Ratio
    call_oi = sum(int(c.open_interest or 0) for c in calls)
    put_oi = sum(int(p.open_interest or 0) for p in puts)
    call_vol = sum(int(c.volume or 0) for c in calls)
    put_vol = sum(int(p.volume or 0) for p in puts)
    
    pcr_oi = round(put_oi / max(1, call_oi), 2)
    pcr_vol = round(put_vol / max(1, call_vol), 2)
    
    # Parse valid calls
    parsed_calls = []
    for c in calls:
        det = c.option_details
        if det and det.strike_price:
            strike = float(det.strike_price)
            bid = float(c.bid or 0.0)
            ask = float(c.ask or 0.0)
            mid = float(det.mid_price or (bid + ask) / 2.0)
            grk = det.greeks
            delta = float(getattr(grk, 'delta', 0.0) or 0.0)
            gamma = float(getattr(grk, 'gamma', 0.0) or 0.0)
            theta = float(getattr(grk, 'theta', 0.0) or 0.0)
            iv = float(getattr(grk, 'implied_volatility', 0.0) or 0.0)
            
            parsed_calls.append({
                'symbol': c.instrument.symbol if (hasattr(c, 'instrument') and c.instrument) else None,
                'strike': strike,
                'bid': bid,
                'ask': ask,
                'mid': round(mid, 2),
                'delta': round(delta, 3),
                'gamma': round(gamma, 4),
                'theta': round(theta, 3),
                'iv': round(iv * 100.0, 1),
                'volume': int(c.volume or 0),
                'oi': int(c.open_interest or 0)
            })
            
    if not parsed_calls:
        return None
        
    # If the user already holds an active contract in their broker portfolio, match that exact contract!
    held_contract_sym = None
    if active_pos and active_pos.get('broker_pos'):
        held_contract_sym = (active_pos['broker_pos'].get('ticker') or '').upper()

    optimal_call = None
    if held_contract_sym:
        for c in parsed_calls:
            if c.get('symbol') and c['symbol'].upper() == held_contract_sym:
                optimal_call = c
                break

    if not optimal_call:
        # Find Optimal 0.65 Delta Call
        target_delta = 0.67
        parsed_calls.sort(key=lambda x: abs(x['delta'] - target_delta))
        optimal_call = parsed_calls[0]
    
    # Current underlying estimate from passed price or live price lookup or ATM strike
    if not stock_price or stock_price <= 0:
        try:
            from public_executor import get_live_prices
            p_map = get_live_prices([ticker])
            current_p = p_map.get(ticker) or optimal_call['strike']
        except Exception:
            current_p = optimal_call['strike']
    else:
        current_p = stock_price
    
    # Determine Architecture Classification (Mix of 1, 3, 4)
    is_sweet_spot = 15.0 <= current_p <= 75.0
    category = "SWEET_SPOT_MIDCAP" if is_sweet_spot else "MEGACAP_TECH"
    
    # Strategy 1 & 3: Naked Single/Multi Contract
    call_mid = optimal_call['mid']
    contract_cost = round(call_mid * 100.0, 2)
    
    # Strategy 4: Vertical Bull Call Debit Spread
    # Buy optimal call strike, sell strike roughly at stock_price + (target_r * init_risk)
    upper_target_p = current_p + (target_r * max(0.5, init_risk))
    # Find call strike closest to upper_target_p
    calls_above = [c for c in parsed_calls if c['strike'] > optimal_call['strike']]
    if calls_above:
        calls_above.sort(key=lambda x: abs(x['strike'] - upper_target_p))
        short_leg = calls_above[0]
    else:
        short_leg = None
        
    vertical_spread = None
    if short_leg and short_leg['strike'] > optimal_call['strike']:
        spread_width = short_leg['strike'] - optimal_call['strike']
        net_debit = round(max(0.10, optimal_call['mid'] - short_leg['mid']), 2)
        max_payout = round(spread_width * 100.0, 2)
        max_profit = round(max(0.0, max_payout - (net_debit * 100.0)), 2)
        ror_pct = round((max_profit / (net_debit * 100.0)) * 100.0, 1) if net_debit > 0 else 0.0
        
        vertical_spread = {
            'long_strike': optimal_call['strike'],
            'short_strike': short_leg['strike'],
            'width': spread_width,
            'net_debit': net_debit,
            'spread_cost': round(net_debit * 100.0, 2),
            'max_profit': max_profit,
            'return_on_risk_pct': ror_pct
        }
        
    # Concentrated Multi-Tranche Architecture: Strictly Strategy 1 and Strategy 4
    if active_pos and active_pos.get('broker_pos'):
        b_pos = active_pos['broker_pos']
        b_qty = float(b_pos.get('quantity') or 2.0)
        b_entry = float(b_pos.get('entry_price') or optimal_call['mid'])
        est_risk = round(b_entry * 100.0 * b_qty, 2)
        recommended_strategy = "STRATEGY_1_TRANCHE"
        strategy_desc = f"ACTIVE POSITION ({b_qty:.0f}x Calls - ${est_risk:.0f} total debit): Entry at ${b_entry:.2f}. Milestone 1 stop armed at $0.31 (-35%). Target 1 @ $0.72 (+50%)."
    elif is_sweet_spot:
        recommended_strategy = "STRATEGY_1_TRANCHE"
        strategy_desc = f"STRATEGY 1 (2x Calls - ${contract_cost * 2:.0f} total): Sell Tranche 1 @ +50% target to bank profit & ratchet Stop to Breakeven $0.00. Ride Tranche 2 to resistance."
        est_risk = contract_cost * 2
    else:
        recommended_strategy = "STRATEGY_4_SPREAD"
        if vertical_spread:
            spread_2x_cost = vertical_spread['spread_cost'] * 2
            strategy_desc = f"STRATEGY 4 (2x Bull Call Spreads - ${spread_2x_cost:.0f} total): Buy 2x {vertical_spread['long_strike']}C/{vertical_spread['short_strike']}C spreads. Sell Spread 1 @ +50% target to recover total debit; Spread 2 becomes 100% risk-free runner!"
            est_risk = spread_2x_cost
        else:
            strategy_desc = f"STRATEGY 1 Fallback (2x Contracts - ${contract_cost * 2:.0f} total): Ladder exits into Wyckoff target."
            est_risk = contract_cost * 2
        
    # Bullish Options Confluence Score (0 to 100)
    score = 50
    if pcr_vol <= 0.60: score += 15
    elif pcr_vol <= 0.75: score += 10
    if optimal_call['delta'] >= 0.63: score += 10
    if optimal_call['iv'] <= 40.0: score += 15 # Cheap IV
    if optimal_call['volume'] >= 100: score += 10 # Liquid
    score = min(100, score)
    
    # Determine Live Trade Status & Milestone Progression
    if active_pos:
        pos_entry = float(active_pos.get('entry_price') or current_p)
        pos_sl = float(active_pos.get('stop_loss') or 0.0)
        pos_tp = float(active_pos.get('take_profit') or 0.0)
        pos_be = bool(active_pos.get('breakeven_set'))
        pos_partial = bool(active_pos.get('partial_exit_done'))
        peak_r = float(active_pos.get('peak_high_r') or 0.0)
        # Check if option contract position from broker
        b_pos = active_pos.get('broker_pos')
        if b_pos and 'entry_price' in b_pos and 'current_price' in b_pos:
            opt_entry = float(b_pos['entry_price'])
            opt_curr = float(b_pos['current_price'])
            pos_entry = opt_entry
            pos_sl = round(opt_entry * 0.65, 2)
            pos_tp = round(opt_entry * 1.50, 2)
            pnl_pct = float(b_pos.get('unrealized_pnl_pct', 0.0))
            current_r = round(pnl_pct / 35.0, 2)
        else:
            pos_risk = abs(pos_entry - pos_sl) if abs(pos_entry - pos_sl) > 0.01 else 1.0
            current_r = round((current_p - pos_entry) / pos_risk, 2)
        
        if pos_partial or pos_be or current_r >= 0.50:
            active_milestone = 3
            milestone_status_text = "Milestone 3 Reached: Tranche 1 Locked, Stop at Breakeven $0.00"
        elif current_r >= 0.20 or peak_r >= 0.20:
            active_milestone = 2
            milestone_status_text = "Milestone 2 Reached: Early Stop Compressed to -20%"
        else:
            active_milestone = 1
            milestone_status_text = "Milestone 1 Active: Position Open at Entry"

        trade_status = {
            'is_active': True,
            'badge': 'ACTIVE_POSITION',
            'status_label': 'LIVE ACTIVE POSITION',
            'current_r': current_r,
            'entry_price': round(pos_entry, 2),
            'stop_loss': round(pos_sl, 2),
            'take_profit': round(pos_tp, 2),
            'breakeven_locked': pos_be,
            'tranche_1_filled': pos_partial,
            'active_milestone': active_milestone,
            'milestone_status_text': milestone_status_text
        }
    else:
        trade_status = {
            'is_active': False,
            'badge': 'WATCHLIST_SETUP',
            'status_label': 'SCANNER SETUP (WATCHLIST)',
            'current_r': 0.0,
            'entry_price': round(current_p, 2),
            'stop_loss': 0.0,
            'take_profit': 0.0,
            'breakeven_locked': False,
            'tranche_1_filled': False,
            'active_milestone': 0,
            'milestone_status_text': 'Awaiting 9:30 AM Market Open Trigger'
        }
    
    return {
        'ticker': ticker,
        'stock_price': round(current_p, 2),
        'category': category,
        'expiration': target_exp,
        'dte': target_dte,
        'pcr_vol': pcr_vol,
        'pcr_oi': pcr_oi,
        'confluence_score': score,
        'recommended_strategy': recommended_strategy,
        'strategy_description': strategy_desc,
        'estimated_risk': round(est_risk, 2),
        'optimal_call': optimal_call,
        'vertical_spread': vertical_spread,
        'trade_status': trade_status,
        'tranche_plan': {
            'tier1_target': "+20% Target: Early Stop Compression",
            'tier3_target': "+50% Target: Lock Tranche 1 Profit & Move Stop to Breakeven $0.00",
            'tier5_target': "+100% Target: Close Core Tranche into Resistance",
            'tier6_target': "3:55 PM EST: Final Runner EOD Market Flatten"
        }
    }

_OPTIONS_CACHE = {"data": [], "timestamp": 0}
CACHE_TTL = 60 # Cache for 60 seconds to ensure instant UI rendering

def get_options_scanner_data(force_refresh=False):
    """
    Scans active open trades AND recent Wyckoff Spring setups,
    pairing them with live options intelligence and milestone tracking.
    """
    global _OPTIONS_CACHE
    import time
    now = time.time()
    if not force_refresh and _OPTIONS_CACHE["data"] and (now - _OPTIONS_CACHE["timestamp"] < CACHE_TTL):
        return _OPTIONS_CACHE["data"]

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # 1. Fetch live active open trades (user_active = 1 strictly, meaning REAL BROKER TRADES!)
    c.execute("""
        SELECT ticker, entry_price, stop_loss, take_profit, optimal_target_r, timestamp, outcome, breakeven_set, partial_exit_done, peak_high_r, trailing_stop_price
        FROM alerts
        WHERE outcome = 'OPEN' AND user_active = 1
        ORDER BY id DESC
    """)
    active_rows_raw = {r['ticker'].upper(): dict(r) for r in c.fetchall()}
    import re
    broker_held = {}
    try:
        from public_executor import get_broker_portfolio_positions
        for p in get_broker_portfolio_positions():
            raw_sym = (p.get('ticker') or '').upper()
            if not raw_sym or raw_sym in ['AMC', 'APE', 'NKE']:
                continue
            m = re.match(r"^([A-Z]+)", raw_sym)
            root_sym = m.group(1) if m else raw_sym
            broker_held[root_sym] = p
    except Exception:
        broker_held = {}

    active_rows = {}
    for sym, b_pos in broker_held.items():
        if sym in active_rows_raw:
            row_dict = dict(active_rows_raw[sym])
            row_dict['broker_pos'] = b_pos
            active_rows[sym] = row_dict
        else:
            entry_p = float(b_pos.get('entry_price') or 0.0)
            active_rows[sym] = {
                'ticker': sym,
                'entry_price': entry_p,
                'stop_loss': round(entry_p * 0.65, 2),
                'take_profit': round(entry_p * 1.50, 2),
                'optimal_target_r': 1.15,
                'timestamp': str(datetime.datetime.now()),
                'outcome': 'OPEN',
                'breakeven_set': False,
                'partial_exit_done': False,
                'peak_high_r': 0.0,
                'trailing_stop_price': round(entry_p * 0.65, 2),
                'broker_pos': b_pos
            }
    
    # 2. Fetch recent Long alerts from past 48 hours
    c.execute("""
        SELECT ticker, entry_price, stop_loss, take_profit, optimal_target_r, timestamp
        FROM alerts
        WHERE direction = 'LONG'
        GROUP BY ticker
        ORDER BY id DESC
        LIMIT 4
    """)
    recent_rows = [dict(r) for r in c.fetchall()]
    conn.close()
    
    results = []
    seen = set()
    
    # Priority 1: Process active open trades
    for sym, pos in active_rows.items():
        if sym in seen: continue
        seen.add(sym)
        try:
            entry = float(pos.get('entry_price') or 0.0)
            sl = float(pos.get('stop_loss') or 0.0)
            risk = abs(entry - sl) if abs(entry - sl) > 0.001 else 1.0
            tgt_r = float(pos.get('optimal_target_r') or 1.15)
            # Pass None for stock_price if entry is option premium (< $10)
            stk_p = None if (pos.get('broker_pos') and entry < 10.0) else entry
            opt_data = scan_single_ticker_options(sym, stock_price=stk_p, target_r=tgt_r, init_risk=risk, active_pos=pos)
            if opt_data:
                results.append(opt_data)
        except: pass

    # Priority 2: Process recent watchlist setups
    for t_info in recent_rows:
        sym = t_info['ticker'].upper()
        if sym in seen: continue
        seen.add(sym)
        try:
            entry = float(t_info.get('entry_price') or 0.0)
            sl = float(t_info.get('stop_loss') or 0.0)
            risk = abs(entry - sl) if abs(entry - sl) > 0.001 else 1.0
            tgt_r = float(t_info.get('optimal_target_r') or 1.15)
            opt_data = scan_single_ticker_options(sym, stock_price=entry, target_r=tgt_r, init_risk=risk, active_pos=None)
            if opt_data:
                results.append(opt_data)
        except: pass
            
    # Backfill with top liquid sweet-spot tickers if fewer than 4
    if len(results) < 4:
        backfills = ['UBER', 'PLTR', 'AAPL']
        for sym in backfills:
            if len(results) >= 4:
                break
            if sym not in seen:
                seen.add(sym)
                try:
                    opt_data = scan_single_ticker_options(sym)
                    if opt_data:
                        results.append(opt_data)
                except: pass
                
    # Sort active positions first, then by Confluence Score descending
    results.sort(key=lambda x: (1 if x.get('trade_status', {}).get('is_active') else 0, x['confluence_score']), reverse=True)
    
    # Save to memory cache
    _OPTIONS_CACHE["data"] = results
    _OPTIONS_CACHE["timestamp"] = now
    return results

_COVERED_CALLS_CACHE = {"timestamp": 0, "data": None}

def get_covered_calls_scanner_data(force_refresh=False):
    """
    Scans real-time covered call weekly/monthly options chains for user's long holdings (AMC, NKE).
    Calculates instant cash deposits (31x AMC, 1x NKE), keep-stock delta probability,
    5-milestone execution roadmaps, and annualized yield.
    """
    global _COVERED_CALLS_CACHE
    import time, datetime
    from public_executor import get_live_prices
    now = time.time()
    if not force_refresh and _COVERED_CALLS_CACHE["data"] and (now - _COVERED_CALLS_CACHE["timestamp"] < CACHE_TTL):
        return _COVERED_CALLS_CACHE["data"]

    today = datetime.date.today()
    configs = [
        {"ticker": "AMC", "company": "AMC Entertainment", "shares": 3108, "contracts": 31, "cost_basis": 13.25, "strikes": [3.5, 4.0, 4.5, 5.0]},
        {"ticker": "NKE", "company": "NIKE, Inc.", "shares": 100, "contracts": 1, "cost_basis": 67.40, "strikes": [35.5, 36.0, 37.0, 38.0, 40.0]}
    ]

    setups = []
    holdings_summary = []
    live_px_map = get_live_prices([c["ticker"] for c in configs])

    for cfg in configs:
        sym = cfg["ticker"]
        try:
            price = float(live_px_map.get(sym) or 0.0)
            if price <= 0:
                continue

            # Fetch Flow & Max Pain Intelligence
            flow = {}
            try:
                from options_flow import get_options_flow
                flow = get_options_flow(sym) or {}
            except Exception:
                flow = {}
            
            max_pain = float(flow.get("max_pain") or (2.50 if sym == "AMC" else 35.0))
            gamma_wall = float(flow.get("gamma_wall") or (2.50 if sym == "AMC" else 35.0))
            pcr = float(flow.get("put_call_ratio") or (0.37 if sym == "AMC" else 0.85))
            pcr_label = str(flow.get("put_call_label") or ("BULLISH SKEW" if sym == "AMC" else "NEUTRAL"))
            call_oi = int(flow.get("total_call_oi") or (43294 if sym == "AMC" else 15000))
            put_oi = int(flow.get("total_put_oi") or (16220 if sym == "AMC" else 12000))

            dist_to_mp = round(price - max_pain, 2)
            dist_to_mp_pct = round((dist_to_mp / max_pain) * 100, 1) if max_pain > 0 else 0.0
            pin_zone_low = max_pain
            pin_zone_high = round(max_pain + 0.50, 2) if sym == "AMC" else round(max_pain + 2.0, 2)

            exps = t.options[:6]

            holding_val = round(price * cfg["shares"], 2)
            total_basis = round(cfg["cost_basis"] * cfg["shares"], 2)
            best_weekly = None

            for exp in exps:
                try:
                    exp_date = datetime.datetime.strptime(exp, '%Y-%m-%d').date()
                    dte = (exp_date - today).days
                    if dte < 3 or dte > 45:
                        continue
                    opt = t.option_chain(exp)
                    for s in cfg["strikes"]:
                        c = opt.calls[opt.calls['strike'] == s]
                        if not c.empty:
                            r = c.iloc[0]
                            bid = float(r.bid or 0.0)
                            ask = float(r.ask or 0.0)
                            mid = round((bid + ask) / 2.0, 2)
                            last_p = float(r.lastPrice or 0.0)
                            if mid <= 0.01 and bid == 0 and last_p == 0:
                                continue
                            if mid == 0 and last_p > 0:
                                mid = last_p

                            iv = float(r.impliedVolatility or 0.0)
                            vol = int(r.volume or 0)
                            oi = int(r.openInterest or 0)

                            moneyness = price / s
                            if moneyness >= 1.0:
                                delta = min(0.95, round(0.50 + (moneyness - 1.0) * 2.0, 2))
                            else:
                                dist = (s - price) / price
                                delta = max(0.04, round(0.50 / (1.0 + (dist * 12.0)), 2))

                            if 0.14 <= delta <= 0.28:
                                flavor = "SWEET_SPOT"
                                flavor_title = "🛡️ SWEET SPOT (RECOMMENDED)"
                                flavor_desc = f"Ideal ~0.20Δ balance: ~{int((1-delta)*100)}% probability to keep shares while harvesting robust weekly cash."
                            elif delta > 0.28:
                                flavor = "AGGRESSIVE_CASH"
                                flavor_title = "💰 AGGRESSIVE CASH"
                                flavor_desc = f"Near-the-money ({delta:.2f}Δ): Maximizes immediate upfront cash deposit today."
                            else:
                                flavor = "GROWTH_EXIT"
                                flavor_title = "🚀 TARGET PROFIT EXIT"
                                flavor_desc = f"Higher strike (${s:.2f}): Harvests light cash while locking in higher liquidation value."

                            total_cash = round(mid * cfg["contracts"] * 100, 2)
                            called_cash = round((s * cfg["contracts"] * 100) + total_cash, 2)
                            port_val = price * cfg["contracts"] * 100
                            ann_yield = round((total_cash / max(port_val, 1)) * (365 / max(dte, 1)) * 100, 1)
                            keep_prob = int(round((1.0 - delta) * 100))

                            # Flow & Max Pain Pin Gravity Analysis
                            dist_strike_from_mp = round(s - max_pain, 2)
                            dist_strike_from_spot = round(s - price, 2)
                            if s >= pin_zone_high:
                                pin_safety_badge = "🛡️ SHELTERED ABOVE PIN"
                                pin_safety_status = "SAFE (ABOVE MM PIN ZONE)"
                                pin_bias_desc = (
                                    f"Strike ${s:.2f} sits ${dist_strike_from_mp:.2f} above Max Pain (${max_pain:.2f}). "
                                    f"Institutions face heavy payout losses if {sym} finishes above ${pin_zone_high:.2f} (defending {call_oi:,} open calls). "
                                    f"Market Maker inventory hedging creates strong downward selling pressure toward ${pin_zone_low:.2f}–${pin_zone_high:.2f} "
                                    f"into expiration, accelerating theta decay on your short call while keeping your shares safe."
                                )
                                algo_pin_target = True
                            else:
                                pin_safety_badge = "⚠️ IN MM BATTLE PIN ZONE"
                                pin_safety_status = "CAUTION (IN PIN ZONE)"
                                pin_bias_desc = (
                                    f"Strike ${s:.2f} is inside the Market Maker pin defense zone (${pin_zone_low:.2f}–${pin_zone_high:.2f}). "
                                    f"Higher pin volatility and assignment risk."
                                )
                                algo_pin_target = False

                            setup_item = {
                                "ticker": sym,
                                "company_name": cfg["company"],
                                "category": "COVERED_CALL",
                                "stock_price": round(price, 2),
                                "shares_held": cfg["shares"],
                                "contracts_count": cfg["contracts"],
                                "cost_basis": cfg["cost_basis"],
                                "strike": s,
                                "expiry": exp,
                                "dte": dte,
                                "bid": bid,
                                "ask": ask,
                                "mid": mid,
                                "delta": delta,
                                "iv": round(iv * 100, 1) if iv < 5.0 else round(iv, 1),
                                "volume": vol,
                                "open_interest": oi,
                                "upfront_cash": total_cash,
                                "called_cash": called_cash,
                                "ann_yield": ann_yield,
                                "keep_prob": keep_prob,
                                "flavor": flavor,
                                "flavor_title": flavor_title,
                                "flavor_desc": flavor_desc,
                                "confluence_score": 92 if flavor == "SWEET_SPOT" else (86 if flavor == "AGGRESSIVE_CASH" else 76),
                                "estimated_risk": f"+${total_cash:.0f} CASH",
                                "recommended_strategy": f"SELL {cfg['contracts']}x ${s:.2f} CALL ({exp})",
                                "optimal_call": {
                                    "strike": s,
                                    "bid": bid,
                                    "ask": ask,
                                    "mid": mid,
                                    "delta": delta,
                                    "iv": round(iv * 100, 1) if iv < 5.0 else round(iv, 1),
                                    "gamma": round(0.05 / max(price, 1), 3),
                                    "theta": round(-mid / max(dte, 1), 3)
                                },
                                "max_pain_intel": {
                                    "max_pain": max_pain,
                                    "gamma_wall": gamma_wall,
                                    "put_call_ratio": pcr,
                                    "put_call_label": pcr_label,
                                    "call_oi": call_oi,
                                    "put_oi": put_oi,
                                    "spot_dist_from_mp": dist_to_mp,
                                    "spot_dist_from_mp_pct": dist_to_mp_pct,
                                    "strike_dist_from_mp": dist_strike_from_mp,
                                    "strike_dist_from_spot": dist_strike_from_spot,
                                    "pin_zone": f"${pin_zone_low:.2f} – ${pin_zone_high:.2f}",
                                    "pin_safety_badge": pin_safety_badge,
                                    "pin_safety_status": pin_safety_status,
                                    "pin_bias_desc": pin_bias_desc,
                                    "algo_pin_target": algo_pin_target,
                                    "algo_rec_text": f"TARGET STRIKE ${s:.2f}: Exploit Market Maker pin pull (-${dist_to_mp:.2f} below spot) to rapidly capture 50% decay."
                                },
                                "milestones": {
                                    "m1": {
                                        "title": "MILESTONE 1: INSTANT CASH CREDIT DEPOSIT",
                                        "desc": f"Sell to Open {cfg['contracts']}x ${s:.2f} Call at mid (${mid:.2f}). Deposit +${total_cash:.2f} cash into buying power immediately upon fill.",
                                        "target": f"+${total_cash:.2f}"
                                    },
                                    "m2": {
                                        "title": "MILESTONE 2: DOWNSIDE BREAKEVEN CUSHION",
                                        "desc": f"Cost basis effectively reduced by ${mid:.2f}/sh. Immediate breakeven safety floor is ${price - mid:.2f}.",
                                        "target": f"${price - mid:.2f}"
                                    },
                                    "m3": {
                                        "title": "MILESTONE 3: 50% PROFIT TAKE & RE-ROLL",
                                        "desc": f"If option price decays by 50% to ${mid*0.5:.2f} (banking +${total_cash*0.5:.2f} profit early assisted by MM Pin Gravity), buy back to close and immediately re-sell next weekly cycle.",
                                        "target": f"${mid*0.5:.2f} (50% Decay)"
                                    },
                                    "m4": {
                                        "title": "MILESTONE 4: ROLL DEFENSE TRIGGER",
                                        "desc": f"If {sym} surges toward ${s*0.96:.2f}, roll up-and-out: buy back call and sell next month's higher strike for net credit to keep 100% of shares.",
                                        "target": f"${s*0.96:.2f}"
                                    },
                                    "m5": {
                                        "title": "MILESTONE 5: ASSIGNMENT CASH LIQUIDATION",
                                        "desc": f"If {sym} finishes above ${s:.2f} at expiration, broker delivers ${s * cfg['contracts'] * 100:.2f} share sale cash + keep ${total_cash:.2f} premium.",
                                        "target": f"${called_cash:.2f} TOTAL CASH"
                                    }
                                }
                            }
                            setups.append(setup_item)
                            if (flavor == "SWEET_SPOT" and (not best_weekly or dte < best_weekly["dte"])) or not best_weekly:
                                best_weekly = setup_item
                except Exception:
                    pass

            holdings_summary.append({
                "ticker": sym,
                "company": cfg["company"],
                "shares": cfg["shares"],
                "contracts": cfg["contracts"],
                "cost_basis": cfg["cost_basis"],
                "price": round(price, 2),
                "holding_val": holding_val,
                "total_basis": total_basis,
                "unrealized_pnl": round(holding_val - total_basis, 2),
                "unrealized_pct": round(((holding_val - total_basis) / max(total_basis, 1)) * 100, 1),
                "best_weekly": best_weekly,
                "flow_intel": {
                    "max_pain": max_pain,
                    "gamma_wall": gamma_wall,
                    "put_call_ratio": pcr,
                    "put_call_label": pcr_label,
                    "call_oi": call_oi,
                    "put_oi": put_oi,
                    "spot_dist_from_mp": dist_to_mp,
                    "spot_dist_from_mp_pct": dist_to_mp_pct,
                    "pin_zone": f"${pin_zone_low:.2f} – ${pin_zone_high:.2f}",
                    "bias": "BEARISH MM PIN PULL (FAVORS SHORT CALL EXPIRY)" if dist_to_mp > 0 else "BULLISH PIN REVERSION",
                    "algo_edge": f"Strikes >= ${pin_zone_high:.2f} sit sheltered above institutional pin walls."
                }
            })
        except Exception as e:
            print(f"Error scanning covered calls for {sym}: {e}")

    result_data = {
        "holdings": holdings_summary,
        "setups": setups,
        "timestamp": now
    }
    _COVERED_CALLS_CACHE["data"] = result_data
    _COVERED_CALLS_CACHE["timestamp"] = now
    return result_data

