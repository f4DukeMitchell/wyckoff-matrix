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
        
    # Find Optimal 0.65 Delta Call
    target_delta = 0.67
    parsed_calls.sort(key=lambda x: abs(x['delta'] - target_delta))
    optimal_call = parsed_calls[0]
    
    # Current underlying estimate from ATM strike or passed price
    current_p = stock_price if (stock_price and stock_price > 0) else optimal_call['strike']
    
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
    if is_sweet_spot:
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

def get_options_scanner_data():
    """
    Scans active open trades AND recent Wyckoff Spring setups,
    pairing them with live options intelligence and milestone tracking.
    """
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
    try:
        from public_executor import get_broker_portfolio_positions
        held_tickers = set(p['ticker'].upper() for p in get_broker_portfolio_positions() if p.get('ticker') and p.get('ticker').upper() not in ['AMC', 'APE', 'NKE'])
        active_rows = {k: v for k, v in active_rows_raw.items() if k in held_tickers}
    except:
        active_rows = {}
    
    # 2. Fetch recent Long alerts from past 48 hours
    c.execute("""
        SELECT ticker, entry_price, stop_loss, take_profit, optimal_target_r, timestamp
        FROM alerts
        WHERE direction = 'LONG'
        GROUP BY ticker
        ORDER BY id DESC
        LIMIT 15
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
            opt_data = scan_single_ticker_options(sym, stock_price=entry, target_r=tgt_r, init_risk=risk, active_pos=pos)
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
            
    # Backfill with top liquid sweet-spot tickers if fewer than 6
    if len(results) < 6:
        backfills = ['UBER', 'PLTR', 'CCL', 'NVDA', 'AAPL', 'AMD']
        for sym in backfills:
            if sym not in seen:
                seen.add(sym)
                try:
                    opt_data = scan_single_ticker_options(sym)
                    if opt_data:
                        results.append(opt_data)
                except: pass
                
    # Sort active positions first, then by Confluence Score descending
    results.sort(key=lambda x: (1 if x.get('trade_status', {}).get('is_active') else 0, x['confluence_score']), reverse=True)
    return results
