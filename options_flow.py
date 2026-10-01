"""
Options Flow Analysis Module
Fetches options chain data from yfinance to detect unusual activity near Wyckoff zones.
"""
import yfinance as yf
import numpy as np
import datetime


def get_options_flow(ticker):
    """
    Analyze the options chain for a ticker to detect unusual institutional activity.
    
    Returns a dict with:
    - put_call_ratio: Total put OI / total call OI
    - put_call_label: "BEARISH SKEW", "BULLISH SKEW", "NEUTRAL"
    - max_pain: The strike price where most options expire worthless
    - unusual_calls: List of strikes with call volume > 3x avg
    - unusual_puts: List of strikes with put volume > 3x avg
    - total_call_oi: Total call open interest
    - total_put_oi: Total put open interest
    - nearest_expiry: The expiration date analyzed
    - gamma_wall: Strike with highest total OI (acts as price magnet)
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
        'net_sentiment': 'N/A',
    }
    
    try:
        stock = yf.Ticker(ticker)
        expirations = stock.options
        
        if not expirations:
            return result
        
        # Use the nearest expiration (most liquid, most institutional activity)
        nearest_exp = expirations[0]
        result['nearest_expiry'] = nearest_exp
        
        chain = stock.option_chain(nearest_exp)
        calls = chain.calls
        puts = chain.puts
        
        if calls.empty or puts.empty:
            return result
        
        # Total OI
        total_call_oi = int(calls['openInterest'].sum()) if 'openInterest' in calls.columns else 0
        total_put_oi = int(puts['openInterest'].sum()) if 'openInterest' in puts.columns else 0
        result['total_call_oi'] = total_call_oi
        result['total_put_oi'] = total_put_oi
        
        # Put/Call Ratio
        if total_call_oi > 0:
            pcr = total_put_oi / total_call_oi
            result['put_call_ratio'] = round(pcr, 2)
            if pcr > 1.2:
                result['put_call_label'] = 'BEARISH SKEW'
            elif pcr < 0.7:
                result['put_call_label'] = 'BULLISH SKEW'
            else:
                result['put_call_label'] = 'NEUTRAL'
        
        # Unusual Volume Detection (volume > 3x the average volume for that chain)
        if 'volume' in calls.columns:
            avg_call_vol = calls['volume'].mean()
            if avg_call_vol > 0:
                unusual_c = calls[calls['volume'] > 3 * avg_call_vol]
                result['unusual_calls'] = [
                    {'strike': float(row['strike']), 'volume': int(row['volume']), 'oi': int(row.get('openInterest', 0))}
                    for _, row in unusual_c.iterrows()
                ]
        
        if 'volume' in puts.columns:
            avg_put_vol = puts['volume'].mean()
            if avg_put_vol > 0:
                unusual_p = puts[puts['volume'] > 3 * avg_put_vol]
                result['unusual_puts'] = [
                    {'strike': float(row['strike']), 'volume': int(row['volume']), 'oi': int(row.get('openInterest', 0))}
                    for _, row in unusual_p.iterrows()
                ]
        
        # Max Pain Calculation (strike where total dollar value of expiring options is minimized)
        current_price = stock.info.get('regularMarketPrice', 0) or stock.info.get('currentPrice', 0)
        
        if 'openInterest' in calls.columns and 'openInterest' in puts.columns:
            strikes = sorted(set(calls['strike'].tolist() + puts['strike'].tolist()))
            min_pain = float('inf')
            max_pain_strike = 0
            
            for strike in strikes:
                call_pain = 0
                put_pain = 0
                for _, row in calls.iterrows():
                    if strike > row['strike']:
                        call_pain += (strike - row['strike']) * row.get('openInterest', 0)
                for _, row in puts.iterrows():
                    if strike < row['strike']:
                        put_pain += (row['strike'] - strike) * row.get('openInterest', 0)
                total_pain = call_pain + put_pain
                if total_pain < min_pain:
                    min_pain = total_pain
                    max_pain_strike = strike
            
            result['max_pain'] = float(max_pain_strike)
        
        # Gamma Wall (strike with highest combined OI = price magnet)
        all_strikes = {}
        if 'openInterest' in calls.columns:
            for _, row in calls.iterrows():
                s = float(row['strike'])
                all_strikes[s] = all_strikes.get(s, 0) + row.get('openInterest', 0)
        if 'openInterest' in puts.columns:
            for _, row in puts.iterrows():
                s = float(row['strike'])
                all_strikes[s] = all_strikes.get(s, 0) + row.get('openInterest', 0)
        
        if all_strikes:
            gamma_wall = max(all_strikes, key=all_strikes.get)
            result['gamma_wall'] = float(gamma_wall)
        
        # Net sentiment combining P/C ratio with unusual activity
        unusual_call_count = len(result['unusual_calls'])
        unusual_put_count = len(result['unusual_puts'])
        
        if result['put_call_label'] == 'BULLISH SKEW' and unusual_call_count > unusual_put_count:
            result['net_sentiment'] = 'STRONG BULLISH'
        elif result['put_call_label'] == 'BEARISH SKEW' and unusual_put_count > unusual_call_count:
            result['net_sentiment'] = 'STRONG BEARISH'
        elif unusual_call_count > unusual_put_count + 2:
            result['net_sentiment'] = 'BULLISH'
        elif unusual_put_count > unusual_call_count + 2:
            result['net_sentiment'] = 'BEARISH'
        else:
            result['net_sentiment'] = 'NEUTRAL'
        
    except Exception as e:
        pass
    
    return result


if __name__ == '__main__':
    flow = get_options_flow("AAPL")
    print(f"AAPL Options Flow:")
    print(f"  P/C Ratio: {flow['put_call_ratio']} ({flow['put_call_label']})")
    print(f"  Max Pain: ${flow['max_pain']}")
    print(f"  Gamma Wall: ${flow['gamma_wall']}")
    print(f"  Call OI: {flow['total_call_oi']:,} | Put OI: {flow['total_put_oi']:,}")
    print(f"  Unusual Calls: {len(flow['unusual_calls'])} | Unusual Puts: {len(flow['unusual_puts'])}")
    print(f"  Net Sentiment: {flow['net_sentiment']}")
