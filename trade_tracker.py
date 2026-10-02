import sqlite3
import yfinance as yf
import datetime
import os

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
        try: cursor.execute("ALTER TABLE alerts ADD COLUMN pcr REAL")
        except: pass
        try: cursor.execute("ALTER TABLE alerts ADD COLUMN sentiment TEXT")
        except: pass
        try: cursor.execute("ALTER TABLE alerts ADD COLUMN bars_in_regime INTEGER DEFAULT 0")
        except: pass
        conn.commit()
    except Exception as e:
        print(f"Error initializing database: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

def log_alert(ticker, direction, entry_price, stop_loss, take_profit, regime, timeframe='5m', pcr=None, sentiment=None, bars_in_regime=0):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        timestamp = datetime.datetime.now().isoformat()
        cursor.execute('''
            INSERT INTO alerts (ticker, direction, entry_price, stop_loss, take_profit, regime, timestamp, pcr, sentiment, timeframe, bars_in_regime)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (ticker, direction, entry_price, stop_loss, take_profit, regime, timestamp, pcr, sentiment, timeframe, bars_in_regime))
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
        
        for trade in open_trades:
            trade_id = trade['id']
            ticker = trade['ticker']
            direction = trade['direction'].upper()
            entry_price = trade['entry_price']
            stop_loss = trade['stop_loss']
            take_profit = trade['take_profit']
            
            price = get_latest_price(ticker)
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
                    
            if outcome != 'OPEN':
                if direction == 'LONG':
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
                    'pnl_r': pnl_r
                })
                
    except Exception as e:
        print(f"Error checking open trades: {e}")
    finally:
        if 'conn' in locals():
            conn.close()
            
    return closed_trades

def get_stats():
    stats = {
        'total_trades': 0,
        'wins': 0,
        'losses': 0,
        'open_count': 0,
        'win_rate': 0.0,
        'avg_pnl_r': 0.0,
        'best_trade': None,
        'worst_trade': None
    }
    
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) as count FROM alerts")
        stats['total_trades'] = cursor.fetchone()['count']
        
        cursor.execute("SELECT COUNT(*) as count FROM alerts WHERE outcome = 'WIN'")
        stats['wins'] = cursor.fetchone()['count']
        
        cursor.execute("SELECT COUNT(*) as count FROM alerts WHERE outcome = 'LOSS'")
        stats['losses'] = cursor.fetchone()['count']
        
        cursor.execute("SELECT COUNT(*) as count FROM alerts WHERE outcome = 'OPEN'")
        stats['open_count'] = cursor.fetchone()['count']
        
        closed_count = stats['wins'] + stats['losses']
        if closed_count > 0:
            stats['win_rate'] = (stats['wins'] / closed_count) * 100
            
            cursor.execute("SELECT AVG(pnl_r) as avg_pnl FROM alerts WHERE pnl_r IS NOT NULL")
            avg_row = cursor.fetchone()
            if avg_row['avg_pnl'] is not None:
                stats['avg_pnl_r'] = avg_row['avg_pnl']
                
            cursor.execute("SELECT ticker, pnl_r FROM alerts WHERE pnl_r IS NOT NULL ORDER BY pnl_r DESC LIMIT 1")
            best_row = cursor.fetchone()
            if best_row:
                stats['best_trade'] = f"{best_row['ticker']} ({best_row['pnl_r']:.2f}R)"
                
            cursor.execute("SELECT ticker, pnl_r FROM alerts WHERE pnl_r IS NOT NULL ORDER BY pnl_r ASC LIMIT 1")
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

# Initialize database on import
init_db()
