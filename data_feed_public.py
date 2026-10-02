import os
from dotenv import load_dotenv
from public_api_sdk import PublicApiClient, ApiKeyAuthConfig
from public_api_sdk.models import QuoteRequest, BarPeriod, BarAggregation
import pandas as pd
import datetime

load_dotenv()

_CLIENT = None

def get_client():
    global _CLIENT
    if _CLIENT is None:
        api_key = os.getenv('PUBLIC_API_KEY')
        if not api_key:
            raise ValueError("PUBLIC_API_KEY not found in environment variables.")
        _CLIENT = PublicApiClient(auth_config=ApiKeyAuthConfig(api_secret_key=api_key))
    return _CLIENT

def get_public_bars(ticker, interval='5m', lookback_days=10):
    """
    Fetch historical bars from Public.com API and format them identically to yfinance.
    interval: '5m', '15m', '1h', '1d'
    """
    client = get_client()
    
    # Map intervals to BarAggregation
    agg_map = {
        '5m': BarAggregation.FIVE_MINUTES,
        '15m': BarAggregation.FIFTEEN_MINUTES,
        '1h': BarAggregation.ONE_HOUR,
        '1d': BarAggregation.ONE_DAY
    }
    
    if interval not in agg_map:
        raise ValueError(f"Unsupported interval for Public API: {interval}")
        
    aggregation = agg_map[interval]
    
    # Determine the period to fetch based on lookback needs
    # For intraday (5m, 15m, 1h), a week or month is usually enough
    # For daily (1d), we might need half a year or more.
    if interval == '1d':
        period = BarPeriod.HALF_YEAR
    else:
        period = BarPeriod.WEEK
    
    try:
        response = client.get_bars(ticker, period=period, aggregation=aggregation)
        
        data = []
        
        # Public API organizes bars into sessions. We will extract all and sort by time.
        sessions = []
        if getattr(response, 'pre_market_overnight', None) and response.pre_market_overnight.bars:
            sessions.extend(response.pre_market_overnight.bars)
        if getattr(response, 'pre_market', None) and response.pre_market.bars:
            sessions.extend(response.pre_market.bars)
        if getattr(response, 'regular_market', None) and response.regular_market.bars:
            sessions.extend(response.regular_market.bars)
        if getattr(response, 'after_market', None) and response.after_market.bars:
            sessions.extend(response.after_market.bars)
        if getattr(response, 'post_market_overnight', None) and response.post_market_overnight.bars:
            sessions.extend(response.post_market_overnight.bars)
            
        for b in sessions:
            data.append({
                'Date': pd.to_datetime(b.timestamp),
                'Open': b.open,
                'High': b.high,
                'Low': b.low,
                'Close': b.close,
                'Volume': b.volume
            })
            
        df = pd.DataFrame(data)
        if not df.empty:
            df.set_index('Date', inplace=True)
            df.sort_index(inplace=True)
        return df
        
    except Exception as e:
        print(f"Error fetching Public data for {ticker}: {e}")
        return pd.DataFrame()

def get_public_quote(ticker):
    client = get_client()
    try:
        req = QuoteRequest(symbols=[ticker])
        res = client.get_quotes(req)
        if res.quotes:
            return res.quotes[0].price
        return None
    except Exception as e:
        print(f"Error fetching Public quote for {ticker}: {e}")
        return None

if __name__ == "__main__":
    # Quick Test
    print("Testing Public API Connection...")
    try:
        price = get_public_quote("AAPL")
        print(f"Live AAPL Price: {price}")
        
        df = get_public_bars("AAPL", "5m")
        print(f"Fetched {len(df)} 5m bars.")
        if not df.empty:
            print(df.tail())
    except Exception as e:
        print("Test failed. Check API Key:", e)
