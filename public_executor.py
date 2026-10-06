import os
import uuid
from decimal import Decimal
from dotenv import load_dotenv
from public_api_sdk import PublicApiClient, ApiKeyAuthConfig
from public_api_sdk.models import (
    OrderRequest, OrderInstrument, OrderSide, OrderType,
    OrderExpirationRequest, TimeInForce
)

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

def execute_exit_sell(ticker, quantity=None):
    """
    Sells open position when Stop Loss or Take Profit is triggered.
    """
    client = get_client()
    acc_id = get_account_id()
    order_uuid = str(uuid.uuid4())
    
    # If quantity not provided, fetch current held quantity from portfolio
    if quantity is None:
        port = client.get_portfolio(acc_id)
        for p in (port.positions or []):
            if hasattr(p, 'instrument') and p.instrument.symbol.upper() == ticker.upper():
                quantity = Decimal(str(p.quantity))
                break
                
    if not quantity or quantity <= 0:
        return {'status': 'NO_POSITION_FOUND', 'ticker': ticker}
        
    req = OrderRequest(
        order_id=order_uuid,
        instrument=OrderInstrument(symbol=ticker.upper(), type='EQUITY'),
        order_side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        expiration=OrderExpirationRequest(time_in_force=TimeInForce.DAY),
        quantity=quantity
    )
    
    res = client.place_order(req, account_id=acc_id)
    return {
        'order_id': order_uuid,
        'ticker': ticker.upper(),
        'quantity': float(quantity),
        'status': getattr(res, 'status', 'SUBMITTED'),
        'response': res
    }

if __name__ == '__main__':
    print("Testing Public.com Execution Module...")
    summary = get_account_capital_summary()
    alloc_1pct = calculate_test_allocation(0.01)
    print(f"Total Equity: ${summary['total_equity']:,.2f}")
    print(f"Available Buying Power: ${summary['buying_power']:,.2f}")
    print(f"Calculated 1% Test Order Sizing: ${alloc_1pct:.2f}")
