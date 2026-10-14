import streamlit as st
import os
from dotenv import load_dotenv
from public_api_sdk import PublicApiClient, ApiKeyAuthConfig
from public_api_sdk.models import OptionChainRequest, OptionExpirationsRequest, OrderInstrument

load_dotenv()
API_KEY = os.getenv('PUBLIC_API_KEY')

import datetime
import math

@st.cache_data(ttl=300)
def get_options_flow(ticker, curr_price=None):
    """
    Analyze the options chain and institutional positioning using Public.com API.
    """
    result = {
        'ticker': ticker,
        'put_call_ratio': 0.0,
        'put_call_label': 'N/A',
        'vol_put_call_ratio': 0.0,
        'max_pain': 0.0,
        'unusual_calls': [],
        'unusual_puts': [],
        'total_call_oi': 0,
        'total_put_oi': 0,
        'total_call_vol': 0,
        'total_put_vol': 0,
        'call_oi_pct': 50.0,
        'put_oi_pct': 50.0,
        'nearest_expiry': 'N/A',
        'dte': 0,
        'gamma_wall': 0.0,
        'atm_iv': 0.0,
        'expected_move': 0.0,
        'expected_move_pct': 0.0,
        'dist_gamma_wall': 0.0,
        'dist_gamma_wall_pct': 0.0,
        'dist_max_pain': 0.0,
        'dist_max_pain_pct': 0.0,
        'top_calls': [],
        'top_puts': [],
        'net_sentiment': 'N/A',
    }
    
    if not API_KEY:
        return result
        
    try:
        client = PublicApiClient(auth_config=ApiKeyAuthConfig(api_secret_key=API_KEY))
        accounts = client.get_accounts()
        if not accounts.accounts: return result
        account_id = accounts.accounts[0].account_id
        
        inst = OrderInstrument(symbol=ticker, type="EQUITY")
        exp_req = OptionExpirationsRequest(instrument=inst)
        exp_res = client.get_option_expirations(exp_req, account_id=account_id)
        
        if not exp_res.expirations:
            return result
            
        # Get the first two expirations to find liquidity
        nearest = exp_res.expirations[0]
        if len(exp_res.expirations) > 1:
            nearest = exp_res.expirations[1]
            
        result['nearest_expiry'] = nearest
        try:
            exp_dt = datetime.datetime.strptime(nearest, '%Y-%m-%d').date()
            today = datetime.date.today()
            dte = max(1, (exp_dt - today).days)
            result['dte'] = dte
        except Exception:
            result['dte'] = 7
        
        chain_req = OptionChainRequest(instrument=inst, expiration_date=nearest)
        chain_res = client.get_option_chain(chain_req, account_id=account_id)
        
        if not chain_res.calls and not chain_res.puts:
            return result
            
        calls = chain_res.calls
        puts = chain_res.puts
                
        total_call_oi = sum(int(c.open_interest or 0) for c in calls)
        total_put_oi = sum(int(p.open_interest or 0) for p in puts)
        total_call_vol = sum(int(c.volume or 0) for c in calls)
        total_put_vol = sum(int(p.volume or 0) for p in puts)
        
        result['total_call_oi'] = total_call_oi
        result['total_put_oi'] = total_put_oi
        result['total_call_vol'] = total_call_vol
        result['total_put_vol'] = total_put_vol
        
        total_oi = total_call_oi + total_put_oi
        if total_oi > 0:
            result['call_oi_pct'] = round((total_call_oi / total_oi) * 100.0, 1)
            result['put_oi_pct'] = round((total_put_oi / total_oi) * 100.0, 1)
        
        if total_call_oi > 0:
            pcr = total_put_oi / total_call_oi
            result['put_call_ratio'] = round(pcr, 2)
            if pcr > 1.2: result['put_call_label'] = 'BEARISH SKEW'
            elif pcr < 0.7: result['put_call_label'] = 'BULLISH SKEW'
            else: result['put_call_label'] = 'NEUTRAL'

        if total_call_vol > 0:
            result['vol_put_call_ratio'] = round(total_put_vol / total_call_vol, 2)
            
        # Top Call & Put Strikes by Open Interest
        top_c = sorted([
            {'strike': float(c.option_details.strike_price), 'oi': int(c.open_interest or 0), 'vol': int(c.volume or 0)}
            for c in calls if c.option_details and float(c.option_details.strike_price or 0) > 0
        ], key=lambda x: x['oi'], reverse=True)[:3]
        result['top_calls'] = top_c

        top_p = sorted([
            {'strike': float(p.option_details.strike_price), 'oi': int(p.open_interest or 0), 'vol': int(p.volume or 0)}
            for p in puts if p.option_details and float(p.option_details.strike_price or 0) > 0
        ], key=lambda x: x['oi'], reverse=True)[:3]
        result['top_puts'] = top_p
            
        # Gamma Wall & Max Pain
        all_strikes = {}
        strike_call_oi = {}
        strike_put_oi = {}

        for c in calls:
            s = float(c.option_details.strike_price) if c.option_details else 0
            if s > 0:
                oi = int(c.open_interest or 0)
                all_strikes[s] = all_strikes.get(s, 0) + oi
                strike_call_oi[s] = strike_call_oi.get(s, 0) + oi

        for p in puts:
            s = float(p.option_details.strike_price) if p.option_details else 0
            if s > 0:
                oi = int(p.open_interest or 0)
                all_strikes[s] = all_strikes.get(s, 0) + oi
                strike_put_oi[s] = strike_put_oi.get(s, 0) + oi

        if all_strikes and max(all_strikes.values()) > 0:
            gamma_wall = max(all_strikes, key=all_strikes.get)
            result['gamma_wall'] = float(gamma_wall)

            # --- MAX PAIN CALCULATION ---
            sorted_strikes = sorted(all_strikes.keys())
            min_loss = float('inf')
            max_pain_strike = None

            for eval_strike in sorted_strikes:
                total_loss = 0.0
                for c_strike, c_oi in strike_call_oi.items():
                    if eval_strike > c_strike:
                        total_loss += (eval_strike - c_strike) * c_oi * 100.0

                for p_strike, p_oi in strike_put_oi.items():
                    if eval_strike < p_strike:
                        total_loss += (p_strike - eval_strike) * p_oi * 100.0

                if total_loss < min_loss:
                    min_loss = total_loss
                    max_pain_strike = eval_strike

            if max_pain_strike is not None:
                result['max_pain'] = float(max_pain_strike)
            
            # Extract IV at the Gamma Wall strike
            for c in calls + puts:
                if c.option_details and float(c.option_details.strike_price) == gamma_wall:
                    if c.option_details.greeks and c.option_details.greeks.implied_volatility:
                        result['atm_iv'] = float(c.option_details.greeks.implied_volatility)
                        break

            # Calculate Expected Move if curr_price available
            if curr_price and curr_price > 0 and result['atm_iv'] > 0:
                em = curr_price * result['atm_iv'] * math.sqrt(result['dte'] / 365.0)
                result['expected_move'] = round(em, 2)
                result['expected_move_pct'] = round((em / curr_price) * 100.0, 2)
                
            if curr_price and curr_price > 0:
                if result['gamma_wall'] > 0:
                    diff_gw = curr_price - result['gamma_wall']
                    result['dist_gamma_wall'] = round(diff_gw, 2)
                    result['dist_gamma_wall_pct'] = round((diff_gw / result['gamma_wall']) * 100.0, 2)
                if result['max_pain'] > 0:
                    diff_mp = curr_price - result['max_pain']
                    result['dist_max_pain'] = round(diff_mp, 2)
                    result['dist_max_pain_pct'] = round((diff_mp / result['max_pain']) * 100.0, 2)
            
        # Sentiment
        if result['put_call_label'] != 'N/A':
            result['net_sentiment'] = result['put_call_label'].replace(' SKEW', '')
            
    except Exception as e:
        print(f"Error fetching options from Public: {e}")
        pass
        
    return result

def get_public_quotes(ticker):
    res = {
        'bid_ask_ratio': 0.0,
        'spread_width_pct': 0.0,
        'bid': 0.0,
        'ask': 0.0,
        'bid_size': 0,
        'ask_size': 0,
        'last': 0.0,
        'day_volume': 0,
        'change_pct': 0.0,
        'imbalance_label': 'BALANCED'
    }
    if not API_KEY: return res
    try:
        client = PublicApiClient(auth_config=ApiKeyAuthConfig(api_secret_key=API_KEY))
        accounts = client.get_accounts()
        if not accounts.accounts: return res
        account_id = accounts.accounts[0].account_id
        
        inst = OrderInstrument(symbol=ticker.upper(), type="EQUITY")
        quotes = client.get_quotes(instruments=[inst], account_id=account_id)
        if quotes:
            q = quotes[0]
            bid = float(q.bid or 0.0)
            ask = float(q.ask or 0.0)
            bid_size = int(q.bid_size or 0)
            ask_size = int(q.ask_size or 0)
            last = float(q.last or 0.0)
            vol = int(q.volume or 0)
            
            res['bid'] = round(bid, 2)
            res['ask'] = round(ask, 2)
            res['bid_size'] = bid_size
            res['ask_size'] = ask_size
            res['last'] = round(last, 2)
            res['day_volume'] = vol
            
            if q.one_day_change and q.one_day_change.percent_change is not None:
                res['change_pct'] = round(float(q.one_day_change.percent_change), 2)
                
            if ask_size > 0:
                bar = round(bid_size / ask_size, 2)
                res['bid_ask_ratio'] = bar
                if bar >= 1.5: res['imbalance_label'] = 'BID HEAVY (BUY SUPPORT)'
                elif bar <= 0.67: res['imbalance_label'] = 'ASK HEAVY (SELL PRESSURE)'
                else: res['imbalance_label'] = 'BALANCED'
                
            if bid > 0 and ask > 0:
                res['spread_width_pct'] = round(((ask - bid) / bid) * 100.0, 3)
                res['spread_dollar'] = round(ask - bid, 2)
    except Exception as e:
        print(f"Error fetching quotes from Public: {e}")
        pass
    return res

if __name__ == '__main__':
    flow = get_options_flow("AAPL")
    q = get_public_quotes("AAPL")
    print(f"AAPL Options Flow (Public.com API):")
    print(f"  P/C Ratio: {flow['put_call_ratio']} ({flow['put_call_label']})")
    print(f"  Gamma Wall: ${flow['gamma_wall']} (IV: {flow.get('atm_iv', 0)})")
    print(f"  Call OI: {flow['total_call_oi']:,} | Put OI: {flow['total_put_oi']:,}")
    print(f"  Net Sentiment: {flow['net_sentiment']}")
    print(f"  Quote Details: Bid/Ask Ratio={q['bid_ask_ratio']}, Spread={q['spread_width_pct']}%")
