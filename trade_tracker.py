import sqlite3
import yfinance as yf
import datetime
import os
import pandas as pd

DB_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(DB_DIR, "wyckoff_trades.db")

def init_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT,
                direction TEXT,
                entry_price REAL,
                stop_loss REAL,
                take_profit REAL,
                timeframe TEXT,
                regime TEXT,
                timestamp TEXT,
                outcome TEXT DEFAULT 'OPEN',
                exit_price REAL DEFAULT NULL,
                pnl_r REAL DEFAULT NULL
            )
        ''')
        # Institutional feature columns (ML toolbelt)
        new_cols = [
            ("pcr", "REAL"),
            ("sentiment", "TEXT"),
            ("bars_in_regime", "INTEGER DEFAULT 0"),
            ("vwap_distance", "REAL"),
            ("hour_of_day", "REAL"),
            ("spy_bullish", "INTEGER"),
            ("atr_expansion", "REAL"),
            ("bid_ask_ratio", "REAL"),
            ("spread_width_pct", "REAL"),
            ("implied_volatility", "REAL"),
            ("user_active", "INTEGER DEFAULT 0"),
            ("telegram_alerted", "INTEGER DEFAULT 0"),
            ("breakeven_set", "INTEGER DEFAULT 0"),
            ("model_version", "TEXT DEFAULT 'v1.0'"),
        ]
        for col_name, col_type in new_cols:
            try: cursor.execute(f"ALTER TABLE alerts ADD COLUMN {col_name} {col_type}")
            except: pass
        conn.commit()
    except Exception as e:
        print(f"Error initializing database: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

def get_active_model_version():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT version FROM ml_model_history WHERE status = 'ACTIVE' ORDER BY id DESC LIMIT 1")
        row = c.fetchone()
        conn.close()
        return row[0] if row else 'v1.1'
    except:
        return 'v1.1'

def log_alert(ticker, direction, entry_price, stop_loss, take_profit, regime,
              timeframe='5m', pcr=None, sentiment=None, bars_in_regime=0,
              vwap_distance=None, hour_of_day=None, spy_bullish=None, atr_expansion=None,
              bid_ask_ratio=None, spread_width_pct=None, implied_volatility=None, model_version=None):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        timestamp = datetime.datetime.now().isoformat()
        active_version = model_version or get_active_model_version()
        cursor.execute('''
            INSERT INTO alerts (ticker, direction, entry_price, stop_loss, take_profit, regime,
                                timestamp, pcr, sentiment, timeframe, bars_in_regime,
                                vwap_distance, hour_of_day, spy_bullish, atr_expansion,
                                bid_ask_ratio, spread_width_pct, implied_volatility, model_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (ticker, direction, entry_price, stop_loss, take_profit, regime,
              timestamp, pcr, sentiment, timeframe, bars_in_regime,
              vwap_distance, hour_of_day, 1 if spy_bullish else 0 if spy_bullish is not None else None,
              atr_expansion, bid_ask_ratio, spread_width_pct, implied_volatility, active_version))
        conn.commit()
        last_id = cursor.lastrowid
        return last_id
    except Exception as e:
        print(f"Error logging alert: {e}")
        return None
    finally:
        if 'conn' in locals():
            conn.close()

def get_latest_price(ticker):
    try:
        ticker_obj = yf.Ticker(ticker)
        data = ticker_obj.history(period="1d")
        if not data.empty:
            return data['Close'].iloc[-1]
    except Exception as e:
        print(f"Error fetching price for {ticker}: {e}")
    return None

def check_open_trades():
    closed_trades = []
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM alerts WHERE outcome = 'OPEN'")
        open_trades = cursor.fetchall()
        if not open_trades:
            return closed_trades
            
        unique_tickers = list(set(trade['ticker'] for trade in open_trades))
        prices = {}
        try:
            data = yf.download(unique_tickers, period="1d", interval="1m", progress=False)
            if not data.empty and 'Close' in data:
                latest = data['Close'].iloc[-1]
                for sym in unique_tickers:
                    if sym in latest and not pd.isna(latest[sym]):
                        prices[sym] = float(latest[sym])
        except Exception as e:
            print(f"Batch price download error: {e}")

        for trade in open_trades:
            trade_id = trade['id']
            ticker = trade['ticker']
            direction = trade['direction'].upper()
            entry_price = trade['entry_price']
            stop_loss = trade['stop_loss']
            take_profit = trade['take_profit']
            
            price = prices.get(ticker) or get_latest_price(ticker)
            if price is None:
                continue
                
            outcome = 'OPEN'
            exit_price = None
            pnl_r = None
            
            if direction == 'LONG':
                if price >= take_profit:
                    outcome = 'WIN'
                    exit_price = price
                elif price <= stop_loss:
                    outcome = 'LOSS'
                    exit_price = price
            elif direction == 'SHORT':
                if price <= take_profit:
                    outcome = 'WIN'
                    exit_price = price
                elif price >= stop_loss:
                    outcome = 'LOSS'
                    exit_price = price

            # --- Feature 1: Breakeven Stop Ratchet (+0.75R) ---
            if outcome == 'OPEN':
                b_set = trade['breakeven_set'] if 'breakeven_set' in trade.keys() else 0
                if not b_set:
                    init_risk = abs(entry_price - stop_loss)
                    curr_gain = (price - entry_price) if direction == 'LONG' else (entry_price - price)
                    curr_r = (curr_gain / init_risk) if init_risk > 0 else 0
                    if curr_r >= 0.75:
                        cursor.execute("UPDATE alerts SET stop_loss = ?, breakeven_set = 1 WHERE id = ?", (entry_price, trade_id))
                        conn.commit()
                        closed_trades.append({
                            'id': trade_id,
                            'ticker': ticker,
                            'direction': direction,
                            'entry_price': entry_price,
                            'current_r': curr_r,
                            'is_breakeven': True,
                            'outcome': 'BREAKEVEN_SET',
                            'user_active': trade['user_active'] if 'user_active' in trade.keys() else 0,
                            'telegram_alerted': trade['telegram_alerted'] if 'telegram_alerted' in trade.keys() else 0,
                            'timeframe': trade['timeframe'] if 'timeframe' in trade.keys() else '5m'
                        })
                    
            if outcome != 'OPEN':
                b_set = trade['breakeven_set'] if 'breakeven_set' in trade.keys() else 0
                if b_set and outcome == 'LOSS':
                    outcome = 'BREAKEVEN'
                    pnl_r = 0.0
                elif direction == 'LONG':
                    risk = entry_price - stop_loss
                    pnl_r = (exit_price - entry_price) / risk if risk != 0 else 0
                else:
                    risk = stop_loss - entry_price
                    pnl_r = (entry_price - exit_price) / risk if risk != 0 else 0
                    
                cursor.execute('''
                    UPDATE alerts
                    SET outcome = ?, exit_price = ?, pnl_r = ?
                    WHERE id = ?
                ''', (outcome, exit_price, pnl_r, trade_id))
                conn.commit()
                
                closed_trades.append({
                    'id': trade_id,
                    'ticker': ticker,
                    'direction': direction,
                    'entry_price': entry_price,
                    'exit_price': exit_price,
                    'outcome': outcome,
                    'pnl_r': pnl_r,
                    'user_active': trade.get('user_active', 0),
                    'telegram_alerted': trade.get('telegram_alerted', 0),
                    'timeframe': trade.get('timeframe', '5m')
                })
                
    except Exception as e:
        print(f"Error checking open trades: {e}")
    finally:
        if 'conn' in locals():
            conn.close()
            
    if closed_trades:
        has_exit = any(t.get('outcome') in ['WIN', 'LOSS', 'BREAKEVEN'] for t in closed_trades)
        if has_exit:
            update_realized_version_stats()
            
    return closed_trades

def update_realized_version_stats():
    """Recalculate realized out-of-sample metrics for model versions in ml_model_history."""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            SELECT model_version, 
                   COUNT(*) as total_trades,
                   SUM(CASE WHEN outcome = 'WIN' THEN 1 ELSE 0 END) as wins,
                   SUM(pnl_r) as net_r,
                   AVG(pnl_r) as avg_r
            FROM alerts
            WHERE outcome IN ('WIN', 'LOSS', 'BREAKEVEN')
            GROUP BY model_version
        """)
        rows = c.fetchall()
        for version, total, wins, net_r, avg_r in rows:
            if not version:
                continue
            wr = (wins / total * 100.0) if total > 0 else 0.0
            net_r = net_r if net_r is not None else 0.0
            avg_r = avg_r if avg_r is not None else 0.0
            c.execute("""
                UPDATE ml_model_history
                SET realized_trades = ?, realized_win_rate = ?, realized_net_r = ?, realized_avg_r = ?
                WHERE version = ?
            """, (total, round(wr, 1), round(net_r, 2), round(avg_r, 2), version))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error updating realized version stats: {e}")

def get_model_version_stats():
    """Returns list of version history records."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM ml_model_history ORDER BY id DESC")
        rows = c.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"Error fetching model version stats: {e}")
        return []

def get_stats(model_version=None):
    stats = {
        'total_trades': 0,
        'wins': 0,
        'losses': 0,
        'open_count': 0,
        'win_rate': 0.0,
        'avg_pnl_r': 0.0,
        'net_pnl_r': 0.0,
        'best_trade': None,
        'worst_trade': None
    }
    
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        where_v = " WHERE model_version = ?" if model_version else ""
        params = (model_version,) if model_version else ()
        
        cursor.execute(f"SELECT COUNT(*) as count FROM alerts{where_v}", params)
        stats['total_trades'] = cursor.fetchone()['count']
        
        w_clause = f"WHERE outcome = 'WIN' AND model_version = ?" if model_version else "WHERE outcome = 'WIN'"
        cursor.execute(f"SELECT COUNT(*) as count FROM alerts {w_clause}", params)
        stats['wins'] = cursor.fetchone()['count']
        
        l_clause = f"WHERE outcome = 'LOSS' AND model_version = ?" if model_version else "WHERE outcome = 'LOSS'"
        cursor.execute(f"SELECT COUNT(*) as count FROM alerts {l_clause}", params)
        stats['losses'] = cursor.fetchone()['count']
        
        o_clause = f"WHERE outcome = 'OPEN' AND model_version = ?" if model_version else "WHERE outcome = 'OPEN'"
        cursor.execute(f"SELECT COUNT(*) as count FROM alerts {o_clause}", params)
        stats['open_count'] = cursor.fetchone()['count']
        
        closed_count = stats['wins'] + stats['losses']
        if closed_count > 0:
            stats['win_rate'] = (stats['wins'] / closed_count) * 100
            
            pnl_clause = f"WHERE pnl_r IS NOT NULL AND model_version = ?" if model_version else "WHERE pnl_r IS NOT NULL"
            cursor.execute(f"SELECT AVG(pnl_r) as avg_pnl, SUM(pnl_r) as net_pnl FROM alerts {pnl_clause}", params)
            pnl_row = cursor.fetchone()
            if pnl_row['avg_pnl'] is not None:
                stats['avg_pnl_r'] = pnl_row['avg_pnl']
            if pnl_row['net_pnl'] is not None:
                stats['net_pnl_r'] = pnl_row['net_pnl']
                
            cursor.execute(f"SELECT ticker, pnl_r FROM alerts {pnl_clause} ORDER BY pnl_r DESC LIMIT 1", params)
            best_row = cursor.fetchone()
            if best_row:
                stats['best_trade'] = f"{best_row['ticker']} ({best_row['pnl_r']:.2f}R)"
                
            cursor.execute(f"SELECT ticker, pnl_r FROM alerts {pnl_clause} ORDER BY pnl_r ASC LIMIT 1", params)
            worst_row = cursor.fetchone()
            if worst_row:
                stats['worst_trade'] = f"{worst_row['ticker']} ({worst_row['pnl_r']:.2f}R)"
                
    except Exception as e:
        print(f"Error getting stats: {e}")
    finally:
        if 'conn' in locals():
            conn.close()
            
    return stats

def get_recent_trades(n=20):
    trades = []
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (n,))
        rows = cursor.fetchall()
        for row in rows:
            trades.append(dict(row))
    except Exception as e:
        print(f"Error getting recent trades: {e}")
    finally:
        if 'conn' in locals():
            conn.close()
    return trades

def has_open_alerted_trade(ticker, timeframe=None):
    """
    Feature 2: Duplicate Lock.
    Returns True if this ticker already has an active OPEN alert on Telegram.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        if timeframe:
            c.execute("SELECT COUNT(*) FROM alerts WHERE ticker = ? AND timeframe = ? AND outcome = 'OPEN' AND telegram_alerted = 1", (ticker, timeframe))
        else:
            c.execute("SELECT COUNT(*) FROM alerts WHERE ticker = ? AND outcome = 'OPEN' AND telegram_alerted = 1", (ticker,))
        cnt = c.fetchone()[0]
        conn.close()
        return cnt > 0
    except:
        return False

def has_open_trade(ticker, timeframe=None):
    """
    Checks if any active OPEN record exists for this ticker and timeframe.
    Prevents background loop from repeatedly inserting duplicate candidates.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        if timeframe:
            c.execute("SELECT COUNT(*) FROM alerts WHERE ticker = ? AND timeframe = ? AND outcome = 'OPEN'", (ticker, timeframe))
        else:
            c.execute("SELECT COUNT(*) FROM alerts WHERE ticker = ? AND outcome = 'OPEN'", (ticker,))
        cnt = c.fetchone()[0]
        conn.close()
        return cnt > 0
    except:
        return False

def mark_trade_alerted(trade_id):
    """
    Marks that a trade was broadcast to Telegram.
    """
    if not trade_id: return
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE alerts SET telegram_alerted = 1 WHERE id = ?", (trade_id,))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error marking trade {trade_id} as alerted: {e}")

def sync_public_positions():
    """
    Feature 3: Auto-Sync with Public.com Portfolio.
    Automatically marks user_active = 1 for any OPEN alert currently held in Public account.
    """
    api_key = os.getenv("PUBLIC_API_KEY")
    if not api_key:
        return []
        
    try:
        from public_api_sdk import PublicApiClient, ApiKeyAuthConfig
        client = PublicApiClient(auth_config=ApiKeyAuthConfig(api_secret_key=api_key))
        accounts = client.get_accounts()
        if not accounts.accounts:
            return []
        account_id = accounts.accounts[0].account_id
        port = client.get_portfolio(account_id)
        
        held_symbols = set()
        for p in (port.positions or []):
            if hasattr(p, "instrument") and hasattr(p.instrument, "symbol"):
                held_symbols.add(p.instrument.symbol.upper())
                
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        newly_active = []
        c.execute("SELECT id, ticker FROM alerts WHERE outcome = 'OPEN' AND user_active = 0")
        for row in c.fetchall():
            t_id, sym = row[0], row[1].upper()
            if sym in held_symbols:
                c.execute("UPDATE alerts SET user_active = 1 WHERE id = ?", (t_id,))
                newly_active.append((t_id, sym))
                
        conn.commit()
        conn.close()
        return newly_active
    except Exception as e:
        return []

# Initialize database on import
init_db()
