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
    
    # Recent scanner signals (all OPEN setups)
    c.execute("""
        SELECT id, ticker, direction, entry_price, stop_loss, take_profit, regime, timeframe, timestamp, outcome, model_version, user_active
        FROM alerts
        WHERE outcome = 'OPEN'
        ORDER BY id DESC LIMIT 50
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
    """Fetches clean OHLCV bars directly from Public.com broker feed."""
    df = get_public_bars_sync(ticker.upper(), interval)
    if df.empty:
        return {"ticker": ticker.upper(), "bars": []}
        
    bars = []
    for idx, row in df.iterrows():
        bars.append({
            "time": int(idx.timestamp()),
            "open": float(row['Open']),
            "high": float(row['High']),
            "low": float(row['Low']),
            "close": float(row['Close']),
            "volume": float(row['Volume'])
        })
    return {"ticker": ticker.upper(), "interval": interval, "bars": bars}

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
