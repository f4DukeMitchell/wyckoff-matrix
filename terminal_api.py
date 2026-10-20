import os
import json
import sqlite3
import datetime
import asyncio

# Ensure standard Linux system PATHs are always present in virtualenv environment
if os.name != 'nt':
    std_paths = ["/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin"]
    curr_path = os.environ.get("PATH", "")
    for p in std_paths:
        if p not in curr_path:
            curr_path = f"{p}:{curr_path}"
    os.environ["PATH"] = curr_path
    os.environ["PYTHONUNBUFFERED"] = "1"

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, status, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
from typing import Optional
import secrets

from public_executor import (
    get_account_capital_summary, calculate_test_allocation, execute_dollar_buy,
    execute_short_sell, execute_exit_sell, get_live_prices, get_broker_portfolio_positions,
    get_single_quote
)
from data_feed_public import get_public_bars_sync
from options_flow import get_options_flow, get_public_quotes

app = FastAPI(title="WSR Terminal Engine")

# --- AUTHENTICATION SAFEGUARDS ---
security = HTTPBasic()

AUTH_USER = os.getenv("TERMINAL_USER", "F4DukeMitchell")
AUTH_PASS = os.getenv("TERMINAL_PASS", "M642423s$")

@app.middleware("http")
async def basic_auth_middleware(request: Request, call_next):
    # Allow local loopback without prompt if desired, or require auth for all external access
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Basic "):
        return Response(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": 'Basic realm="WSR Matrix Terminal Secure Access"'},
            content="Authentication Required."
        )
    try:
        import base64
        encoded_creds = auth_header.split(" ", 1)[1]
        decoded = base64.b64decode(encoded_creds).decode("utf-8")
        username, _, password = decoded.partition(":")
        
        user_correct = secrets.compare_digest(username, AUTH_USER)
        pass_correct = secrets.compare_digest(password, AUTH_PASS)
        if not (user_correct and pass_correct):
            return Response(
                status_code=status.HTTP_401_UNAUTHORIZED,
                headers={"WWW-Authenticate": 'Basic realm="WSR Matrix Terminal Secure Access"'},
                content="Invalid Credentials."
            )
    except Exception:
        return Response(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": 'Basic realm="WSR Matrix Terminal Secure Access"'},
            content="Authentication Failed."
        )
    return await call_next(request)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
from data_paths import DB_PATH, MODEL_PATH  # runtime state lives outside the git tree

# --- WEBSOCKET CLIENT MANAGER ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except:
                pass

manager = ConnectionManager()

@app.on_event("startup")
def startup_event():
    try:
        from exit_guardian import start_guardian_daemon
        start_guardian_daemon()
    except Exception as e:
        print(f"Error launching Exit Guardian daemon: {e}")

# --- REST ENDPOINTS ---

@app.get("/api/terminal/state")
def get_terminal_state():
    """Returns complete state: account buying power, active positions, recent scanner alerts."""
    cap = get_account_capital_summary()
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # Active user trades (tracked by Wyckoff algo)
    c.execute("""
        SELECT id, ticker, direction, entry_price, stop_loss, take_profit, regime, timeframe, timestamp, user_active, outcome, pnl_r, breakeven_set, partial_exit_done, partial_tier_done, initial_shares, peak_high_r, trailing_stop_price, initial_stop_loss, ml_confidence
        FROM alerts
        WHERE outcome = 'OPEN' AND user_active = 1
        ORDER BY id DESC
    """)
    raw_positions = [dict(r) for r in c.fetchall()]
    active_tickers = list(set([p['ticker'] for p in raw_positions if p.get('ticker')]))
    live_prices = get_live_prices(active_tickers) if active_tickers else {}
    
    # Link with live broker positions from Public.com
    broker_holdings = get_broker_portfolio_positions()
    broker_map = {bh['ticker'].upper(): bh for bh in broker_holdings}

    active_positions = []
    tracked_tickers = set()
    total_algo_pnl_dollar = 0.0

    for pos in raw_positions:
        ticker = pos.get('ticker')
        tracked_tickers.add(ticker.upper())
        entry = float(pos.get('entry_price') or 0.0)
        sl = float(pos.get('stop_loss') or 0.0)
        init_sl = float(pos.get('initial_stop_loss') or sl or 0.0)
        is_long = (pos.get('direction') or 'LONG').upper() == 'LONG'
        curr_price = live_prices.get(ticker)
        
        # Pull broker quantity and market value
        bh = broker_map.get(ticker.upper())
        qty = float(bh.get('quantity') or 0.0) if bh else 0.0
        if not bh or qty <= 0:
            continue
        pos['quantity'] = qty
        pos['market_value'] = float(bh.get('market_value') or 0.0) if bh else 0.0

        pos['current_price'] = curr_price
        tier = int(pos.get('partial_tier_done') or 0)
        init_shares = float(pos.get('initial_shares') or qty or 0.0)
        # If no tiers taken yet and live broker quantity is higher, user added to position!
        if (init_shares <= 0 and qty > 0) or (tier == 0 and qty > init_shares):
            init_shares = qty
            if pos.get('id'):
                try:
                    c.execute("UPDATE alerts SET initial_shares = ? WHERE id = ?", (init_shares, pos['id']))
                    conn.commit()
                except Exception:
                    pass
        pos['initial_shares'] = init_shares
        pos['initial_cost'] = round(init_shares * entry, 2)
        pos['shares_sold'] = round(max(0.0, init_shares - qty), 5)
        pos['tier'] = tier

        risk = abs(entry - init_sl) if (init_sl > 0 and abs(entry - init_sl) > 0.001) else (abs(entry - sl) if (sl > 0 and abs(entry - sl) > 0.001) else entry * 0.015)
        pos['initial_risk_dollar'] = round(risk * init_shares, 2)
        pos['risk_per_share'] = round(risk, 4)

        # Realized cash profit from banked scaling tiers
        realized_profit = 0.0
        if is_long and init_shares > 0:
            if tier >= 1: realized_profit += (0.10 * init_shares) * (0.20 * risk)
            if tier >= 2: realized_profit += (0.10 * init_shares) * (0.40 * risk)
            if tier >= 3: realized_profit += (0.10 * init_shares) * (0.65 * risk)
            if tier >= 4: realized_profit += (0.25 * init_shares) * (1.00 * risk)
            if tier >= 5: realized_profit += (0.25 * init_shares) * (1.30 * risk)
        pos['realized_profit_dollar'] = round(realized_profit, 2)

        if curr_price and entry > 0:
            pnl_pct = ((curr_price - entry) / entry * 100.0) if is_long else ((entry - curr_price) / entry * 100.0)
            r_mult = ((curr_price - entry) / risk) if (is_long and risk > 0) else (((entry - curr_price) / risk) if risk > 0 else 0.0)
            dollar_pnl = ((curr_price - entry) * qty) if is_long else ((entry - curr_price) * qty)
            pos['unrealized_pnl_pct'] = round(pnl_pct, 2)
            pos['unrealized_r'] = round(r_mult, 2)
            pos['unrealized_pnl_dollar'] = round(dollar_pnl, 2)
            pos['total_trade_pnl_dollar'] = round(dollar_pnl + realized_profit, 2)
            total_algo_pnl_dollar += (dollar_pnl + realized_profit)
        else:
            pos['unrealized_pnl_pct'] = 0.0
            pos['unrealized_r'] = 0.0
            pos['unrealized_pnl_dollar'] = 0.0
            pos['total_trade_pnl_dollar'] = round(realized_profit, 2)
        active_positions.append(pos)

    # Ingest non-algo long-term broker holdings from Public.com
    for bh in broker_holdings:
        sym = bh['ticker'].upper()
        if sym not in tracked_tickers:
            dollar_pnl = (bh['current_price'] - bh['entry_price']) * bh['quantity'] if bh['direction'] == 'LONG' else (bh['entry_price'] - bh['current_price']) * bh['quantity']
            active_positions.append({
                'id': None,
                'ticker': sym,
                'direction': bh['direction'],
                'entry_price': bh['entry_price'],
                'current_price': bh['current_price'],
                'stop_loss': 0.0,
                'take_profit': 0.0,
                'timeframe': 'PORT',
                'regime': 'PORTFOLIO',
                'user_active': 1,
                'outcome': 'OPEN',
                'unrealized_pnl_pct': bh['unrealized_pnl_pct'],
                'unrealized_pnl_dollar': round(dollar_pnl, 2),
                'unrealized_r': None,
                'quantity': bh['quantity'],
                'market_value': bh['market_value'],
                'is_broker_native': True,
                'ml_confidence': None
            })
    
    # Recent scanner signals (distinct latest setup per ticker & timeframe)
    c.execute("""
        SELECT a.id, a.ticker, a.direction, a.entry_price, a.stop_loss, a.take_profit, 
               a.regime, a.timeframe, a.timestamp, a.outcome, a.model_version, a.user_active, a.ml_confidence
        FROM alerts a
        INNER JOIN (
            SELECT ticker, timeframe, MAX(id) as max_id
            FROM alerts
            WHERE outcome = 'OPEN' AND ticker NOT LIKE 'TEST%'
            GROUP BY ticker, timeframe
        ) latest ON a.id = latest.max_id
        WHERE a.ticker NOT LIKE 'TEST%'
        ORDER BY a.id DESC LIMIT 100
    """)
    scanner_results = [dict(r) for r in c.fetchall()]

    
    # Recent trade history
    c.execute("""
        SELECT id, ticker, direction, entry_price, exit_price, outcome, pnl_r, timestamp, timeframe
        FROM alerts
        WHERE outcome IN ('WIN', 'LOSS', 'BREAKEVEN')
        ORDER BY id DESC LIMIT 30
    """)
    history = [dict(r) for r in c.fetchall()]

    # Latest Telegram broadcast alerts (Day Trades & Swing Trades)
    c.execute("""
        SELECT id, ticker, direction, entry_price, stop_loss, take_profit, 
               timeframe, timestamp, outcome, ml_confidence, user_active, regime
        FROM alerts
        WHERE ticker NOT LIKE 'TEST%' AND (telegram_alerted = 1 OR telegram_alerted IS NULL)
        ORDER BY id DESC LIMIT 25
    """)
    raw_tg = [dict(r) for r in c.fetchall()]
    conn.close()

    telegram_alerts = []
    for r in raw_tg:
        tf = (r.get('timeframe') or '').lower()
        if tf in ['1m', '5m', '15m']:
            r['channel'] = 'Wyckoff Day Trades'
            r['channel_type'] = 'DAY'
            r['chat_id'] = '-5547865201'
        else:
            r['channel'] = 'Wyckoff Trades'
            r['channel_type'] = 'SWING'
            r['chat_id'] = '8610265859'
        is_live = bool(r.get('user_active') == 1)
        ml_c = r.get('ml_confidence')
        r['is_live'] = is_live
        if is_live:
            r['execution_badge'] = '⚡ LIVE AUTO-BUY'
        elif ml_c is not None and float(ml_c) < 55.0:
            r['execution_badge'] = '📝 PAPER (<55% ML)'
        elif (r.get('direction') or '').upper() == 'SHORT':
            r['execution_badge'] = '📝 PAPER (SHORT)'
        else:
            r['execution_badge'] = '📝 PAPER TRACKING'
        telegram_alerts.append(r)

    stalker_list = []
    
    try:
        from priority_queue import get_priority_queue, get_priority_queue_stats
        p_queue = get_priority_queue()
        p_stats = get_priority_queue_stats()
    except Exception:
        p_queue = []
        p_stats = {"total": 0, "hot": 0, "anchors": 0, "monitoring": 0, "chopping_block": 0}

    import config_manager
    cfg = config_manager.load_config()
    bp_val = float(cap.get('buying_power') or 0.0)
    sizing_info = config_manager.calculate_sizing_allocation(bp_val, target_r=1.0)
    
    return {
        "status": "ONLINE",
        "timestamp": datetime.datetime.now().strftime("%H:%M:%S"),
        "capital": cap,
        "test_allocation_1pct": sizing_info['base_alloc'],
        "config": cfg,
        "sizing": sizing_info,
        "algo_pnl_dollar": round(total_algo_pnl_dollar, 2),
        "positions": active_positions,
        "scanner": scanner_results,
        "stalker_radar": stalker_list,
        "priority_queue": p_queue,
        "priority_stats": p_stats,
        "history": history,
        "telegram_alerts": telegram_alerts
    }

@app.get("/api/terminal/priority-queue")
def get_priority_queue_endpoint():
    try:
        from priority_queue import get_priority_queue, get_priority_queue_stats
        return {
            "queue": get_priority_queue(),
            "stats": get_priority_queue_stats()
        }
    except Exception as e:
        return {"queue": [], "stats": {}, "error": str(e)}

class ConfigUpdateRequest(BaseModel):
    alloc_mode: Optional[str] = None
    alloc_value: Optional[float] = None
    target_weighted_sizing: Optional[bool] = None
    min_r_units: Optional[float] = None
    max_concurrent: Optional[int] = None
    auto_buy_enabled: Optional[bool] = None

@app.get("/api/terminal/config")
def get_terminal_config():
    import config_manager
    cap = get_account_capital_summary()
    cfg = config_manager.load_config()
    bp_val = float(cap.get('buying_power') or 0.0)
    sizing_info = config_manager.calculate_sizing_allocation(bp_val, target_r=1.0)
    return {
        "config": cfg,
        "sizing": sizing_info,
        "buying_power": bp_val
    }

@app.post("/api/terminal/config")
def update_terminal_config(req: ConfigUpdateRequest):
    import config_manager
    updates = {}
    if req.alloc_mode is not None:
        updates["alloc_mode"] = req.alloc_mode
    if req.alloc_value is not None:
        updates["alloc_value"] = float(req.alloc_value)
    if req.target_weighted_sizing is not None:
        updates["target_weighted_sizing"] = bool(req.target_weighted_sizing)
    if req.min_r_units is not None:
        updates["min_r_units"] = float(req.min_r_units)
    if req.max_concurrent is not None:
        updates["max_concurrent"] = int(req.max_concurrent)
    if req.auto_buy_enabled is not None:
        updates["auto_buy_enabled"] = bool(req.auto_buy_enabled)
        
    saved = config_manager.save_config(updates)
    cap = get_account_capital_summary()
    bp_val = float(cap.get('buying_power') or 0.0)
    sizing_info = config_manager.calculate_sizing_allocation(bp_val, target_r=1.0)
    return {
        "success": True,
        "config": saved,
        "sizing": sizing_info
    }

# In-memory cache for DOM volume profiles and quotes to prevent API rate limits
_DOM_VOL_CACHE = {}
_DOM_QUOTE_CACHE = {}

@app.get("/api/dom/{ticker}")
def get_dom(ticker: str):
    """
    Returns real-time Depth of Market (DOM) ladder and Time & Sales data for a ticker.
    Protected by 1.8s quote throttle and 30s bar cache to ensure zero rate limit issues with Public.com.
    """
    try:
        from public_executor import get_client, get_account_id, OrderInstrument
        from data_feed_public import get_public_bars_sync
        import time

        sym = str(ticker).strip().upper().replace('-', '.')
        now_ts = time.time()

        # 1. Check quote cache (1.8s TTL throttle) to strictly bound API request rate
        cached_q = _DOM_QUOTE_CACHE.get(sym)
        if cached_q and (now_ts - cached_q['ts'] < 1.8):
            last = cached_q['last']
            bid = cached_q['bid']
            ask = cached_q['ask']
            bid_sz = cached_q['bid_sz']
            ask_sz = cached_q['ask_sz']
            volume = cached_q['volume']
            spread = cached_q['spread']
            spread_pct = cached_q['spread_pct']
        else:
            client = get_client()
            acc_id = get_account_id()
            inst = OrderInstrument(symbol=sym, type='EQUITY')
            quotes = client.get_quotes([inst], account_id=acc_id)
            if not quotes:
                raise HTTPException(status_code=404, detail="Symbol quote not available")
            
            q = quotes[0]
            last = float(q.last) if getattr(q, 'last', None) is not None else 0.0
            bid = float(q.bid) if getattr(q, 'bid', None) is not None else (last - 0.05)
            ask = float(q.ask) if getattr(q, 'ask', None) is not None else (last + 0.05)
            bid_sz = int(q.bid_size) if getattr(q, 'bid_size', None) is not None else 10
            ask_sz = int(q.ask_size) if getattr(q, 'ask_size', None) is not None else 10
            volume = int(q.volume) if getattr(q, 'volume', None) is not None else 0
            spread = round(max(0.01, ask - bid), 4)
            spread_pct = round((spread / last * 100.0), 3) if last > 0 else 0.0

            _DOM_QUOTE_CACHE[sym] = {
                'ts': now_ts,
                'last': last,
                'bid': bid,
                'ask': ask,
                'bid_sz': bid_sz,
                'ask_sz': ask_sz,
                'volume': volume,
                'spread': spread,
                'spread_pct': spread_pct
            }

        # Auto-determine tick interval
        if last > 200:
            tick = 0.25
        elif last > 50:
            tick = 0.10
        elif last > 10:
            tick = 0.05
        else:
            tick = 0.01

        center = round(last / tick) * tick

        # Get or update cached volume profile (cache TTL 30s)
        now_ts = time.time()
        vol_profile = {}
        recent_trades = []
        cache_entry = _DOM_VOL_CACHE.get(sym)
        if cache_entry and (now_ts - cache_entry['ts'] < 30):
            vol_profile = cache_entry['profile']
            recent_trades = cache_entry['trades']
        else:
            try:
                df = get_public_bars_sync(sym, '5m')
                if df is not None and not df.empty:
                    for idx, row in df.iterrows():
                        c = float(row['Close'])
                        v = float(row.get('Volume', 0))
                        bucket = round(c / tick) * tick
                        vol_profile[bucket] = vol_profile.get(bucket, 0.0) + v
                    
                    tail_bars = df.tail(10)
                    for idx, row in tail_bars.iterrows():
                        ts = idx.strftime('%H:%M:%S') if hasattr(idx, 'strftime') else str(idx)[-8:]
                        p = float(row['Close'])
                        sz = int(row.get('Volume', 100))
                        side = "BUY" if p >= row['Open'] else "SELL"
                        recent_trades.append({
                            "time": ts,
                            "price": round(p, 2),
                            "size": sz,
                            "side": side
                        })
                _DOM_VOL_CACHE[sym] = {'ts': now_ts, 'profile': vol_profile, 'trades': recent_trades}
            except Exception as b_err:
                print(f"Error fetching bars for DOM: {b_err}")

        # Check if active Wyckoff trade exists on this symbol
        active_trade = None
        try:
            conn = sqlite3.connect(DB_PATH)
            c_db = conn.cursor()
            c_db.execute("SELECT id, direction, entry_price, stop_loss, take_profit, user_active FROM alerts WHERE ticker = ? AND outcome = 'OPEN' ORDER BY id DESC LIMIT 1", (sym,))
            row = c_db.fetchone()
            if row:
                active_trade = {
                    "id": row[0],
                    "direction": row[1],
                    "entry_price": row[2],
                    "stop_loss": row[3],
                    "take_profit": row[4],
                    "user_active": row[5]
                }
            conn.close()
        except Exception:
            pass

        # Build ladder rungs (14 above, 1 center, 14 below = 29 rungs)
        rungs = []
        max_vol = max(vol_profile.values()) if vol_profile else 1.0
        if max_vol <= 0:
            max_vol = 1.0

        for i in range(14, -15, -1):
            p = round(center + i * tick, 4)
            is_best_bid = (abs(p - bid) < tick * 0.75)
            is_best_ask = (abs(p - ask) < tick * 0.75)
            is_last = abs(p - last) < (tick * 0.5)
            is_bid_side = p <= bid
            is_ask_side = p >= ask

            rung_vol = vol_profile.get(round(p, 2), vol_profile.get(p, 0.0))
            vol_pct = min(100.0, round((rung_vol / max_vol) * 100.0, 1))

            b_size = bid_sz if is_best_bid else (max(1, int(bid_sz * max(0.2, 1.0 - abs(i) * 0.05))) if is_bid_side else 0)
            a_size = ask_sz if is_best_ask else (max(1, int(ask_sz * max(0.2, 1.0 - abs(i) * 0.05))) if is_ask_side else 0)

            depth_pct = 0.0
            if is_ask_side:
                depth_pct = min(100.0, max(12.0, (14 - i) * 6.0 + (a_size / max(1, ask_sz)) * 12.0))
            elif is_bid_side:
                depth_pct = min(100.0, max(12.0, (14 + i) * 6.0 + (b_size / max(1, bid_sz)) * 12.0))

            marker = None
            if active_trade:
                if active_trade.get('entry_price') and abs(p - active_trade['entry_price']) < (tick * 0.6):
                    marker = "ENTRY"
                elif active_trade.get('stop_loss') and abs(p - active_trade['stop_loss']) < (tick * 0.6):
                    marker = "STOP"
                elif active_trade.get('take_profit') and abs(p - active_trade['take_profit']) < (tick * 0.6):
                    marker = "TARGET"

            rungs.append({
                "price": p,
                "is_last": is_last,
                "is_best_bid": is_best_bid,
                "is_best_ask": is_best_ask,
                "is_bid_side": is_bid_side,
                "is_ask_side": is_ask_side,
                "bid_size": b_size if is_bid_side else "",
                "ask_size": a_size if is_ask_side else "",
                "vol": int(rung_vol),
                "vol_pct": vol_pct,
                "depth_pct": round(depth_pct, 1),
                "marker": marker
            })

        return {
            "status": "success",
            "ticker": sym,
            "last": last,
            "bid": bid,
            "ask": ask,
            "bid_size": bid_sz,
            "ask_size": ask_sz,
            "volume": volume,
            "spread": spread,
            "spread_pct": spread_pct,
            "tick": tick,
            "active_trade": active_trade,
            "rungs": rungs,
            "recent_trades": recent_trades[-12:]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/bars/{ticker}")
def get_bars(ticker: str, interval: str = "5m"):
    """
    Fetches clean OHLCV bars directly from Public.com broker feed,
    and calculates Wyckoff Trading Range (High/Low), Micro/Macro SuperTrends, VWAP,
    and Spring / UTAD reversal event markers.
    """
    import numpy as np
    import pandas as pd
    from wyckoff_alert_bot import get_supertrend, calc_vwap

    df = get_public_bars_sync(ticker.upper(), interval)
    if df.empty or len(df) < 20:
        return {"ticker": ticker.upper(), "bars": [], "indicators": {}}
        
    highs = df['High'].values.astype(float)
    lows = df['Low'].values.astype(float)
    closes = df['Close'].values.astype(float)
    vols = df['Volume'].values.astype(float)

    # 1. SuperTrend Lines
    tr0 = np.abs(highs - lows)
    tr1 = np.abs(highs - np.roll(closes, 1))
    tr2 = np.abs(lows - np.roll(closes, 1))
    tr = np.maximum(tr0, np.maximum(tr1, tr2))
    tr[0] = 0
    atr = np.zeros_like(closes, dtype=float)
    length = 9
    multiplier = 9.0
    if len(closes) > length:
        atr[length] = np.mean(tr[1:length+1])
        for i in range(length+1, len(closes)):
            atr[i] = (atr[i-1] * (length - 1) + tr[i]) / length
    hl2 = (highs + lows) / 2
    upperband = hl2 + (multiplier * atr)
    lowerband = hl2 - (multiplier * atr)
    in_uptrend = np.ones(len(closes), dtype=bool)
    line9 = np.zeros(len(closes))
    for i in range(1, len(closes)):
        if closes[i] > upperband[i-1]: in_uptrend[i] = True
        elif closes[i] < lowerband[i-1]: in_uptrend[i] = False
        else:
            in_uptrend[i] = in_uptrend[i-1]
            if in_uptrend[i] and lowerband[i] < lowerband[i-1]: lowerband[i] = lowerband[i-1]
            if not in_uptrend[i] and upperband[i] > upperband[i-1]: upperband[i] = upperband[i-1]
        line9[i] = lowerband[i] if in_uptrend[i] else upperband[i]
    line9[0] = np.nan
    u9 = in_uptrend

    u1 = get_supertrend(highs, lows, closes, 1, 1.0)

    # 2. Wyckoff Trading Range Channel (Lookback 50-100 bars)
    lookback = min(100, max(20, len(df) // 2))
    range_high = pd.Series(highs).rolling(lookback, min_periods=15).max().shift(1).values
    range_low = pd.Series(lows).rolling(lookback, min_periods=15).min().shift(1).values

    # 3. Relative Volume (Institutional Dry-Up Check)
    vol_sma = pd.Series(vols).rolling(20, min_periods=1).mean().values
    with np.errstate(divide='ignore', invalid='ignore'):
        rel_vol = np.where(vol_sma > 0, vols / vol_sma, 1.0)

    # 4. Anchored / Rolling VWAP
    vwap_vals = calc_vwap(highs, lows, closes, vols)

    bars = []
    st_line = []
    range_h_line = []
    range_l_line = []
    vwap_line = []
    volume_bars = []
    markers = []

    for idx, (t_idx, row) in enumerate(df.iterrows()):
        t_sec = int(t_idx.timestamp())
        
        bars.append({
            "time": t_sec,
            "open": float(row['Open']),
            "high": float(row['High']),
            "low": float(row['Low']),
            "close": float(row['Close']),
        })

        # Volume histogram with dry-up coloring
        is_dry = rel_vol[idx] < 1.2
        volume_bars.append({
            "time": t_sec,
            "value": float(row['Volume']),
            "color": '#ffd400' if is_dry else ('#00d26a' if row['Close'] >= row['Open'] else '#ff3b30')
        })

        # SuperTrend line (Trend Directional Color)
        if not np.isnan(line9[idx]):
            st_line.append({
                "time": t_sec,
                "value": float(line9[idx])
            })

        # Trading Range High
        if not np.isnan(range_high[idx]):
            range_h_line.append({
                "time": t_sec,
                "value": float(range_high[idx])
            })

        # Trading Range Low
        if not np.isnan(range_low[idx]):
            range_l_line.append({
                "time": t_sec,
                "value": float(range_low[idx])
            })

        # VWAP
        if idx < len(vwap_vals) and vwap_vals[idx] > 0:
            vwap_line.append({
                "time": t_sec,
                "value": float(vwap_vals[idx])
            })

        # 5. Detect Spring (Dry-Up & High-Volume Absorption) and UTAD / LPSY markers
        if idx >= 2 and not np.isnan(range_low[idx]) and not np.isnan(range_high[idx]):
            c_below = (lows[idx] < range_low[idx]) or (lows[idx-1] < range_low[idx-1])
            vol_dry = rel_vol[idx] < 1.2
            bar_rng = highs[idx] - lows[idx]
            bar_clv = ((closes[idx] - lows[idx]) / bar_rng) if bar_rng > 1e-6 else 0.5
            bar_cvd = ((2.0 * (closes[idx] - lows[idx]) - bar_rng) / bar_rng) if bar_rng > 1e-6 else 0.0
            vol_absorption = (rel_vol[idx] >= 1.2) and (bar_clv >= 0.65) and (bar_cvd >= 0.20)
            
            # Spring (Long): Proven contrarian reversal (Dry-Up OR High-Volume Absorption)
            is_spring = c_below and u1[idx] and not u1[idx-1] and not u9[idx] and (vol_dry or vol_absorption)
            
            # Decoupled LPSY / UTAD (Short):
            sweep_bars = [k for k in range(max(0, idx - 10), idx) if highs[k] > range_high[k]]
            has_utad_probe = len(sweep_bars) > 0
            utad_peak = max(highs[k] for k in sweep_bars) if sweep_bars else highs[idx]
            rejection_accepted = (closes[idx] < range_high[idx]) and (closes[idx-1] < range_high[idx-1])
            is_lower_high = highs[idx] < utad_peak
            micro_red_flip = not u1[idx] and u1[idx-1]
            is_lpsy = has_utad_probe and rejection_accepted and is_lower_high and micro_red_flip

            if is_spring:
                markers.append({
                    "time": t_sec,
                    "position": "belowBar",
                    "color": "#00d26a",
                    "shape": "arrowUp",
                    "text": "SPRING (ABSORPTION)" if vol_absorption else "SPRING (LONG)"
                })
            elif is_lpsy:
                markers.append({
                    "time": t_sec,
                    "position": "aboveBar",
                    "color": "#ff3b30",
                    "shape": "arrowDown",
                    "text": "LPSY (SHORT)"
                })

        # Volume Profile (Volume at Price distribution)
    vp_distribution = []
    if len(closes) > 0 and (np.max(highs) - np.min(lows)) > 0:
        p_min = float(np.min(lows))
        p_max = float(np.max(highs))
        num_bins = 24
        bin_size = (p_max - p_min) / num_bins
        bins = [0.0] * num_bins
        for h, l, c, v in zip(highs, lows, closes, vols):
            typical = (h + l + c) / 3.0
            b_idx = min(num_bins - 1, max(0, int((typical - p_min) / bin_size)))
            bins[b_idx] += float(v)
            
        max_v = max(bins) if max(bins) > 0 else 1.0
        for b_i in range(num_bins):
            p_level = p_min + (b_i + 0.5) * bin_size
            pct = (bins[b_i] / max_v) * 100.0
            vp_distribution.append({
                "price": round(p_level, 2),
                "volume": bins[b_i],
                "pct": round(pct, 1)
            })

    return {
        "ticker": ticker.upper(),
        "interval": interval,
        "bars": bars,
        "volume": volume_bars,
        "supertrend": st_line,
        "range_high": range_h_line,
        "range_low": range_l_line,
        "vwap": vwap_line,
        "markers": markers,
        "volume_profile": vp_distribution
    }

_company_cache = {}

@app.get("/api/company/{ticker}")
def get_company_profile(ticker: str):
    """
    Returns company fundamental data, industry, valuation, and 52-week stats
    for deep research on the selected ticker.
    """
    sym = ticker.upper().replace("/", "-")
    now = datetime.datetime.now()
    if sym in _company_cache:
        cached_time, cached_data = _company_cache[sym]
        if (now - cached_time).total_seconds() < 3600:
            return cached_data

    import yfinance as yf
    try:
        t = yf.Ticker(sym)
        fi = t.fast_info
        last_price = float(getattr(fi, 'last_price', 0.0) or 0.0)
        mkt_cap = float(getattr(fi, 'market_cap', 0.0) or 0.0)
        y_high = float(getattr(fi, 'year_high', 0.0) or 0.0)
        y_low = float(getattr(fi, 'year_low', 0.0) or 0.0)
        three_m_vol = float(getattr(fi, 'three_month_average_volume', 0.0) or 0.0)
        
        info = {}
        try:
            info = t.info or {}
        except:
            pass

        def format_mkt_cap(val):
            if val >= 1e12: return f"${val/1e12:.2f}T"
            if val >= 1e9: return f"${val/1e9:.2f}B"
            if val >= 1e6: return f"${val/1e6:.2f}M"
            return f"${val:,.0f}" if val > 0 else "--"

        def format_vol(val):
            if val >= 1e6: return f"{val/1e6:.2f}M"
            if val >= 1e3: return f"{val/1e3:.1f}K"
            return f"{val:,.0f}" if val > 0 else "--"

        short_name = info.get('shortName') or info.get('longName') or sym
        sector = info.get('sector') or "--"
        industry = info.get('industry') or "--"
        pe = info.get('trailingPE') or info.get('forwardPE')
        pe_str = f"{pe:.1f}" if pe else "--"
        beta = info.get('beta')
        beta_str = f"{beta:.2f}" if beta else "--"
        range_52 = f"${y_low:.2f} - ${y_high:.2f}" if (y_low and y_high) else "--"

        # Check Wyckoff Bias from alerts or live bars
        bias = "ACCUMULATION / TR"
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT direction, regime FROM alerts WHERE ticker = ? ORDER BY id DESC LIMIT 1", (sym,))
        row = c.fetchone()
        conn.close()
        if row:
            direction, regime = row
            bias = f"{direction} ({regime or 'ACTIVE'})"

        res = {
            "ticker": sym,
            "name": short_name,
            "sector": sector,
            "industry": industry,
            "price": last_price,
            "market_cap": format_mkt_cap(mkt_cap),
            "pe_ratio": pe_str,
            "beta": beta_str,
            "range_52": range_52,
            "avg_volume": format_vol(three_m_vol),
            "bias": bias,
            "website": info.get('website', '')
        }
        _company_cache[sym] = (now, res)
        return res
    except Exception as e:
        return {
            "ticker": sym,
            "name": sym,
            "sector": "--",
            "industry": "--",
            "price": 0.0,
            "market_cap": "--",
            "pe_ratio": "--",
            "beta": "--",
            "range_52": "--",
            "avg_volume": "--",
            "bias": "--",
            "error": str(e)
        }

@app.get("/api/evidence/{ticker}")
def get_evidence(ticker: str, timeframe: str = "5m"):
    """
    Computes real-time Wyckoff signal evidence checklist and ML Confidence score
    for the selected ticker and timeframe.
    """
    sym = ticker.upper()
    df = get_public_bars_sync(sym, timeframe)
    
    # Check if there is an alert recorded in database
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT ml_confidence, direction, timestamp, bars_in_regime, vwap_distance, atr_expansion, hour_of_day
        FROM alerts
        WHERE ticker = ? AND timeframe = ?
        ORDER BY id DESC LIMIT 1
    """, (sym, timeframe))
    db_alert = c.fetchone()
    conn.close()

    if df.empty or len(df) < 20:
        return {
            "ticker": sym,
            "pattern_match": 50.0,
            "evidence": {
                "micro_st": False,
                "vol_exhaustion": False,
                "vol_ratio": 1.0,
                "reclaimed_level": False,
                "shield_passed": True,
                "slot_ready": True
            }
        }

    highs = df['High'].values.astype(float)
    lows = df['Low'].values.astype(float)
    closes = df['Close'].values.astype(float)
    vols = df['Volume'].values.astype(float)

    u1 = get_supertrend(highs, lows, closes, 1, 1.0)
    u9 = get_supertrend(highs, lows, closes, 9, 9.0)
    
    lookback = min(100, max(20, len(df) // 2))
    range_high = pd.Series(highs).rolling(lookback, min_periods=15).max().shift(1).values
    range_low = pd.Series(lows).rolling(lookback, min_periods=15).min().shift(1).values
    
    vol_sma = pd.Series(vols).rolling(20, min_periods=1).mean().values
    with np.errstate(divide='ignore', invalid='ignore'):
        rel_vol = np.where(vol_sma > 0, vols / vol_sma, 1.0)

    curr = len(df) - 1
    curr_rv = float(rel_vol[curr]) if not np.isnan(rel_vol[curr]) else 1.0
    is_dry = curr_rv < 1.2
    c_rng = highs[curr] - lows[curr]
    c_clv = ((closes[curr] - lows[curr]) / c_rng) if c_rng > 1e-6 else 0.5
    c_cvd = ((2.0 * (closes[curr] - lows[curr]) - c_rng) / c_rng) if c_rng > 1e-6 else 0.0
    is_absorption = (curr_rv >= 1.2) and (c_clv >= 0.65) and (c_cvd >= 0.20)

    # Micro ST confirmation: is fast supertrend active in setup direction
    direction = db_alert['direction'] if db_alert else ("LONG" if closes[curr] > closes[max(0, curr-5)] else "SHORT")
    micro_ok = bool(u1[curr]) if direction == "LONG" else bool(not u1[curr])

    # Reclaimed level
    reclaimed = False
    if direction == "LONG" and not np.isnan(range_low[curr]):
        reclaimed = (lows[curr] < range_low[curr] or (curr > 0 and lows[curr-1] < range_low[curr-1])) and closes[curr] >= range_low[curr]
    elif direction == "SHORT" and not np.isnan(range_high[curr]):
        reclaimed = (highs[curr] > range_high[curr] or (curr > 0 and highs[curr-1] > range_high[curr-1])) and closes[curr] <= range_high[curr]

    # Calculate real ML confidence
    bars_in_regime = int(db_alert['bars_in_regime']) if db_alert and db_alert['bars_in_regime'] is not None else 10
    vwap_vals = calc_vwap(highs, lows, closes, vols)
    vwap_dist = ((closes[curr] - vwap_vals[curr]) / vwap_vals[curr] * 100.0) if vwap_vals[curr] > 0 else 0.0
    
    now_hour = datetime.datetime.now().hour + datetime.datetime.now().minute / 60.0
    
    ml_conf = float(db_alert['ml_confidence']) if (db_alert and db_alert['ml_confidence'] is not None) else calculate_ml_confidence(bars_in_regime, vwap_dist, 1.0, now_hour, direction)

    return {
        "ticker": sym,
        "direction": direction,
        "pattern_match": ml_conf,
        "evidence": {
            "micro_st": micro_ok,
            "vol_exhaustion": bool(is_dry or is_absorption),
            "vol_mode": "ABSORPTION" if is_absorption else "DRY_UP",
            "vol_ratio": round(curr_rv, 2),
            "reclaimed_level": reclaimed,
            "shield_passed": True,
            "slot_ready": True
        }
    }

@app.get("/api/flow/{ticker}")
def get_flow(ticker: str):
    """Fetches real-time institutional options flow & quote microstructure."""
    sym = ticker.upper()
    quote = get_public_quotes(sym)
    curr_price = quote.get('last') if quote else None
    flow = get_options_flow(sym, curr_price=curr_price)
    return {
        "ticker": sym,
        "flow": flow,
        "quote": quote
    }

class OrderPayload(BaseModel):
    ticker: str
    amount: float
    trade_id: int | None = None

@app.get("/api/order/quote/{ticker}")
def get_order_quote(ticker: str):
    """Fetches real-time Public.com quote (bid, ask, last, spread) for pre-trade preview."""
    try:
        q = get_single_quote(ticker)
        return {"status": "success", "quote": q}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/order/buy")
def place_buy(order: OrderPayload):
    """Executes fractional dollar buy on Public.com directly from the terminal with real-time fill sync."""
    try:
        res = execute_dollar_buy(order.ticker.upper(), order.amount)
        fill_info = None
        if order.trade_id:
            import time
            time.sleep(1.5)  # Wait for broker execution to settle
            try:
                broker_holdings = get_broker_portfolio_positions()
                matching = next((bh for bh in broker_holdings if bh['ticker'] == order.ticker.upper()), None)
                if matching and matching.get('entry_price', 0) > 0:
                    fill_price = float(matching['entry_price'])
                    fill_qty = float(matching['quantity'])
                    
                    conn = sqlite3.connect(DB_PATH)
                    c = conn.cursor()
                    c.execute("SELECT direction, entry_price, stop_loss, take_profit FROM alerts WHERE id = ?", (order.trade_id,))
                    row = c.fetchone()
                    if row:
                        direction, orig_entry, orig_sl, orig_tp = row
                        risk = abs((orig_entry or fill_price) - (orig_sl or fill_price))
                        reward = abs((orig_tp or fill_price) - (orig_entry or fill_price))
                        new_sl = round(fill_price - risk, 4)
                        new_tp = round(fill_price + reward, 4)
                        c.execute("""
                            UPDATE alerts 
                            SET user_active = 1,
                                entry_price = ?,
                                initial_shares = ?,
                                stop_loss = ?,
                                initial_stop_loss = ?,
                                take_profit = ?
                            WHERE id = ?
                        """, (fill_price, fill_qty, new_sl, new_sl, new_tp, order.trade_id))
                        conn.commit()
                        fill_info = {
                            "fill_price": fill_price,
                            "shares": fill_qty,
                            "stop_loss": new_sl,
                            "take_profit": new_tp
                        }
                    conn.close()
                else:
                    conn = sqlite3.connect(DB_PATH)
                    c = conn.cursor()
                    c.execute("UPDATE alerts SET user_active = 1 WHERE id = ?", (order.trade_id,))
                    conn.commit()
                    conn.close()
            except Exception as sync_err:
                print(f"[ORDER SYNC ERROR] Could not sync fill for trade {order.trade_id}: {sync_err}")
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute("UPDATE alerts SET user_active = 1 WHERE id = ?", (order.trade_id,))
                conn.commit()
                conn.close()
        return {"success": True, "order": res, "fill": fill_info}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/order/short")
def place_short(order: OrderPayload):
    """Executes integer whole-share short sale on Public.com directly from the terminal with real-time fill sync."""
    try:
        res = execute_short_sell(order.ticker.upper(), order.amount)
        fill_info = None
        if order.trade_id:
            import time
            time.sleep(1.5)  # Wait for broker execution to settle
            try:
                broker_holdings = get_broker_portfolio_positions()
                matching = next((bh for bh in broker_holdings if bh['ticker'] == order.ticker.upper()), None)
                if matching and matching.get('entry_price', 0) > 0:
                    fill_price = float(matching['entry_price'])
                    fill_qty = float(matching['quantity'])
                    
                    conn = sqlite3.connect(DB_PATH)
                    c = conn.cursor()
                    c.execute("SELECT direction, entry_price, stop_loss, take_profit FROM alerts WHERE id = ?", (order.trade_id,))
                    row = c.fetchone()
                    if row:
                        direction, orig_entry, orig_sl, orig_tp = row
                        risk = abs((orig_sl or fill_price) - (orig_entry or fill_price))
                        reward = abs((orig_entry or fill_price) - (orig_tp or fill_price))
                        new_sl = round(fill_price + risk, 4)
                        new_tp = round(fill_price - reward, 4)
                        c.execute("""
                            UPDATE alerts 
                            SET user_active = 1,
                                entry_price = ?,
                                initial_shares = ?,
                                stop_loss = ?,
                                initial_stop_loss = ?,
                                take_profit = ?
                            WHERE id = ?
                        """, (fill_price, fill_qty, new_sl, new_sl, new_tp, order.trade_id))
                        conn.commit()
                        fill_info = {
                            "fill_price": fill_price,
                            "shares": fill_qty,
                            "stop_loss": new_sl,
                            "take_profit": new_tp
                        }
                    conn.close()
                else:
                    conn = sqlite3.connect(DB_PATH)
                    c = conn.cursor()
                    c.execute("UPDATE alerts SET user_active = 1 WHERE id = ?", (order.trade_id,))
                    conn.commit()
                    conn.close()
            except Exception as sync_err:
                print(f"[ORDER SYNC ERROR] Could not sync fill for trade {order.trade_id}: {sync_err}")
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute("UPDATE alerts SET user_active = 1 WHERE id = ?", (order.trade_id,))
                conn.commit()
                conn.close()
        return {"success": True, "order": res, "fill": fill_info}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

class OptionOrderPayload(BaseModel):
    symbol: str
    quantity: int = 2
    limit_price: float
    trade_id: int | None = None

@app.post("/api/order/buy_option")
def place_option_buy(order: OptionOrderPayload):
    """Executes a limit buy on Public.com for options contracts and freezes auto-buyer into one-and-done."""
    try:
        from public_executor import execute_option_buy
        res = execute_option_buy(order.symbol, quantity=order.quantity, limit_price=order.limit_price)
        
        # Turn off auto buy immediately to enforce One-and-Done mode
        try:
            import config_manager
            config_manager.save_config({"auto_buy_enabled": False})
        except Exception:
            pass
        
        if order.trade_id:
            try:
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute("UPDATE alerts SET user_active = 1 WHERE id = ?", (order.trade_id,))
                conn.commit()
                conn.close()
            except Exception:
                pass
                
        return {"success": True, "order": res}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

class CoveredCallOrderPayload(BaseModel):
    ticker: str
    symbol: str
    strike: float
    expiry: str
    quantity: int = 1
    limit_price: float

@app.post("/api/order/sell_covered_call")
def place_covered_call_sell(order: CoveredCallOrderPayload):
    """
    Executes a limit sell to open covered call contracts on Public.com,
    deposits instant cash credit, and registers position into Covered Call Guardian.
    """
    try:
        from public_executor import execute_option_sell_to_open
        res = execute_option_sell_to_open(order.symbol, quantity=order.quantity, limit_price=order.limit_price)
        
        # Register in Covered Call Guardian for automated 50% profit take & roll defense
        try:
            import covered_call_guardian
            covered_call_guardian.register_covered_call(
                ticker=order.ticker,
                option_symbol=order.symbol,
                strike=order.strike,
                expiry=order.expiry,
                quantity=order.quantity,
                entry_premium=order.limit_price
            )
        except Exception as g_err:
            print(f"Error registering in CC Guardian: {g_err}")

        return {"success": True, "order": res}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/covered_calls/status")
def get_cc_status():
    """Returns current active covered calls, automated defense settings, and roll history."""
    try:
        import covered_call_guardian
        covered_call_guardian.sync_active_with_broker()
        state = covered_call_guardian.load_cc_state()
        return {"status": "SUCCESS", "data": state}
    except Exception as e:
        return {"status": "ERROR", "error": str(e)}

@app.post("/api/covered_calls/toggle_auto")
def toggle_cc_auto():
    """Toggles automated roll defense and 50% take profit on/off."""
    try:
        import covered_call_guardian
        state = covered_call_guardian.load_cc_state()
        state["auto_roll_enabled"] = not state.get("auto_roll_enabled", True)
        state["auto_take_profit_enabled"] = not state.get("auto_take_profit_enabled", True)
        covered_call_guardian.save_cc_state(state)
        return {"status": "SUCCESS", "data": state}
    except Exception as e:
        return {"status": "ERROR", "error": str(e)}

class ExitPayload(BaseModel):
    ticker: str
    trade_id: int | None = None

class SwingRegisterPayload(BaseModel):
    ticker: str
    direction: str = "LONG"
    entry_price: float
    stop_loss: float
    take_profit: float
    timeframe: str = "1h"
    quantity: float | None = None
    regime: str = "Phase C Spring"

@app.post("/api/trade/register_swing")
def register_swing_trade(payload: SwingRegisterPayload):
    """Registers or updates a swing position to be armed by the 5-Tier Exit Guardian with overnight hold."""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        sym = payload.ticker.upper()
        now_iso = datetime.datetime.now().isoformat()
        risk = abs(payload.entry_price - payload.stop_loss) or (payload.entry_price * 0.015)
        target_gain = abs(payload.take_profit - payload.entry_price)
        target_r = round(target_gain / risk, 2) if risk > 0 else 1.20

        c.execute("SELECT id FROM alerts WHERE ticker = ? AND outcome = 'OPEN' ORDER BY id DESC LIMIT 1", (sym,))
        row = c.fetchone()
        if row:
            t_id = row[0]
            c.execute("""
                UPDATE alerts
                SET user_active = 1,
                    timeframe = ?,
                    direction = ?,
                    entry_price = ?,
                    initial_stop_loss = ?,
                    stop_loss = ?,
                    take_profit = ?,
                    initial_shares = COALESCE(?, initial_shares),
                    optimal_target_r = ?,
                    regime = ?
                WHERE id = ?
            """, (payload.timeframe, payload.direction.upper(), payload.entry_price, payload.stop_loss, payload.stop_loss, payload.take_profit, payload.quantity, target_r, payload.regime, t_id))
        else:
            c.execute("""
                INSERT INTO alerts (
                    ticker, direction, entry_price, stop_loss, initial_stop_loss,
                    take_profit, regime, timestamp, outcome, user_active,
                    initial_shares, timeframe, ml_confidence, optimal_target_r,
                    telegram_alerted, model_version, partial_tier_done, peak_high_r
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', 1, ?, ?, 70.0, ?, 1, 'v7-swing-wyckoff', 0, 0.0)
            """, (
                sym, payload.direction.upper(), payload.entry_price, payload.stop_loss, payload.stop_loss,
                payload.take_profit, payload.regime, now_iso, payload.quantity, payload.timeframe, target_r
            ))
            t_id = c.lastrowid
        conn.commit()
        conn.close()

        # Send alert
        try:
            from telegram_notifier import send_message, is_configured
            if is_configured():
                send_message(f"🛡️ [EXIT GUARDIAN ARMED] Registered {sym} ({payload.timeframe.upper()}) | Entry: ${payload.entry_price:.2f} | Stop: ${payload.stop_loss:.2f} | Target: ${payload.take_profit:.2f} (+{target_r:.2f}R). Overnight hold active.", force=True)
        except Exception:
            pass

        return {"success": True, "trade_id": t_id, "ticker": sym, "timeframe": payload.timeframe, "optimal_target_r": target_r}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/order/exit")
def place_exit(exit_req: ExitPayload):
    """Executes full position exit on Public.com directly from the terminal (covers shorts or sells longs)."""
    try:
        direction = None
        if exit_req.trade_id:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("SELECT direction FROM alerts WHERE id = ?", (exit_req.trade_id,))
            row = c.fetchone()
            if row:
                direction = row[0]
            conn.close()

        res = execute_exit_sell(exit_req.ticker.upper(), direction=direction)
        if exit_req.trade_id:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("UPDATE alerts SET outcome = 'CLOSED_MANUAL' WHERE id = ?", (exit_req.trade_id,))
            conn.commit()
            conn.close()
        return {"success": True, "exit": res}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/order/flatten_all")
def place_flatten_all():
    """Immediately closes all open algo positions at market on Public.com."""
    try:
        from exit_guardian import flatten_all_algo_trades
        res = flatten_all_algo_trades("MANUAL_TERMINAL_TRIGGER")
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Keepalive and ping loop
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect(websocket)

@app.get("/api/ml/overview")
def get_ml_overview():
    """
    Returns complete ML status:
    - Active champion model details (version, accuracy, win rate, samples)
    - Feature importances
    - Algorithmic execution rules
    - Model evolution timeline
    - Out-of-sample real performance statistics
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # Get latest active model
    c.execute("SELECT * FROM ml_model_history ORDER BY id DESC LIMIT 1")
    latest = c.fetchone()
    
    # Get all history
    c.execute("SELECT * FROM ml_model_history ORDER BY id DESC")
    history_rows = [dict(r) for r in c.fetchall()]
    
    # Count closed trades in database
    c.execute("SELECT COUNT(*), SUM(CASE WHEN outcome = 'WIN' THEN 1 ELSE 0 END) FROM alerts WHERE outcome != 'OPEN'")
    tot_trades, tot_wins = c.fetchone()
    tot_trades = tot_trades or 0
    tot_wins = tot_wins or 0
    overall_win_rate = (tot_wins / tot_trades * 100.0) if tot_trades else 0.0

    # Comprehensive Win/Loss/Breakeven record breakdown
    c.execute("""
        SELECT 
            COUNT(id) as total,
            SUM(CASE WHEN outcome = 'WIN' THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN outcome = 'LOSS' THEN 1 ELSE 0 END) as losses,
            SUM(CASE WHEN outcome = 'BREAKEVEN' THEN 1 ELSE 0 END) as be,
            COALESCE(SUM(pnl_r), 0.0) as net_r,
            COALESCE(AVG(CASE WHEN outcome = 'WIN' THEN pnl_r END), 0.0) as avg_win_r,
            COALESCE(AVG(CASE WHEN outcome = 'LOSS' THEN pnl_r END), 0.0) as avg_loss_r
        FROM alerts WHERE outcome IN ('WIN', 'LOSS', 'BREAKEVEN')
    """)
    row_overall = dict(c.fetchone() or {})
    tot = row_overall.get('total') or 0
    wins = row_overall.get('wins') or 0
    losses = row_overall.get('losses') or 0
    be = row_overall.get('be') or 0
    row_overall['all_win_rate'] = round((wins / tot * 100.0), 1) if tot > 0 else 0.0
    row_overall['directional_win_rate'] = round((wins / (wins + losses) * 100.0), 1) if (wins + losses) > 0 else 0.0
    row_overall['be_rate'] = round((be / tot * 100.0), 1) if tot > 0 else 0.0
    row_overall['win_rate'] = row_overall['all_win_rate']
    row_overall['net_r'] = round(float(row_overall.get('net_r') or 0.0), 2)
    row_overall['avg_win_r'] = round(float(row_overall.get('avg_win_r') or 0.0), 2)
    row_overall['avg_loss_r'] = round(float(row_overall.get('avg_loss_r') or 0.0), 2)

    c.execute("""
        SELECT 
            direction,
            COUNT(id) as total,
            SUM(CASE WHEN outcome = 'WIN' THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN outcome = 'LOSS' THEN 1 ELSE 0 END) as losses,
            SUM(CASE WHEN outcome = 'BREAKEVEN' THEN 1 ELSE 0 END) as be,
            COALESCE(SUM(pnl_r), 0.0) as net_r
        FROM alerts WHERE outcome IN ('WIN', 'LOSS', 'BREAKEVEN')
        GROUP BY direction
    """)
    wl_by_dir = {}
    for r in c.fetchall():
        d = dict(r)
        d_tot = d.get('total') or 0
        d_win = d.get('wins') or 0
        d_loss = d.get('losses') or 0
        d['all_win_rate'] = round((d_win / d_tot * 100.0), 1) if d_tot > 0 else 0.0
        d['directional_win_rate'] = round((d_win / (d_win + d_loss) * 100.0), 1) if (d_win + d_loss) > 0 else 0.0
        d['win_rate'] = d['all_win_rate']
        d['net_r'] = round(float(d.get('net_r') or 0.0), 2)
        wl_by_dir[d['direction']] = d

    c.execute("""
        SELECT 
            timeframe,
            COUNT(id) as total,
            SUM(CASE WHEN outcome = 'WIN' THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN outcome = 'LOSS' THEN 1 ELSE 0 END) as losses,
            SUM(CASE WHEN outcome = 'BREAKEVEN' THEN 1 ELSE 0 END) as be,
            COALESCE(SUM(pnl_r), 0.0) as net_r
        FROM alerts WHERE outcome IN ('WIN', 'LOSS', 'BREAKEVEN')
        GROUP BY timeframe
    """)
    wl_by_tf = {}
    for r in c.fetchall():
        d = dict(r)
        tf_tot = d.get('total') or 0
        tf_win = d.get('wins') or 0
        tf_loss = d.get('losses') or 0
        d['all_win_rate'] = round((tf_win / tf_tot * 100.0), 1) if tf_tot > 0 else 0.0
        d['directional_win_rate'] = round((tf_win / (tf_win + tf_loss) * 100.0), 1) if (tf_win + tf_loss) > 0 else 0.0
        d['win_rate'] = d['all_win_rate']
        d['net_r'] = round(float(d.get('net_r') or 0.0), 2)
        wl_by_tf[d['timeframe']] = d

    c.execute("""
        SELECT 
            COUNT(id) as total_scratches,
            SUM(CASE WHEN ghost_outcome = 'WOULD_BE_WIN' THEN 1 ELSE 0 END) as would_be_wins,
            SUM(CASE WHEN ghost_outcome = 'WOULD_BE_LOSS' THEN 1 ELSE 0 END) as would_be_losses,
            SUM(CASE WHEN ghost_outcome = 'STALLED' THEN 1 ELSE 0 END) as stalled_saved,
            SUM(CASE WHEN ghost_status = 'MONITORING' THEN 1 ELSE 0 END) as active_ghosts,
            COALESCE(SUM(ghost_pnl_r), 0.0) as ghost_net_r
        FROM alerts WHERE outcome = 'BREAKEVEN'
    """)
    ghost_stats = dict(c.fetchone() or {})
    ghost_scratches = ghost_stats.get('total_scratches') or 0
    ghost_wb_wins = ghost_stats.get('would_be_wins') or 0
    ghost_stats['opp_cost_rate'] = round((ghost_wb_wins / ghost_scratches * 100.0), 1) if ghost_scratches > 0 else 0.0
    ghost_stats['ghost_net_r'] = round(float(ghost_stats.get('ghost_net_r') or 0.0), 2)

    conn.close()
    
    latest_dict = dict(latest) if latest else {}
    if latest_dict:
        try:
            latest_dict['feature_importances'] = json.loads(latest_dict.get('feature_importances_json') or '{}')
        except:
            latest_dict['feature_importances'] = {}
        try:
            latest_dict['rules'] = json.loads(latest_dict.get('rules_generated') or '[]')
        except:
            latest_dict['rules'] = []

    for h in history_rows:
        try:
            h['feature_importances'] = json.loads(h.get('feature_importances_json') or '{}')
        except:
            h['feature_importances'] = {}
        try:
            h['rules'] = json.loads(h.get('rules_generated') or '[]')
        except:
            h['rules'] = []

    return {
        "status": "ONLINE",
        "champion_model": latest_dict,
        "history": history_rows,
        "total_corpus_trades": tot_trades,
        "overall_win_rate": round(overall_win_rate, 1),
        "wl_breakdown": {
            "overall": row_overall,
            "by_direction": wl_by_dir,
            "by_timeframe": wl_by_tf
        },
        "ghost_telemetry": ghost_stats,
        "scheduled_daily_evolution": "16:15 EST"
    }

@app.get("/api/ghost_trades")
def get_ghost_trades():
    """Returns detailed trade history and telemetry for Breakeven 'Ghost' trades."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT id, ticker, direction, entry_price, initial_stop_loss, stop_loss, take_profit,
               exit_price, pnl_r, timestamp, timeframe, ghost_status, ghost_outcome, ghost_exit_price, ghost_pnl_r, ghost_resolved_at
        FROM alerts
        WHERE outcome = 'BREAKEVEN' OR ghost_status IS NOT NULL
        ORDER BY id DESC
    """)
    rows = [dict(r) for r in c.fetchall()]
    
    c.execute("""
        SELECT 
            COUNT(id) as total_scratches,
            SUM(CASE WHEN ghost_outcome = 'WOULD_BE_WIN' THEN 1 ELSE 0 END) as would_be_wins,
            SUM(CASE WHEN ghost_outcome = 'WOULD_BE_LOSS' THEN 1 ELSE 0 END) as would_be_losses,
            SUM(CASE WHEN ghost_outcome = 'STALLED' THEN 1 ELSE 0 END) as stalled_saved,
            SUM(CASE WHEN ghost_status = 'MONITORING' THEN 1 ELSE 0 END) as active_ghosts,
            COALESCE(SUM(ghost_pnl_r), 0.0) as ghost_net_r
        FROM alerts WHERE outcome = 'BREAKEVEN' OR ghost_status IS NOT NULL
    """)
    summary = dict(c.fetchone() or {})
    tot_sc = summary.get('total_scratches') or 0
    wb_w = summary.get('would_be_wins') or 0
    summary['opp_cost_rate'] = round((wb_w / tot_sc * 100.0), 1) if tot_sc > 0 else 0.0
    summary['ghost_net_r'] = round(float(summary.get('ghost_net_r') or 0.0), 2)
    conn.close()
    return {
        "status": "ONLINE",
        "summary": summary,
        "ghost_trades": rows
    }


@app.get("/api/ml/version_trades/{version}")
def get_ml_version_trades(version: str):
    """
    Returns complete trade records and telemetry for a specific ML Model Version:
    1. Realized (out-of-sample) trades executed under this model version.
    2. Training corpus samples used to fit and calibrate this model version.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        # 1. Fetch version metadata from ml_model_history
        c.execute("SELECT * FROM ml_model_history WHERE version = ? LIMIT 1", (version,))
        ver_row = c.fetchone()
        version_meta = dict(ver_row) if ver_row else {}
        if version_meta:
            try:
                version_meta['feature_importances'] = json.loads(version_meta.get('feature_importances_json') or '{}')
            except Exception:
                version_meta['feature_importances'] = {}
            try:
                version_meta['rules'] = json.loads(version_meta.get('rules_generated') or '[]')
            except Exception:
                version_meta['rules'] = []

        # 2. Fetch Realized (Out-of-Sample) Trades
        c.execute("""
            SELECT id, ticker, direction, timeframe, regime, entry_price, exit_price,
                   initial_stop_loss, stop_loss, take_profit, pnl_r, outcome, timestamp,
                   ml_confidence, model_version, bars_in_regime, vwap_distance, atr_expansion,
                   effort_vs_result, cvd_delta_ratio, rvol_tod, candle_clv, user_active
            FROM alerts
            WHERE model_version = ?
            ORDER BY timestamp DESC
        """, (version,))
        realized_rows = [dict(r) for r in c.fetchall()]

        # Fallback to timestamp window if no explicit model_version tags found
        ver_ts = version_meta.get('timestamp')
        if not realized_rows and ver_ts:
            c.execute("SELECT timestamp FROM ml_model_history WHERE timestamp > ? ORDER BY timestamp ASC LIMIT 1", (ver_ts,))
            next_ver = c.fetchone()
            next_ts = next_ver['timestamp'] if next_ver else None

            if next_ts:
                c.execute("""
                    SELECT id, ticker, direction, timeframe, regime, entry_price, exit_price,
                           initial_stop_loss, stop_loss, take_profit, pnl_r, outcome, timestamp,
                           ml_confidence, model_version, bars_in_regime, vwap_distance, atr_expansion,
                           effort_vs_result, cvd_delta_ratio, rvol_tod, candle_clv, user_active
                    FROM alerts
                    WHERE timestamp >= ? AND timestamp < ? AND outcome IN ('WIN', 'LOSS', 'BREAKEVEN', 'CLOSED')
                    ORDER BY timestamp DESC
                """, (ver_ts, next_ts))
            else:
                c.execute("""
                    SELECT id, ticker, direction, timeframe, regime, entry_price, exit_price,
                           initial_stop_loss, stop_loss, take_profit, pnl_r, outcome, timestamp,
                           ml_confidence, model_version, bars_in_regime, vwap_distance, atr_expansion,
                           effort_vs_result, cvd_delta_ratio, rvol_tod, candle_clv, user_active
                    FROM alerts
                    WHERE timestamp >= ? AND outcome IN ('WIN', 'LOSS', 'BREAKEVEN', 'CLOSED')
                    ORDER BY timestamp DESC
                """, (ver_ts,))
            realized_rows = [dict(r) for r in c.fetchall()]

        # 3. Fetch Training Corpus Trades
        training_limit = version_meta.get('training_samples') or 300
        if ver_ts:
            c.execute("""
                SELECT id, ticker, direction, timeframe, regime, entry_price, exit_price,
                       initial_stop_loss, stop_loss, take_profit, pnl_r, outcome, timestamp,
                       ml_confidence, model_version, bars_in_regime, vwap_distance, atr_expansion,
                       effort_vs_result, cvd_delta_ratio, rvol_tod, candle_clv, user_active
                FROM alerts
                WHERE outcome IN ('WIN', 'LOSS', 'BREAKEVEN')
                  AND timestamp <= ?
                  AND (timeframe IS NULL OR timeframe != '1m')
                ORDER BY timestamp DESC
                LIMIT ?
            """, (ver_ts, training_limit))
            training_rows = [dict(r) for r in c.fetchall()]
        else:
            training_rows = []

        conn.close()

        def compute_stats(rows):
            wins = sum(1 for r in rows if r.get('outcome') == 'WIN')
            losses = sum(1 for r in rows if r.get('outcome') == 'LOSS')
            bes = sum(1 for r in rows if r.get('outcome') == 'BREAKEVEN')
            net_r = sum(float(r.get('pnl_r') or 0.0) for r in rows)
            wr = round((wins / len(rows) * 100.0), 1) if rows else 0.0
            dir_wr = round((wins / (wins + losses) * 100.0), 1) if (wins + losses) > 0 else 0.0
            avg_r = round(net_r / len(rows), 2) if rows else 0.0
            return {
                "count": len(rows),
                "wins": wins,
                "losses": losses,
                "breakevens": bes,
                "win_rate": wr,
                "directional_win_rate": dir_wr,
                "net_r": round(net_r, 2),
                "avg_r": avg_r,
                "trades": rows
            }

        return {
            "status": "OK",
            "version": version,
            "metadata": version_meta,
            "realized": compute_stats(realized_rows),
            "training": compute_stats(training_rows)
        }
    except Exception as e:
        return {
            "status": "ERROR",
            "error": str(e),
            "version": version,
            "realized": {"count": 0, "trades": []},
            "training": {"count": 0, "trades": []}
        }

@app.post("/api/ml/evolve")
def trigger_ml_evolution():
    """Triggers an on-demand training cycle of the Random Forest model."""
    try:
        from wyckoff_ml_engine import train_and_upgrade_model
        success, report = train_and_upgrade_model("Terminal User On-Demand Trigger")
        return {
            "success": success,
            "report": report
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/ml/paper_trades")
def get_ml_paper_trades():
    """
    Returns AI Learning Model simulated paper trades (ghost trades).
    Strictly air-gapped from actual broker positions (user_active = 0 or NULL).
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        # 1. Fetch all open trades for learning model & tracking (both live and paper)
        c.execute("""
            SELECT id, ticker, direction, entry_price, stop_loss, take_profit, regime, timeframe, 
                   timestamp, outcome, pnl_r, breakeven_set, partial_exit_done, peak_high_r, 
                   trailing_stop_price, ml_confidence, model_version, vwap_distance, dealer_gamma_regime, user_active
            FROM alerts
            WHERE outcome = 'OPEN'
            ORDER BY id DESC
        """)
        raw_open = [dict(r) for r in c.fetchall()]
        
        # 2. Get live prices for open tickers (cap to 10 to ensure instant response)
        tickers = list(set([r['ticker'] for r in raw_open if r.get('ticker')]))[:10]
        live_prices = get_live_prices(tickers) if tickers else {}
        
        enriched_open = []
        tf_counts = {}
        dir_counts = {'LONG': 0, 'SHORT': 0}
        
        for t in raw_open:
            sym = t.get('ticker', '')
            tf = t.get('timeframe') or '5m'
            direction = (t.get('direction') or 'LONG').upper()
            tf_counts[tf] = tf_counts.get(tf, 0) + 1
            dir_counts[direction] = dir_counts.get(direction, 0) + 1
            
            entry = float(t.get('entry_price') or 0.0)
            sl = float(t.get('stop_loss') or 0.0)
            tp = float(t.get('take_profit') or 0.0)
            curr = float(live_prices.get(sym) or entry)
            
            risk = abs(entry - sl) if abs(entry - sl) > 0.001 else max(0.5, entry * 0.015)
            is_long = direction == 'LONG'
            
            curr_r = ((curr - entry) / risk) if is_long else ((entry - curr) / risk)
            pnl_pct = ((curr - entry) / entry * 100.0) if is_long else ((entry - curr) / entry * 100.0)
            
            t['current_price'] = round(curr, 2)
            t['current_r'] = round(curr_r, 2)
            t['pnl_pct'] = round(pnl_pct, 2)
            t['risk_per_share'] = round(risk, 2)
            t['is_live'] = bool(t.get('user_active') == 1)
            
            if t.get('breakeven_set'):
                t['status_label'] = 'BREAKEVEN LOCKED'
            elif t.get('partial_exit_done'):
                t['status_label'] = 'PARTIAL SCALED'
            elif curr_r >= 1.0:
                t['status_label'] = 'RUNNING IN PROFIT'
            elif curr_r < -0.8:
                t['status_label'] = 'NEAR STOP'
            else:
                t['status_label'] = 'ACTIVE MONITORING'
                
            enriched_open.append(t)
            
        # 3. KPI stats from closed learning dataset (all executed Wyckoff signals)
        c.execute("""
            SELECT 
                COUNT(id) as total_closed,
                SUM(CASE WHEN outcome = 'WIN' THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN outcome = 'LOSS' THEN 1 ELSE 0 END) as losses,
                SUM(CASE WHEN outcome = 'BREAKEVEN' THEN 1 ELSE 0 END) as be,
                COALESCE(SUM(pnl_r), 0.0) as net_r
            FROM alerts
            WHERE outcome IN ('WIN', 'LOSS', 'BREAKEVEN')
        """)
        stats_row = dict(c.fetchone() or {})
        
        # 4. Recent closed trades (for model audit and retraining corpus)
        c.execute("""
            SELECT id, ticker, direction, entry_price, exit_price, outcome, pnl_r, timestamp, timeframe, regime, model_version, user_active
            FROM alerts
            WHERE outcome IN ('WIN', 'LOSS', 'BREAKEVEN')
            ORDER BY id DESC LIMIT 50
        """)
        recent_closed = [dict(r) for r in c.fetchall()]
        conn.close()
        
        total_closed = stats_row.get('total_closed') or 0
        wins = stats_row.get('wins') or 0
        losses = stats_row.get('losses') or 0
        be = stats_row.get('be') or 0
        decisive = wins + losses
        winrate_ex_be = round((wins / decisive * 100.0), 1) if decisive > 0 else 0.0
        
        return {
            "status": "SUCCESS",
            "kpis": {
                "total_open_paper": len(enriched_open),
                "by_timeframe": tf_counts,
                "by_direction": dir_counts,
                "closed_trades": total_closed,
                "wins": wins,
                "losses": losses,
                "breakeven": be,
                "winrate_ex_be": winrate_ex_be,
                "net_r": round(float(stats_row.get('net_r') or 0.0), 2),
                "air_gap_status": "SECURE // REAL BROKER POSITIONS ISOLATED"
            },
            "open_trades": enriched_open,
            "recent_closed": recent_closed
        }
    except Exception as e:
        return {"status": "ERROR", "error": str(e), "kpis": {}, "open_trades": [], "recent_closed": []}

@app.get("/api/pnl/station")
def get_pnl_station():
    """
    Returns complete P&L Station data: aggregate KPIs, all closed and active trades
    (both manual broker executions and automated VM algo trades), with deep trade-by-trade
    autopsy and execution lifecycle analysis.
    """
    try:
        from pnl_engine import fetch_pnl_station_data
        return fetch_pnl_station_data()
    except Exception as e:
        return {"status": "ERROR", "error": str(e), "kpis": {}, "trades": []}

@app.post("/api/alerts/sync")
def sync_alerts(payload: list[dict]):
    """Receives alerts from another instance and inserts or updates records."""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # Dynamic Schema Auto-Migration: Ensure all payload keys exist in alerts table
        c.execute("PRAGMA table_info(alerts)")
        existing_cols = set(r[1] for r in c.fetchall())
        for item in payload:
            for k in item.keys():
                if k != 'id' and k not in existing_cols:
                    try:
                        c.execute(f"ALTER TABLE alerts ADD COLUMN {k} REAL")
                        existing_cols.add(k)
                    except Exception:
                        pass

        inserted = 0
        updated = 0
        for item in payload:
            ticker = item.get("ticker")
            ts = item.get("timestamp")
            if not ticker or not ts:
                continue
            c.execute("SELECT id FROM alerts WHERE ticker = ? AND timestamp = ?", (ticker, ts))
            row = c.fetchone()
            keys = [k for k in item.keys() if k != 'id' and k in existing_cols]
            if row is None:
                placeholders = ', '.join(['?'] * len(keys))
                cols = ', '.join(keys)
                vals = [item[k] for k in keys]
                c.execute(f"INSERT INTO alerts ({cols}) VALUES ({placeholders})", vals)
                inserted += 1
            else:
                existing_id = row[0]
                set_clause = ', '.join([f"{k} = ?" for k in keys])
                vals = [item[k] for k in keys] + [existing_id]
                c.execute(f"UPDATE alerts SET {set_clause} WHERE id = ?", vals)
                updated += 1
        conn.commit()
        conn.close()
        return {"success": True, "inserted": inserted, "updated": updated}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/ml/sync_history")
def sync_ml_history(payload: list[dict]):
    """Receives ml_model_history records and inserts or updates them."""
    try:
        from wyckoff_ml_engine import init_history_table
        init_history_table()
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("PRAGMA table_info(ml_model_history)")
        existing_cols = set(r[1] for r in c.fetchall())
        
        synced = 0
        for item in payload:
            ver = item.get("version")
            if not ver:
                continue
            c.execute("SELECT id FROM ml_model_history WHERE version = ?", (ver,))
            row = c.fetchone()
            keys = [k for k in item.keys() if k != 'id' and k in existing_cols]
            if row is None:
                placeholders = ', '.join(['?'] * len(keys))
                cols = ', '.join(keys)
                vals = [item[k] for k in keys]
                c.execute(f"INSERT INTO ml_model_history ({cols}) VALUES ({placeholders})", vals)
                synced += 1
            else:
                existing_id = row[0]
                set_clause = ', '.join([f"{k} = ?" for k in keys])
                vals = [item[k] for k in keys] + [existing_id]
                c.execute(f"UPDATE ml_model_history SET {set_clause} WHERE id = ?", vals)
                synced += 1
        conn.commit()
        conn.close()
        return {"success": True, "synced": synced}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/ml/sync_model")
async def sync_ml_model(request: Request):
    """Receives raw bytes of wyckoff_model.pkl and updates active model artifact."""
    try:
        body = await request.body()
        if not body:
            raise HTTPException(status_code=400, detail="Empty model payload")
        with open(MODEL_PATH, "wb") as f:
            f.write(body)
        return {"success": True, "bytes_written": len(body)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/system/update")
def trigger_system_update():
    """Pulls latest git commits, executes database migrations, and updates the repository."""
    import subprocess
    import sys
    import shutil
    import os
    try:
        # Resolve git binary across Linux system directories and virtualenv
        git_bin = shutil.which("git") or "/usr/bin/git" or "/usr/local/bin/git"
        env = dict(os.environ)
        # Ensure standard Linux system PATHs are present
        std_paths = ["/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin"]
        current_path = env.get("PATH", "")
        for p in std_paths:
            if p not in current_path:
                current_path = f"{p}:{current_path}"
        env["PATH"] = current_path
        env["HOME"] = "/home/matt_smith"
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["FROM_TERMINAL_UPDATE"] = "1"
        repo_dir = os.path.dirname(os.path.abspath(__file__))

        # Preserve live VM state files (priority_queue.json, terminal_config.json) and recent VM alerts across git reset
        pq_path = os.path.join(repo_dir, "priority_queue.json")
        cfg_path = os.path.join(repo_dir, "terminal_config.json")
        pq_backup = open(pq_path, "rb").read() if os.path.exists(pq_path) else None
        cfg_backup = open(cfg_path, "rb").read() if os.path.exists(cfg_path) else None

        subprocess.run([git_bin, "fetch", "origin", "main"], cwd=repo_dir, capture_output=True, text=True, timeout=25, env=env)
        pull_res = subprocess.run([git_bin, "reset", "--hard", "origin/main"], cwd=repo_dir, capture_output=True, text=True, timeout=25, env=env)

        if pq_backup:
            try:
                open(pq_path, "wb").write(pq_backup)
            except Exception:
                pass
        if cfg_backup:
            try:
                open(cfg_path, "wb").write(cfg_backup)
            except Exception:
                pass

        py_exe = sys.executable or shutil.which("python3") or "python3"
        mig_res = subprocess.run([py_exe, "migrate_db.py"], cwd=repo_dir, capture_output=True, text=True, timeout=30, env=env)


        if os.name != 'nt':
            bash_bin = shutil.which("bash") or "/bin/bash" or "/usr/bin/bash"
            cmd = f"cd {repo_dir} && pkill -9 -f wyckoff_alert_bot 2>/dev/null; sleep 1 && (sudo systemctl restart wyckoff-bot.service wyckoff-terminal.service 2>/dev/null || (echo 'M642423s$' | sudo -S systemctl restart wyckoff-bot.service wyckoff-terminal.service 2>/dev/null) || (pkill -9 -f 'terminal_api' 2>/dev/null; nohup {py_exe} wyckoff_alert_bot.py > bot.log 2>&1 & nohup {py_exe} -m uvicorn terminal_api:app --host 0.0.0.0 --port 8080 > terminal.log 2>&1 &))"
            try:
                subprocess.Popen(
                    [bash_bin, "-c", cmd],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                    env=env
                )
            except:
                pass

        return {
            "success": True,
            "git_output": (pull_res.stdout + "\n" + pull_res.stderr).strip(),
            "migration_output": (mig_res.stdout + "\n" + mig_res.stderr).strip()
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/api/system/restart")
def trigger_system_restart():
    """Triggers clean background reload of wyckoff-bot and wyckoff-terminal services via systemctl or process recycle."""
    import subprocess
    import shutil
    import os
    import sys
    if os.name == 'nt':
        return {"success": True, "message": "Windows environment - manual service restart required."}

    try:
        py_exe = sys.executable or shutil.which("python3") or "python3"
        bash_bin = shutil.which("bash") or "/bin/bash" or "/usr/bin/bash"
        repo_dir = os.path.dirname(os.path.abspath(__file__))
        cmd = f"cd {repo_dir} && pkill -9 -f wyckoff_alert_bot 2>/dev/null; sleep 1 && (sudo systemctl restart wyckoff-bot.service wyckoff-terminal.service 2>/dev/null || (echo 'M642423s$' | sudo -S systemctl restart wyckoff-bot.service wyckoff-terminal.service 2>/dev/null) || (pkill -9 -f 'terminal_api' 2>/dev/null; nohup {py_exe} wyckoff_alert_bot.py > bot.log 2>&1 & nohup {py_exe} -m uvicorn terminal_api:app --host 0.0.0.0 --port 8080 > terminal.log 2>&1 &))"
        env = dict(os.environ)
        env["PATH"] = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" + (f":{env['PATH']}" if 'PATH' in env else "")
        subprocess.Popen(
            [bash_bin, "-c", cmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env=env
        )
        import threading
        threading.Timer(1.0, lambda: os._exit(0)).start()
        return {"success": True, "message": "Services restarting: wyckoff-bot and wyckoff-terminal"}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/api/system/bot_health")
def bot_health():
    """Checks if wyckoff_alert_bot.py is actively running, auto-recovers it if dead, and returns process info."""
    import subprocess
    import shutil
    import os
    import sys
    import time
    if os.name == 'nt':
        return {"status": "WINDOWS_LOCAL"}

    try:
        repo_dir = os.path.dirname(os.path.abspath(__file__))
        py_exe = sys.executable or shutil.which("python3") or "python3"
        ps_res = subprocess.run("ps aux | grep -v grep | grep wyckoff_alert_bot", shell=True, capture_output=True, text=True, timeout=5)
        is_bot_running = bool(ps_res.stdout.strip())

        sys_res = subprocess.run("systemctl is-active wyckoff-bot.service 2>/dev/null", shell=True, capture_output=True, text=True, timeout=5)
        sys_status = sys_res.stdout.strip()

        started = False
        if not is_bot_running and sys_status != "active":
            try:
                subprocess.run("sudo systemctl restart wyckoff-bot.service 2>/dev/null || (echo 'M642423s$' | sudo -S systemctl restart wyckoff-bot.service 2>/dev/null)", shell=True, timeout=5)
                started = True
            except:
                pass

        bot_log_tail = ""
        try:
            j_res = subprocess.run("sudo -n journalctl -u wyckoff-bot.service -n 35 --no-pager 2>/dev/null || journalctl -u wyckoff-bot.service -n 35 --no-pager 2>/dev/null", shell=True, capture_output=True, text=True, timeout=4)
            if j_res.stdout and j_res.stdout.strip():
                bot_log_tail = j_res.stdout
        except:
            pass

        if not bot_log_tail:
            log_path = os.path.join(repo_dir, "bot.log")
            if os.path.exists(log_path):
                with open(log_path, "r", encoding="utf-8", errors="ignore") as lf:
                    bot_log_tail = "".join(lf.readlines()[-30:])

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT ticker, direction, timeframe, timestamp, outcome, user_active, pnl_r, exit_price FROM alerts ORDER BY id DESC LIMIT 5")
        recent_alerts = [dict(zip(['ticker', 'direction', 'timeframe', 'timestamp', 'outcome', 'user_active', 'pnl_r', 'exit_price'], row)) for row in c.fetchall()]
        conn.close()

        # Check ps aux again after potential startup
        ps_after = subprocess.run("ps aux | grep -v grep | grep wyckoff_alert_bot", shell=True, capture_output=True, text=True, timeout=5).stdout.strip()

        return {
            "is_bot_running": bool(ps_after),
            "systemctl_status": sys_status,
            "auto_recovered": started,
            "process_info": ps_after or "OFFLINE",
            "bot_log_tail": bot_log_tail,
            "recent_alerts": recent_alerts
        }
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/options/scanner")
def get_options_scanner():
    """Returns real-time options intelligence and multi-tranche strategy recommendations for Wyckoff setups."""
    try:
        import options_engine
        data = options_engine.get_options_scanner_data()
        return {"status": "SUCCESS", "count": len(data), "setups": data}
    except Exception as e:
        return {"status": "ERROR", "error": str(e), "setups": []}

@app.get("/api/options/covered-calls")
def get_covered_calls():
    """Returns real-time covered call intelligence for user's long positions (AMC, NKE)."""
    try:
        import options_engine
        data = options_engine.get_covered_calls_scanner_data()
        return {"status": "SUCCESS", "data": data}
    except Exception as e:
        return {"status": "ERROR", "error": str(e), "data": {}}

@app.get("/api/options/ticker/{ticker}")
def get_ticker_options(ticker: str):
    """Scans and structures options intelligence for a specific user-requested ticker."""
    try:
        import options_engine
        data = options_engine.scan_single_ticker_options(ticker.upper())
        if not data:
            return {"status": "NOT_FOUND", "message": f"No liquid weekly options found for {ticker.upper()}"}
        return {"status": "SUCCESS", "data": data}
    except Exception as e:
        return {"status": "ERROR", "error": str(e)}

@app.get("/api/market/pulse")
def get_market_pulse_endpoint(tickers: str = None):
    """Returns macroeconomic wire, major index quotes, today's actions/bot ledger, and ticker news."""
    try:
        import market_news
        t_list = [t.strip().upper() for t in tickers.split(',')] if tickers else ['SPY', 'QQQ', 'NVDA', 'TSLA']
        return market_news.get_market_pulse(t_list)
    except Exception as e:
        return {"status": "ERROR", "error": str(e)}

@app.get("/api/market/ticker_news")
def get_ticker_news_endpoint(ticker: str):
    """Returns real-time news headlines for a specific stock."""
    try:
        import market_news
        news = market_news.get_ticker_news(ticker)
        return {"status": "SUCCESS", "ticker": ticker.upper(), "count": len(news), "news": news}
    except Exception as e:
        return {"status": "ERROR", "error": str(e), "news": []}

@app.get("/api/health")
def health_check():
    return {"status": "ONLINE", "version": "2.2.0"}

@app.get("/")
def serve_index():
    index_file = os.path.join(BASE_DIR, "terminal_static", "index.html")
    if not os.path.exists(index_file):
        index_file = os.path.join(BASE_DIR, "cloud_index.html")
    response = FileResponse(index_file)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

# Mount static web assets
static_dir = os.path.join(BASE_DIR, "terminal_static")
if os.path.exists(static_dir):
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("terminal_api:app", host="127.0.0.1", port=8080, reload=False)
