import os
import json
import sqlite3
import datetime
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, status, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
import secrets

from public_executor import (
    get_account_capital_summary, calculate_test_allocation, execute_dollar_buy,
    execute_short_sell, execute_exit_sell, get_live_prices, get_broker_portfolio_positions
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

DB_PATH = "wyckoff_trades.db"

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
        SELECT id, ticker, direction, entry_price, stop_loss, take_profit, regime, timeframe, timestamp, user_active, outcome, pnl_r, breakeven_set
        FROM alerts
        WHERE outcome = 'OPEN' AND user_active = 1
        ORDER BY id DESC
    """)
    raw_positions = [dict(r) for r in c.fetchall()]
    active_tickers = list(set([p['ticker'] for p in raw_positions if p.get('ticker')]))
    live_prices = get_live_prices(active_tickers) if active_tickers else {}
    
    active_positions = []
    tracked_tickers = set()
    for pos in raw_positions:
        ticker = pos.get('ticker')
        tracked_tickers.add(ticker.upper())
        entry = float(pos.get('entry_price') or 0.0)
        sl = float(pos.get('stop_loss') or 0.0)
        is_long = (pos.get('direction') or 'LONG').upper() == 'LONG'
        curr_price = live_prices.get(ticker)
        
        pos['current_price'] = curr_price
        if curr_price and entry > 0:
            pnl_pct = ((curr_price - entry) / entry * 100.0) if is_long else ((entry - curr_price) / entry * 100.0)
            risk = abs(entry - sl) if sl > 0 else 0.0
            r_mult = ((curr_price - entry) / risk) if (is_long and risk > 0) else (((entry - curr_price) / risk) if risk > 0 else 0.0)
            pos['unrealized_pnl_pct'] = round(pnl_pct, 2)
            pos['unrealized_r'] = round(r_mult, 2)
        else:
            pos['unrealized_pnl_pct'] = 0.0
            pos['unrealized_r'] = 0.0
        active_positions.append(pos)

    # Ingest ALL live broker holdings from Public.com (even if not initiated by algo)
    broker_holdings = get_broker_portfolio_positions()
    for bh in broker_holdings:
        sym = bh['ticker'].upper()
        if sym not in tracked_tickers:
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
                'unrealized_r': None,
                'quantity': bh['quantity'],
                'market_value': bh['market_value'],
                'is_broker_native': True
            })
    
    # Recent scanner signals (distinct latest setup per ticker & timeframe)
    c.execute("""
        SELECT a.id, a.ticker, a.direction, a.entry_price, a.stop_loss, a.take_profit, 
               a.regime, a.timeframe, a.timestamp, a.outcome, a.model_version, a.user_active, a.ml_confidence
        FROM alerts a
        INNER JOIN (
            SELECT ticker, timeframe, MAX(id) as max_id
            FROM alerts
            WHERE outcome = 'OPEN'
            GROUP BY ticker, timeframe
        ) latest ON a.id = latest.max_id
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
    conn.close()
    
    return {
        "status": "ONLINE",
        "timestamp": datetime.datetime.now().strftime("%H:%M:%S"),
        "capital": cap,
        "test_allocation_1pct": calculate_test_allocation(0.01),
        "positions": active_positions,
        "scanner": scanner_results,
        "history": history
    }

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

        # 5. Detect Spring and UTAD markers
        if idx >= 1 and not np.isnan(range_low[idx]) and not np.isnan(range_high[idx]):
            c_below = (lows[idx] < range_low[idx]) or (lows[idx-1] < range_low[idx-1])
            c_above = (highs[idx] > range_high[idx]) or (highs[idx-1] > range_high[idx-1])
            vol_dry = rel_vol[idx] < 1.2
            
            is_spring = c_below and u1[idx] and not u1[idx-1] and not u9[idx] and vol_dry
            is_utad = c_above and not u1[idx] and u1[idx-1] and u9[idx] and vol_dry

            if is_spring:
                markers.append({
                    "time": t_sec,
                    "position": "belowBar",
                    "color": "#00d26a",
                    "shape": "arrowUp",
                    "text": "SPRING (LONG)"
                })
            elif is_utad:
                markers.append({
                    "time": t_sec,
                    "position": "aboveBar",
                    "color": "#ff3b30",
                    "shape": "arrowDown",
                    "text": "UTAD (SHORT)"
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
            "vol_exhaustion": is_dry,
            "vol_ratio": round(curr_rv, 2),
            "reclaimed_level": reclaimed,
            "shield_passed": True,
            "slot_ready": True
        }
    }

@app.get("/api/flow/{ticker}")
def get_flow(ticker: str):
    """Fetches real-time institutional options flow & quote microstructure."""
    flow = get_options_flow(ticker.upper())
    quote = get_public_quotes(ticker.upper())
    return {
        "ticker": ticker.upper(),
        "flow": flow,
        "quote": quote
    }

class OrderPayload(BaseModel):
    ticker: str
    amount: float
    trade_id: int | None = None

@app.post("/api/order/buy")
def place_buy(order: OrderPayload):
    """Executes fractional dollar buy on Public.com directly from the terminal."""
    try:
        res = execute_dollar_buy(order.ticker.upper(), order.amount)
        if order.trade_id:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("UPDATE alerts SET user_active = 1 WHERE id = ?", (order.trade_id,))
            conn.commit()
            conn.close()
        return {"success": True, "order": res}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/order/short")
def place_short(order: OrderPayload):
    """Executes integer whole-share short sale on Public.com directly from the terminal."""
    try:
        res = execute_short_sell(order.ticker.upper(), order.amount)
        if order.trade_id:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("UPDATE alerts SET user_active = 1 WHERE id = ?", (order.trade_id,))
            conn.commit()
            conn.close()
        return {"success": True, "order": res}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

class ExitPayload(BaseModel):
    ticker: str
    trade_id: int | None = None

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
        WHERE outcome = 'BREAKEVEN'
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
        FROM alerts WHERE outcome = 'BREAKEVEN'
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
@app.post("/api/alerts/sync")
def sync_alerts(payload: list[dict]):
    """Receives alerts from another instance and inserts any missing records."""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        inserted = 0
        for item in payload:
            ticker = item.get("ticker")
            ts = item.get("timestamp")
            if not ticker or not ts:
                continue
            c.execute("SELECT id FROM alerts WHERE ticker = ? AND timestamp = ?", (ticker, ts))
            if c.fetchone() is None:
                keys = [k for k in item.keys() if k != 'id']
                placeholders = ', '.join(['?'] * len(keys))
                cols = ', '.join(keys)
                vals = [item[k] for k in keys]
                c.execute(f"INSERT INTO alerts ({cols}) VALUES ({placeholders})", vals)
                inserted += 1
        conn.commit()
        conn.close()
        return {"success": True, "inserted": inserted}
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

        pull_res = subprocess.run([git_bin, "pull", "origin", "main"], capture_output=True, text=True, timeout=30, env=env)
        py_exe = sys.executable or shutil.which("python3") or "python3"
        mig_res = subprocess.run([py_exe, "migrate_db.py"], capture_output=True, text=True, timeout=30, env=env)

        if os.name != 'nt':
            cmd = "sleep 1 && sudo systemctl restart wyckoff-bot.service && sudo systemctl restart wyckoff-terminal.service"
            try:
                subprocess.Popen(
                    ["bash", "-c", cmd],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True
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
    """Triggers clean background reload of wyckoff-bot and wyckoff-terminal services via systemctl."""
    import subprocess
    import os
    if os.name == 'nt':
        return {"success": True, "message": "Windows environment - manual service restart required."}

    try:
        cmd = "sleep 1 && sudo systemctl restart wyckoff-bot.service && sudo systemctl restart wyckoff-terminal.service"
        subprocess.Popen(
            ["bash", "-c", cmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True
        )
        return {"success": True, "message": "Services restarting: wyckoff-bot and wyckoff-terminal"}
    except Exception as e:
        return {"success": False, "error": str(e)}

# Mount static web assets
app.mount("/", StaticFiles(directory="terminal_static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("terminal_api:app", host="127.0.0.1", port=8080, reload=False)
