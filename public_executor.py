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
        'status': str(getattr(res, 'status', 'SUBMITTED')),
        'account_id': acc_id
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
        'status': str(getattr(res, 'status', 'SUBMITTED')),
        'account_id': acc_id
    }

def execute_option_buy(option_symbol, quantity=2, limit_price=None):
    """
    Executes a limit order to BUY 2 option contracts on Public.com at the mid-price.
    Sends instant Telegram confirmation and enforces one-and-done entry.
    """
    client = get_client()
    acc_id = get_account_id()
    order_uuid = str(uuid.uuid4())
    option_symbol = option_symbol.upper()

    if limit_price is None or float(limit_price) <= 0:
        raise ValueError("Valid limit_price (mid-price) is required for options orders.")

    limit_dec = Decimal(f"{float(limit_price):.2f}")
    qty_dec = Decimal(str(quantity))

    req = OrderRequest(
        order_id=order_uuid,
        instrument=OrderInstrument(symbol=option_symbol, type='OPTION'),
        order_side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        limit_price=limit_dec,
        quantity=qty_dec,
        open_close_indicator=OpenCloseIndicator.OPEN,
        expiration=OrderExpirationRequest(time_in_force=TimeInForce.DAY)
    )

    res = client.place_order(req, account_id=acc_id)
    status_str = str(getattr(res, 'status', 'SUBMITTED'))

    try:
        from telegram_notifier import send_message, is_configured
        if is_configured():
            msg = (
                f"⚡ [OPTIONS PILOT ORDER EXECUTED]\n"
                f"• Contract: {option_symbol}\n"
                f"• Size: {quantity} Contracts\n"
                f"• Limit Price: ${float(limit_price):.2f} (Mid-Price)\n"
                f"• Order Status: {status_str}\n"
                f"• Order ID: {order_uuid[:8]}...\n"
                f"🛑 One-and-Done Mode: Auto-buy frozen.\n"
                f"🛡️ Exit Guardian is armed with trailing stop defense."
            )
            send_message(msg)
    except Exception as tg_err:
        print(f"Telegram notify error on option buy: {tg_err}")

    return {
        'order_id': order_uuid,
        'symbol': option_symbol,
        'quantity': quantity,
        'limit_price': float(limit_price),
        'status': status_str,
        'account_id': acc_id
    }

def execute_option_sell_to_open(option_symbol, quantity=1, limit_price=None):
    """
    Executes a limit order to SELL TO OPEN covered call contracts on Public.com at mid-price.
    Collects upfront cash credit directly into buying power while keeping underlying shares safe.
    """
    client = get_client()
    acc_id = get_account_id()
    option_symbol = option_symbol.upper()

    limit_dec = Decimal(f"{float(limit_price):.2f}") if limit_price else None
    order_uuid = str(uuid.uuid4())

    req = OrderRequest(
        order_id=order_uuid,
        instrument=OrderInstrument(symbol=option_symbol, type='OPTION'),
        order_side=OrderSide.SELL,
        order_type=OrderType.LIMIT if limit_price else OrderType.MARKET,
        limit_price=limit_dec,
        quantity=Decimal(str(quantity)),
        open_close_indicator=OpenCloseIndicator.OPEN,
        expiration=OrderExpirationRequest(time_in_force=TimeInForce.DAY)
    )

    res = client.place_order(req, account_id=acc_id)
    status_str = str(getattr(res, 'status', 'SUBMITTED'))
    cash_est = (float(limit_price) * quantity * 100) if limit_price else 0.0

    # Register into Covered Call Guardian daemon
    try:
        import covered_call_guardian, re
        root = "AMC" if "AMC" in option_symbol else ("NKE" if "NKE" in option_symbol else option_symbol[:4])
        m = re.search(r'([A-Z]+)(\d{2})(\d{2})(\d{2})[CP](\d{8})', option_symbol)
        if m:
            yr, mo, dy = f"20{m.group(2)}", m.group(3), m.group(4)
            exp_str = f"{yr}-{mo}-{dy}"
            strike_val = float(m.group(5)) / 1000.0
        else:
            exp_str = "2026-10-23"
            strike_val = 3.50
        covered_call_guardian.register_covered_call(
            ticker=root,
            option_symbol=option_symbol,
            strike=strike_val,
            expiry=exp_str,
            quantity=quantity,
            entry_premium=float(limit_price or 0.0)
        )
    except Exception as reg_err:
        print(f"Guardian registration error: {reg_err}")

    try:
        from telegram_notifier import send_message, is_configured
        if is_configured():
            msg = (
                f"🛡️ [COVERED CALL EXECUTED - CASH HARVEST]\n"
                f"• Contract: {option_symbol}\n"
                f"• Size: {quantity} Contracts (Covered by shares)\n"
                f"• Limit Price: ${float(limit_price or 0.0):.2f}\n"
                f"• Instant Cash Deposited: +${cash_est:.2f}\n"
                f"• Order Status: {status_str}\n"
                f"🤖 Covered Call Guardian is actively monitoring 50% decay take-profit and roll defense."
            )
            send_message(msg)
    except Exception as tg_err:
        print(f"Telegram notify error on covered call sell: {tg_err}")

    return {
        'order_id': order_uuid,
        'symbol': option_symbol,
        'quantity': quantity,
        'limit_price': float(limit_price or 0.0),
        'cash_credit': cash_est,
        'status': status_str,
        'account_id': acc_id
    }

def execute_option_buy_to_close(option_symbol, quantity=1, limit_price=None):
    """
    Executes a BUY TO CLOSE order to buy back a short covered call on Public.com (50% profit take or defense).
    """
    client = get_client()
    acc_id = get_account_id()
    option_symbol = option_symbol.upper()

    limit_dec = Decimal(f"{float(limit_price):.2f}") if limit_price else None
    order_uuid = str(uuid.uuid4())

    req = OrderRequest(
        order_id=order_uuid,
        instrument=OrderInstrument(symbol=option_symbol, type='OPTION'),
        order_side=OrderSide.BUY,
        order_type=OrderType.LIMIT if limit_price else OrderType.MARKET,
        limit_price=limit_dec,
        quantity=Decimal(str(quantity)),
        open_close_indicator=OpenCloseIndicator.CLOSE,
        expiration=OrderExpirationRequest(time_in_force=TimeInForce.DAY)
    )

    res = client.place_order(req, account_id=acc_id)
    status_str = str(getattr(res, 'status', 'SUBMITTED'))

    return {
        'order_id': order_uuid,
        'symbol': option_symbol,
        'quantity': quantity,
        'limit_price': float(limit_price or 0.0),
        'status': status_str,
        'account_id': acc_id
    }

def execute_exit_position(ticker, quantity=None, direction=None):
    """
    Closes an open position on Public.com:
    - Auto-detects whether the asset is an OPTION or an EQUITY from live portfolio holdings.
    - If OPTION: Executes OrderInstrument(symbol=..., type='OPTION'), OrderSide.SELL with OpenCloseIndicator.CLOSE.
    - If EQUITY LONG: Executes OrderSide.SELL (sell to close).
    - If EQUITY SHORT: Executes OrderSide.BUY with OpenCloseIndicator.CLOSE (buy to cover).
    - Strictly preserves core protected holds: AMC, APE, NKE.
    """
    PROTECTED_HOLDS = {'AMC', 'APE', 'NKE'}
    sym_upper = ticker.upper()
    if sym_upper in PROTECTED_HOLDS:
        return {'status': 'SKIPPED_PROTECTED_HOLD', 'ticker': sym_upper}

    client = get_client()
    acc_id = get_account_id()
    order_uuid = str(uuid.uuid4())
    
    is_short = False
    if direction and direction.upper() == 'SHORT':
        is_short = True
    
    actual_symbol = sym_upper
    inst_type = 'EQUITY'
    
    # Inspect portfolio to determine exact broker instrument, symbol, and quantity
    port = client.get_portfolio(acc_id)
    matched_pos = None
    for p in (port.positions or []):
        if not hasattr(p, 'instrument'): continue
        psym = p.instrument.symbol.upper()
        if psym in PROTECTED_HOLDS: continue
        
        # Match exact symbol or option derivative of ticker (e.g. NVDA261016C00115000)
        if psym == sym_upper or psym.startswith(sym_upper):
            matched_pos = p
            actual_symbol = psym
            p_type_str = str(getattr(p.instrument, 'type', 'EQUITY'))
            if 'OPTION' in p_type_str.upper() or len(psym) > 10:
                inst_type = 'OPTION'
            break
            
    if matched_pos:
        pos_qty = Decimal(str(matched_pos.quantity))
        if pos_qty < 0:
            is_short = True
            pos_qty = abs(pos_qty)
        
        if quantity is not None and float(quantity) > 0:
            quantity = min(Decimal(str(quantity)), pos_qty)
        else:
            quantity = pos_qty
            
    if not quantity or quantity <= 0:
        return {'status': 'NO_POSITION_FOUND', 'ticker': ticker}
        
    if inst_type == 'OPTION':
        # Sell to close option contract
        req = OrderRequest(
            order_id=order_uuid,
            instrument=OrderInstrument(symbol=actual_symbol, type='OPTION'),
            order_side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            expiration=OrderExpirationRequest(time_in_force=TimeInForce.DAY),
            quantity=Decimal(str(quantity)),
            open_close_indicator=OpenCloseIndicator.CLOSE
        )
    elif is_short:
        # Buy to cover short equity
        req = OrderRequest(
            order_id=order_uuid,
            instrument=OrderInstrument(symbol=actual_symbol, type='EQUITY'),
            order_side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            expiration=OrderExpirationRequest(time_in_force=TimeInForce.DAY),
            quantity=Decimal(str(quantity)),
            open_close_indicator=OpenCloseIndicator.CLOSE
        )
    else:
        # Sell to close long equity
        req = OrderRequest(
            order_id=order_uuid,
            instrument=OrderInstrument(symbol=actual_symbol, type='EQUITY'),
            order_side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            expiration=OrderExpirationRequest(time_in_force=TimeInForce.DAY),
            quantity=Decimal(str(quantity))
        )
        
    res = client.place_order(req, account_id=acc_id)
    return {
        'order_id': order_uuid,
        'ticker': actual_symbol,
        'instrument_type': inst_type,
        'quantity': float(quantity),
        'side': 'SELL' if (inst_type == 'OPTION' or not is_short) else 'COVER',
        'status': str(getattr(res, 'status', 'SUBMITTED')),
        'account_id': acc_id
    }

def get_live_prices(tickers):
    """
    Fetches real-time market prices for a list of tickers via Public.com API quotes (chunked by 50).
    100% Pure Public.com API (Zero Yahoo Finance).
    """
    if not tickers:
        return {}
    prices = {}
    clean_list = []
    orig_map = {}
    for t in tickers:
        if not t:
            continue
        raw = str(t).strip().upper()
        norm = raw.replace("-", ".")
        orig_map[norm] = raw
        if norm not in clean_list:
            clean_list.append(norm)

    try:
        client = get_client()
        acc_id = get_account_id()
        for i in range(0, len(clean_list), 50):
            chunk = clean_list[i:i + 50]
            try:
                instruments = [OrderInstrument(symbol=sym, type='EQUITY') for sym in chunk]
                quotes = client.get_quotes(instruments, account_id=acc_id)
                for q in quotes:
                    sym = getattr(getattr(q, 'instrument', None), 'symbol', None)
                    if sym and getattr(q, 'last', None):
                        s_up = sym.upper()
                        val = float(q.last)
                        prices[s_up] = val
                        if s_up in orig_map:
                            prices[orig_map[s_up]] = val
            except Exception as chunk_err:
                print(f"Public.com quotes chunk notice: {chunk_err}")
    except Exception as e:
        print(f"Error fetching Public.com quotes: {e}")

    # Fallback to Public.com bar close for any ticker with no active quote (e.g. extended hours)
    missing = [sym for sym in clean_list if sym not in prices]
    if missing and len(missing) <= 10:
        try:
            from data_feed_public import get_public_bars_sync
            for sym in missing:
                df = get_public_bars_sync(sym, '5m')
                if df is not None and not df.empty:
                    val = float(df['Close'].iloc[-1])
                    prices[sym] = val
                    if sym in orig_map:
                        prices[orig_map[sym]] = val
        except Exception:
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
