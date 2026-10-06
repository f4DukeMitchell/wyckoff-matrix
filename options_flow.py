import streamlit as st
import os
from dotenv import load_dotenv
from public_api_sdk import PublicApiClient, ApiKeyAuthConfig
from public_api_sdk.models import OptionChainRequest, OptionExpirationsRequest, OrderInstrument

load_dotenv()
API_KEY = os.getenv('PUBLIC_API_KEY')

@st.cache_data(ttl=300)
def get_options_flow(ticker):
    """
    Analyze the options chain using Public.com API.
    """
    result = {
        'ticker': ticker,
        'put_call_ratio': 0.0,
        'put_call_label': 'N/A',
        'max_pain': 0.0,
        'unusual_calls': [],
        'unusual_puts': [],
        'total_call_oi': 0,
        'total_put_oi': 0,
        'nearest_expiry': 'N/A',
        'gamma_wall': 0.0,
        'atm_iv': 0.0,
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
            # Often the current 0DTE has weird OI, check the next one
            nearest = exp_res.expirations[1]
            
        result['nearest_expiry'] = nearest
        
        chain_req = OptionChainRequest(instrument=inst, expiration_date=nearest)
        chain_res = client.get_option_chain(chain_req, account_id=account_id)
        
        if not chain_res.calls and not chain_res.puts:
            return result
            
        calls = chain_res.calls
        puts = chain_res.puts
                
        total_call_oi = sum(c.open_interest for c in calls if c.open_interest)
        total_put_oi = sum(p.open_interest for p in puts if p.open_interest)
        
        result['total_call_oi'] = total_call_oi
        result['total_put_oi'] = total_put_oi
        
        if total_call_oi > 0:
            pcr = total_put_oi / total_call_oi
            result['put_call_ratio'] = round(pcr, 2)
            if pcr > 1.2: result['put_call_label'] = 'BEARISH SKEW'
            elif pcr < 0.7: result['put_call_label'] = 'BULLISH SKEW'
            else: result['put_call_label'] = 'NEUTRAL'
            
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
            # Max Pain is the strike price where the total payout to option buyers
            # (loss to option sellers / market makers) is minimized upon expiration.
            # Loss = sum(max(0, S - strike) * Call_OI) + sum(max(0, strike - S) * Put_OI)
            sorted_strikes = sorted(all_strikes.keys())
            min_loss = float('inf')
            max_pain_strike = None

            for eval_strike in sorted_strikes:
                total_loss = 0.0
                # Call loss if price settles at eval_strike
                for c_strike, c_oi in strike_call_oi.items():
                    if eval_strike > c_strike:
                        total_loss += (eval_strike - c_strike) * c_oi * 100.0

                # Put loss if price settles at eval_strike
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
            
        # Sentiment
        if result['put_call_label'] != 'N/A':
            result['net_sentiment'] = result['put_call_label'].replace(' SKEW', '')
            
    except Exception as e:
        print(f"Error fetching options from Public: {e}")
        pass
        
    return result

def get_public_quotes(ticker):
    from public_api_sdk.models import QuoteRequest
    res = {'bid_ask_ratio': 0.0, 'spread_width_pct': 0.0}
    if not API_KEY: return res
    try:
        client = PublicApiClient(auth_config=ApiKeyAuthConfig(api_secret_key=API_KEY))
        accounts = client.get_accounts()
        if not accounts.accounts: return res
        account_id = accounts.accounts[0].account_id
        
        q_res = client.get_quotes(QuoteRequest(instruments=[OrderInstrument(symbol=ticker, type="EQUITY")]), account_id=account_id)
        if q_res.quotes:
            q = q_res.quotes[0]
            bid, ask = float(q.bid or 0), float(q.ask or 0)
            bid_size, ask_size = float(q.bid_size or 0), float(q.ask_size or 0)
            
            if ask_size > 0: res['bid_ask_ratio'] = round(bid_size / ask_size, 2)
            if bid > 0 and ask > 0: res['spread_width_pct'] = round(((ask - bid) / bid) * 100, 3)
    except Exception as e:
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
