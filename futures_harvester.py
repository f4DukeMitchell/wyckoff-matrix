"""
Futures Data Harvester & Shadow Telemetry Engine
-------------------------------------------------
Collects continuous all-day futures market data (/ES, /NQ, /RTY, ^VIX):
- Computes Intraday VWAP & % distance
- Extracts Overnight High (ONH) and Low (ONL)
- Stores real-time snapshot in futures_market_pulse.json for instant UI/API access
- Records historical time-series rows in wyckoff_trades.db (futures_telemetry table)
  WITHOUT altering or blocking any live trade execution.
"""
import os
import json
import time
import datetime
import threading
import sqlite3
import pandas as pd
import yfinance as yf

from data_paths import DATA_DIR, DB_PATH

FUTURES_PULSE_PATH = os.path.join(DATA_DIR, "futures_market_pulse.json")
_HARVESTER_THREAD = None
_RUNNING = False

def init_futures_db():
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS futures_telemetry (
                timestamp TEXT PRIMARY KEY,
                session_type TEXT,
                es_price REAL,
                es_vwap REAL,
                es_pct_vwap REAL,
                es_onh REAL,
                es_onl REAL,
                es_change_pct REAL,
                nq_price REAL,
                nq_vwap REAL,
                nq_pct_vwap REAL,
                nq_onh REAL,
                nq_onl REAL,
                nq_change_pct REAL,
                rty_price REAL,
                rty_vwap REAL,
                rty_pct_vwap REAL,
                vix_price REAL,
                vix_change_pct REAL
            )
        ''')
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[futures_harvester] Error initializing DB: {e}")

def get_session_type(now_dt=None):
    if now_dt is None:
        now_dt = datetime.datetime.now()
    t = now_dt.time()
    if datetime.time(9, 30) <= t <= datetime.time(16, 0):
        return "REGULAR"
    elif datetime.time(4, 0) <= t < datetime.time(9, 30):
        return "PRE_MARKET"
    elif datetime.time(16, 0) < t <= datetime.time(20, 0):
        return "POST_MARKET"
    else:
        return "OVERNIGHT"

def harvest_futures_once():
    """Polls yfinance for ES=F, NQ=F, RTY=F, ^VIX and computes intraday stats."""
    now_dt = datetime.datetime.now()
    stamp = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    session_type = get_session_type(now_dt)

    try:
        tickers = ['ES=F', 'NQ=F', 'RTY=F', '^VIX']
        df = yf.download(tickers, period='1d', interval='5m', progress=False)
        
        if df is None or df.empty:
            return None

        result = {
            "timestamp": stamp,
            "session_type": session_type,
            "updated_at_epoch": time.time(),
            "es": {},
            "nq": {},
            "rty": {},
            "vix": {}
        }

        def process_sym(sym):
            try:
                sub = df.xs(sym, level=1, axis=1).dropna(how='all')
                if sub.empty:
                    return None
                last_row = sub.iloc[-1]
                close = float(last_row['Close'])
                prev_close = float(sub.iloc[0]['Open'])
                
                # VWAP calculation
                typ = (sub['High'] + sub['Low'] + sub['Close']) / 3.0
                cum_pv = (typ * sub['Volume']).cumsum()
                cum_v = sub['Volume'].cumsum()
                vwap = float(cum_pv.iloc[-1] / cum_v.iloc[-1]) if cum_v.iloc[-1] > 0 else close
                pct_vwap = ((close - vwap) / vwap) * 100.0 if vwap > 0 else 0.0

                onh = float(sub['High'].max())
                onl = float(sub['Low'].min())
                chg_pct = ((close - prev_close) / prev_close) * 100.0 if prev_close > 0 else 0.0

                return {
                    "price": round(close, 2),
                    "vwap": round(vwap, 2),
                    "pct_vwap": round(pct_vwap, 2),
                    "high": round(float(sub['High'].max()), 2),
                    "low": round(float(sub['Low'].min()), 2),
                    "onh": round(onh, 2),
                    "onl": round(onl, 2),
                    "change_pct": round(chg_pct, 2)
                }
            except Exception:
                return None

        es_data = process_sym('ES=F')
        nq_data = process_sym('NQ=F')
        rty_data = process_sym('RTY=F')
        
        vix_data = None
        try:
            v_sub = df.xs('^VIX', level=1, axis=1).dropna(how='all')
            if not v_sub.empty:
                v_close = float(v_sub.iloc[-1]['Close'])
                v_open = float(v_sub.iloc[0]['Open'])
                v_chg = ((v_close - v_open) / v_open) * 100.0 if v_open > 0 else 0.0
                vix_data = {
                    "price": round(v_close, 2),
                    "change_pct": round(v_chg, 2)
                }
        except Exception:
            pass

        result["es"] = es_data or {}
        result["nq"] = nq_data or {}
        result["rty"] = rty_data or {}
        result["vix"] = vix_data or {}

        # Save to atomic JSON cache for instant UI/API access
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp_path = FUTURES_PULSE_PATH + f".tmp{os.getpid()}"
        with open(tmp_path, "w") as f:
            json.dump(result, f, indent=2)
        os.replace(tmp_path, FUTURES_PULSE_PATH)

        # Record to SQLite telemetry table
        conn = sqlite3.connect(DB_PATH, timeout=10)
        c = conn.cursor()
        c.execute('''
            INSERT OR REPLACE INTO futures_telemetry (
                timestamp, session_type,
                es_price, es_vwap, es_pct_vwap, es_onh, es_onl, es_change_pct,
                nq_price, nq_vwap, nq_pct_vwap, nq_onh, nq_onl, nq_change_pct,
                rty_price, rty_vwap, rty_pct_vwap,
                vix_price, vix_change_pct
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            stamp, session_type,
            (es_data or {}).get("price"), (es_data or {}).get("vwap"), (es_data or {}).get("pct_vwap"),
            (es_data or {}).get("onh"), (es_data or {}).get("onl"), (es_data or {}).get("change_pct"),
            (nq_data or {}).get("price"), (nq_data or {}).get("vwap"), (nq_data or {}).get("pct_vwap"),
            (nq_data or {}).get("onh"), (nq_data or {}).get("onl"), (nq_data or {}).get("change_pct"),
            (rty_data or {}).get("price"), (rty_data or {}).get("vwap"), (rty_data or {}).get("pct_vwap"),
            (vix_data or {}).get("price"), (vix_data or {}).get("change_pct")
        ))
        conn.commit()
        conn.close()

        return result

    except Exception as e:
        print(f"[futures_harvester] Harvest error: {e}")
        return None

def get_latest_futures_pulse():
    """Reads the cached pulse in < 1ms without blocking."""
    if os.path.exists(FUTURES_PULSE_PATH):
        try:
            with open(FUTURES_PULSE_PATH, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return None

def _harvester_loop():
    global _RUNNING
    print("[futures_harvester] Daemon loop started. Collecting shadow futures data...")
    init_futures_db()
    while _RUNNING:
        try:
            harvest_futures_once()
        except Exception as e:
            print(f"[futures_harvester] Error in cycle: {e}")
        # Poll every 45 seconds during regular market hours, 90 seconds otherwise
        now_t = datetime.datetime.now().time()
        sleep_dur = 45 if datetime.time(9, 15) <= now_t <= datetime.time(16, 15) else 90
        for _ in range(sleep_dur):
            if not _RUNNING:
                break
            time.sleep(1)

def start_futures_harvester_daemon():
    global _HARVESTER_THREAD, _RUNNING
    if _HARVESTER_THREAD is not None and _HARVESTER_THREAD.is_alive():
        return
    _RUNNING = True
    _HARVESTER_THREAD = threading.Thread(target=_harvester_loop, daemon=True, name="FuturesHarvester")
    _HARVESTER_THREAD.start()
    print("[futures_harvester] Background daemon thread successfully launched.")

if __name__ == '__main__':
    start_futures_harvester_daemon()
    time.sleep(3)
