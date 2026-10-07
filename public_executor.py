import os
import uuid
from decimal import Decimal
from dotenv import load_dotenv
from public_api_sdk import PublicApiClient, ApiKeyAuthConfig
from public_api_sdk.models import (
    OrderRequest, OrderInstrument, OrderSide, OrderType,
    OrderExpirationRequest, TimeInForce
)
from public_api_sdk.models.order import OpenCloseIndicator

load_dotenv()
API_KEY = os.getenv('PUBLIC_API_KEY')

_CLIENT = None

def get_client():
    global _CLIENT
    if _CLIENT is None:
        if not API_KEY:
            raise ValueError("PUBLIC_API_KEY not configured in environment.")
        _CLIENT = PublicApiClient(auth_config=ApiKeyAuthConfig(api_secret_key=API_KEY))
    return _CLIENT

def get_account_id():
    client = get_client()
    accounts = client.get_accounts()
    if not accounts.accounts:
        raise ValueError("No Public.com account found.")
    return accounts.accounts[0].account_id

def get_account_capital_summary():
    """
    Returns buying power and equity for 1% risk allocation calculations.
    """
    client = get_client()
    acc_id = get_account_id()
    port = client.get_portfolio(account_id=acc_id)
    
    # Portfolio equity total
    total_equity = sum(float(x.value) for x in port.equity if x.value) if port.equity else 0.0
    
    # Buying power
    bp = 0.0
    if hasattr(port.buying_power, 'buying_power'):
        bp = float(port.buying_power.buying_power)
    elif port.buying_power:
        bp = float(port.buying_power)
        
    return {
        'total_equity': total_equity,
        'buying_power': bp,
        'account_id': acc_id
    }

def get_broker_portfolio_positions():
    """
    Retrieves all open holdings directly from Public.com broker account
    (including non-algo positions like manual holdings or long-term investments).
    """
    try:
        client = get_client()
        acc_id = get_account_id()
        port = client.get_portfolio(account_id=acc_id)
        positions = []
        for p in (port.positions or []):
            sym = p.instrument.symbol if hasattr(p, 'instrument') else None
            if not sym: continue
            qty = float(p.quantity or 0.0)
            if qty == 0: continue

            # Direction & entry
            direction = "SHORT" if qty < 0 else "LONG"
            unit_cost = float(p.cost_basis.unit_cost) if (p.cost_basis and p.cost_basis.unit_cost) else 0.0
            last_pr = float(p.last_price.last_price) if (p.last_price and p.last_price.last_price) else 0.0
            curr_val = float(p.current_value) if p.current_value else (abs(qty) * last_pr)
            
            # PnL percentage
            pnl_pct = 0.0
            if unit_cost > 0 and last_pr > 0:
                pnl_pct = ((last_pr - unit_cost) / unit_cost * 100.0) if direction == "LONG" else ((unit_cost - last_pr) / unit_cost * 100.0)
            elif p.cost_basis and p.cost_basis.gain_percentage:
                pnl_pct = float(p.cost_basis.gain_percentage)

            positions.append({
                'ticker': sym.upper(),
                'direction': direction,
                'quantity': abs(qty),
                'entry_price': unit_cost,
                'current_price': last_pr,
                'market_value': curr_val,
                'unrealized_pnl_pct': round(pnl_pct, 2),
                'is_broker_native': True
            })
        return positions
    except Exception as e:
        print(f"Error fetching broker portfolio positions: {e}")
        return []

def calculate_test_allocation(pct=0.01, min_amount=5.0, max_amount=150.0):
    """
    Calculates 1% test sizing based on available Buying Power (e.g. 1% of $6,770.20 = $67.70).
    Bounded safely between min_amount and max_amount.
    """
    summary = get_account_capital_summary()
    bp = summary['buying_power']
    
    # 1% of available buying power
    alloc = round(bp * pct, 2)
    
    # Bound safely for testing
    alloc = max(min_amount, min(alloc, max_amount))
    
    # Ensure it never exceeds available buying power
    alloc = min(alloc, bp)
    return round(alloc, 2)

def execute_dollar_buy(ticker, dollar_amount):
    """
    Executes a fractional dollar market BUY order on Public.com.
    """
    client = get_client()
    acc_id = get_account_id()
    order_uuid = str(uuid.uuid4())
    
    req = OrderRequest(
        order_id=order_uuid,
        instrument=OrderInstrument(symbol=ticker.upper(), type='EQUITY'),
        order_side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        expiration=OrderExpirationRequest(time_in_force=TimeInForce.DAY),
        amount=Decimal(f"{dollar_amount:.2f}")
    )
    
    # Execute live order
    res = client.place_order(req, account_id=acc_id)
    return {
        'order_id': order_uuid,
        'ticker': ticker.upper(),
        'amount': dollar_amount,
        'status': getattr(res, 'status', 'SUBMITTED'),
        'response': res
    }

def execute_short_sell(ticker, dollar_amount=None):
    """
    Executes an integer whole-share SHORT order on Public.com.
    Under SEC/broker regulations, fractional short selling is forbidden.
    Calculates whole shares to match dollar_amount (~1% slot, e.g. $67), minimum 1 share.
    """
    client = get_client()
    acc_id = get_account_id()
    
    if dollar_amount is None:
        dollar_amount = calculate_test_allocation(0.01)
        
    # Preflight to verify borrow availability and get current market price
    preflight = client.preflight_short_order(symbol=ticker.upper(), quantity=Decimal('1'), account_id=acc_id)
    share_price = float(preflight.order_value) if preflight.order_value else 1.0
    
    # Calculate whole shares (fractional shorting prohibited)
    if share_price <= dollar_amount:
        shares = max(1, int(dollar_amount / share_price))
    else:
        # Stock price is higher than 1% dollar amount; short the minimum allowed by broker (1 share)
        shares = 1
        
    order_uuid = str(uuid.uuid4())
    res = client.place_short_order(
        symbol=ticker.upper(),
        quantity=Decimal(str(shares)),
        order_id=order_uuid,
        account_id=acc_id
    )
    
    return {
        'order_id': order_uuid,
        'ticker': ticker.upper(),
        'shares': shares,
        'share_price': share_price,
        'notional_value': round(shares * share_price, 2),
        'side': 'SHORT',
        'status': getattr(res, 'status', 'SUBMITTED'),
        'response': res
    }

def execute_exit_position(ticker, quantity=None, direction=None):
    """
    Closes an open position on Public.com:
    - If LONG: Executes OrderSide.SELL (sell to close).
    - If SHORT: Executes OrderSide.BUY with OpenCloseIndicator.CLOSE (buy to cover).
    """
    client = get_client()
    acc_id = get_account_id()
    order_uuid = str(uuid.uuid4())
    
    is_short = False
    if direction and direction.upper() == 'SHORT':
        is_short = True
    
    # If quantity not provided or direction not provided, inspect portfolio
    if quantity is None or direction is None:
        port = client.get_portfolio(acc_id)
        for p in (port.positions or []):
            if hasattr(p, 'instrument') and p.instrument.symbol.upper() == ticker.upper():
                q = Decimal(str(p.quantity))
                if q < 0:
                    is_short = True
                    quantity = abs(q)
                else:
                    quantity = q
                break
                
    if not quantity or quantity <= 0:
        return {'status': 'NO_POSITION_FOUND', 'ticker': ticker}
        
    if is_short:
        # Buy to cover short
        req = OrderRequest(
            order_id=order_uuid,
            instrument=OrderInstrument(symbol=ticker.upper(), type='EQUITY'),
            order_side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            expiration=OrderExpirationRequest(time_in_force=TimeInForce.DAY),
            quantity=Decimal(str(quantity)),
            open_close_indicator=OpenCloseIndicator.CLOSE
        )
    else:
        # Sell to close long
        req = OrderRequest(
            order_id=order_uuid,
            instrument=OrderInstrument(symbol=ticker.upper(), type='EQUITY'),
            order_side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            expiration=OrderExpirationRequest(time_in_force=TimeInForce.DAY),
            quantity=Decimal(str(quantity))
        )
        
    res = client.place_order(req, account_id=acc_id)
    return {
        'order_id': order_uuid,
        'ticker': ticker.upper(),
        'quantity': float(quantity),
        'side': 'COVER' if is_short else 'SELL',
        'status': getattr(res, 'status', 'SUBMITTED'),
        'response': res
    }

def get_live_prices(tickers):
    """
    Fetches real-time market prices for a list of tickers via Public.com API quotes.
    Falls back to yfinance if not available.
    """
    if not tickers:
        return {}
    prices = {}
    try:
        client = get_client()
        acc_id = get_account_id()
        instruments = [OrderInstrument(symbol=t.upper(), type='EQUITY') for t in tickers]
        quotes = client.get_quotes(instruments, account_id=acc_id)
        for q in quotes:
            sym = getattr(getattr(q, 'instrument', None), 'symbol', None)
            if sym and getattr(q, 'last', None):
                prices[sym.upper()] = float(q.last)
    except Exception as e:
        print(f"Error fetching Public.com quotes: {e}")
        
    missing = [t.upper() for t in tickers if t.upper() not in prices]
    if missing:
        try:
            import yfinance as yf
            for t in missing:
                t_obj = yf.Ticker(t)
                prices[t] = float(t_obj.fast_info.last_price)
        except:
            pass
    return prices

# Backward compatibility alias
execute_exit_sell = execute_exit_position

if __name__ == '__main__':
    print("Testing Public.com Execution Module...")
    summary = get_account_capital_summary()
    alloc_1pct = calculate_test_allocation(0.01)
    print(f"Total Equity: ${summary['total_equity']:,.2f}")
    print(f"Available Buying Power: ${summary['buying_power']:,.2f}")
    print(f"Calculated 1% Test Order Sizing: ${alloc_1pct:.2f}")
