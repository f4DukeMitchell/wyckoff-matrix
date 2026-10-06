import os
import json
import sqlite3
import datetime
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

from public_executor import get_account_capital_summary, calculate_test_allocation, execute_dollar_buy, execute_exit_sell
from data_feed_public import get_public_bars_sync
from options_flow import get_options_flow, get_public_quotes

app = FastAPI(title="WSR Terminal Engine")

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
    
    # Active user trades
    c.execute("""
        SELECT id, ticker, direction, entry_price, stop_loss, take_profit, regime, timeframe, timestamp, user_active, outcome, pnl_r, breakeven_set
        FROM alerts
        WHERE outcome = 'OPEN' AND user_active = 1
        ORDER BY id DESC
    """)
    active_positions = [dict(r) for r in c.fetchall()]
    
    # Recent scanner signals (distinct latest setup per ticker & timeframe)
    c.execute("""
        SELECT a.id, a.ticker, a.direction, a.entry_price, a.stop_loss, a.take_profit, 
               a.regime, a.timeframe, a.timestamp, a.outcome, a.model_version, a.user_active
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
    u1, line1 = get_supertrend(highs, lows, closes, 1, 1.0)
    u9, line9 = get_supertrend(highs, lows, closes, 9, 9.0)

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

    return {
        "ticker": ticker.upper(),
        "interval": interval,
        "bars": bars,
        "volume": volume_bars,
        "supertrend": st_line,
        "range_high": range_h_line,
        "range_low": range_l_line,
        "vwap": vwap_line,
        "markers": markers
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

class ExitPayload(BaseModel):
    ticker: str
    trade_id: int | None = None

@app.post("/api/order/exit")
def place_exit(exit_req: ExitPayload):
    """Executes full position exit on Public.com directly from the terminal."""
    try:
        res = execute_exit_sell(exit_req.ticker.upper())
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

# Mount static web assets
app.mount("/", StaticFiles(directory="terminal_static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("terminal_api:app", host="127.0.0.1", port=8080, reload=False)
