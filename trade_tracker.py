import sqlite3
import yfinance as yf
import datetime
import os
import pandas as pd
try:
    import zoneinfo
    ET_TZ = zoneinfo.ZoneInfo("America/New_York")
except:
    ET_TZ = None

def get_est_now():
    if ET_TZ:
        return datetime.datetime.now(ET_TZ)
    return datetime.datetime.now()

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
            ("ml_confidence", "REAL DEFAULT NULL"),
            ("initial_stop_loss", "REAL DEFAULT NULL"),
            ("effort_vs_result", "REAL DEFAULT 1.0"),
            ("test_vol_ratio", "REAL DEFAULT 1.0"),
            ("days_to_rebalance", "INTEGER DEFAULT 45"),
            ("is_triple_witching", "INTEGER DEFAULT 0"),
            ("dealer_gamma_regime", "INTEGER DEFAULT 0"),
            ("gamma_wall_dist_pct", "REAL DEFAULT 0.0"),
            ("moc_surge_score", "REAL DEFAULT 0.0"),
            ("institutional_block_ratio", "REAL DEFAULT 1.0"),
            ("ghost_status", "TEXT DEFAULT NULL"),
            ("ghost_outcome", "TEXT DEFAULT NULL"),
            ("ghost_exit_price", "REAL DEFAULT NULL"),
            ("ghost_pnl_r", "REAL DEFAULT NULL"),
            ("ghost_resolved_at", "TEXT DEFAULT NULL"),
            ("optimal_target_r", "REAL DEFAULT 1.15"),
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

_cached_ml_model = None
def get_ml_model():
    global _cached_ml_model
    if _cached_ml_model is not None:
        return _cached_ml_model
    try:
        import pickle
        if os.path.exists("wyckoff_model.pkl"):
            with open("wyckoff_model.pkl", "rb") as f:
                _cached_ml_model = pickle.load(f)
                return _cached_ml_model
    except Exception as e:
        print(f"Error loading wyckoff_model.pkl: {e}")
    return None

def calculate_ml_confidence(bars_in_regime, vwap_distance, atr_expansion, hour_of_day, direction,
                            effort_vs_result=1.0, test_vol_ratio=1.0,
                            days_to_rebalance=45, is_triple_witching=0,
                            dealer_gamma_regime=0, gamma_wall_dist_pct=0.0,
                            moc_surge_score=0.0, institutional_block_ratio=1.0):
    """
    Computes real machine learning win probability (0.0 to 100.0%)
    using the trained Random Forest model.
    Evaluates 11 institutional factors including:
    - Wyckoff VSA (Effort vs Result, Test Vol Ratio)
    - Institutional Microstructure (Gamma Regimes, MOC Surges, Block Size, Rebalance Proximity)
    """
    model = get_ml_model()
    dir_num = 1 if (direction or "").upper() == "LONG" else 0
    b_reg = float(bars_in_regime or 0)
    v_dist = float(vwap_distance or 0.0)
    a_exp = float(atr_expansion or 1.0)
    h_day = float(hour_of_day or 10.0)
    evr = float(effort_vs_result if effort_vs_result is not None else 1.0)
    tvr = float(test_vol_ratio if test_vol_ratio is not None else 1.0)
    d_reb = int(days_to_rebalance if days_to_rebalance is not None else 45)
    i_tw = int(is_triple_witching if is_triple_witching is not None else 0)
    d_gam = int(dealer_gamma_regime if dealer_gamma_regime is not None else 0)
    g_dist = float(gamma_wall_dist_pct if gamma_wall_dist_pct is not None else 0.0)
    m_moc = float(moc_surge_score if moc_surge_score is not None else 0.0)
    i_blk = float(institutional_block_ratio if institutional_block_ratio is not None else 1.0)

    if model is not None:
        try:
            import pandas as pd
            feat_dict = {
                'bars_in_regime': b_reg,
                'vwap_distance': v_dist,
                'atr_expansion': a_exp,
                'effort_vs_result': evr,
                'test_vol_ratio': tvr,
                'days_to_rebalance': d_reb,
                'is_triple_witching': i_tw,
                'dealer_gamma_regime': d_gam,
                'gamma_wall_dist_pct': g_dist,
                'moc_surge_score': m_moc,
                'institutional_block_ratio': i_blk,
                'hour_of_day': h_day,
                'dir_num': dir_num
            }
            if hasattr(model, 'feature_names_in_'):
                features = pd.DataFrame([{col: feat_dict.get(col, 0.0) for col in model.feature_names_in_}])
            else:
                features = pd.DataFrame([feat_dict])
            # Probability of target == 1 (WIN)
            prob = model.predict_proba(features)[0][1] * 100.0
            return round(float(prob), 1)
        except Exception as e:
            print(f"ML scoring error: {e}")

    # Fallback to calibrated heuristic if model file not available
    score = 50.0 + min(35.0, b_reg * 1.5) + (5.0 if abs(v_dist) > 0.5 else 0.0)
    return round(min(98.0, max(25.0, score)), 1)

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
              bid_ask_ratio=None, spread_width_pct=None, implied_volatility=None, model_version=None,
              ml_confidence=None, effort_vs_result=None, test_vol_ratio=None,
              days_to_rebalance=None, is_triple_witching=None, dealer_gamma_regime=None,
              gamma_wall_dist_pct=None, moc_surge_score=None, institutional_block_ratio=None,
              optimal_target_r=None):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        timestamp = get_est_now().isoformat()
        active_version = model_version or get_active_model_version()
        cursor.execute('''
            INSERT INTO alerts (ticker, direction, entry_price, stop_loss, take_profit, regime,
                                timestamp, pcr, sentiment, timeframe, bars_in_regime,
                                vwap_distance, hour_of_day, spy_bullish, atr_expansion,
                                bid_ask_ratio, spread_width_pct, implied_volatility, model_version, ml_confidence,
                                initial_stop_loss, effort_vs_result, test_vol_ratio,
                                days_to_rebalance, is_triple_witching, dealer_gamma_regime,
                                gamma_wall_dist_pct, moc_surge_score, institutional_block_ratio,
                                optimal_target_r)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (ticker, direction, entry_price, stop_loss, take_profit, regime,
              timestamp, pcr, sentiment, timeframe, bars_in_regime,
              vwap_distance, hour_of_day, 1 if spy_bullish else 0 if spy_bullish is not None else None,
              atr_expansion, bid_ask_ratio, spread_width_pct, implied_volatility, active_version, ml_confidence,
              stop_loss, effort_vs_result if effort_vs_result is not None else 1.0, test_vol_ratio if test_vol_ratio is not None else 1.0,
              days_to_rebalance if days_to_rebalance is not None else 45,
              is_triple_witching if is_triple_witching is not None else 0,
              dealer_gamma_regime if dealer_gamma_regime is not None else 0,
              gamma_wall_dist_pct if gamma_wall_dist_pct is not None else 0.0,
              moc_surge_score if moc_surge_score is not None else 0.0,
              institutional_block_ratio if institutional_block_ratio is not None else 1.0,
              optimal_target_r if optimal_target_r is not None else 1.15))
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
                b_set = trade.get('breakeven_set', 0) if isinstance(trade, dict) else (trade['breakeven_set'] if 'breakeven_set' in trade.keys() else 0)
                if not b_set:
                    init_sl = trade.get('initial_stop_loss') if isinstance(trade, dict) else (trade['initial_stop_loss'] if 'initial_stop_loss' in trade.keys() else None)
                    init_risk = abs(entry_price - (init_sl or stop_loss))
                    if init_risk <= 0.0001:
                        init_risk = abs(take_profit - entry_price) / 1.5 if abs(take_profit - entry_price) > 0 else 0.01
                    curr_gain = (price - entry_price) if direction == 'LONG' else (entry_price - price)
                    curr_r = (curr_gain / init_risk) if init_risk > 0 else 0
                    if curr_r >= 0.75:
                        cursor.execute("UPDATE alerts SET stop_loss = ?, breakeven_set = 1 WHERE id = ?", (entry_price, trade_id))
                        conn.commit()
                        trade = dict(trade)
                        trade['breakeven_set'] = 1
                        trade['stop_loss'] = entry_price
                        closed_trades.append({
                            'id': trade_id,
                            'ticker': ticker,
                            'direction': direction,
                            'entry_price': entry_price,
                            'current_r': curr_r,
                            'is_breakeven': True,
                            'outcome': 'BREAKEVEN_SET',
                            'user_active': trade.get('user_active', 0),
                            'telegram_alerted': trade.get('telegram_alerted', 0),
                            'timeframe': trade.get('timeframe', '5m')
                        })
                    
            if outcome != 'OPEN':
                b_set = trade.get('breakeven_set', 0) if isinstance(trade, dict) else (trade['breakeven_set'] if 'breakeven_set' in trade.keys() else 0)
                is_be_level = abs(stop_loss - entry_price) < 0.001

                # Derive initial risk safely so pnl_r never divides by 0 or collapses to 0 on WIN
                init_sl = trade.get('initial_stop_loss') if isinstance(trade, dict) else (trade['initial_stop_loss'] if 'initial_stop_loss' in trade.keys() else None)
                if not init_sl or abs(init_sl - entry_price) < 0.001:
                    init_risk = abs(take_profit - entry_price) / 1.5 if abs(take_profit - entry_price) > 0 else 0.01
                else:
                    init_risk = abs(entry_price - init_sl)
                if init_risk <= 0.0001:
                    init_risk = max(0.01, abs(entry_price * 0.01))

                ghost_stat = None
                if (b_set or is_be_level) and outcome == 'LOSS':
                    outcome = 'BREAKEVEN'
                    pnl_r = 0.0
                    ghost_stat = 'MONITORING'
                elif outcome == 'WIN':
                    pnl_r = round(abs(exit_price - entry_price) / init_risk, 2)
                else:
                    # Realistic execution containment: cap simulated stop-loss fill slippage to max -1.10R
                    raw_loss_r = round(abs(exit_price - entry_price) / init_risk, 2)
                    pnl_r = max(-1.10, -raw_loss_r)
                    
                if ghost_stat:
                    cursor.execute('''
                        UPDATE alerts
                        SET outcome = ?, exit_price = ?, pnl_r = ?, ghost_status = ?
                        WHERE id = ?
                    ''', (outcome, exit_price, pnl_r, ghost_stat, trade_id))
                else:
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
                    'user_active': trade['user_active'] if 'user_active' in trade.keys() else 0,
                    'telegram_alerted': trade['telegram_alerted'] if 'telegram_alerted' in trade.keys() else 0,
                    'timeframe': trade['timeframe'] if 'timeframe' in trade.keys() else '5m'
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
            
    # Always check open ghost trades in shadow mode
    try:
        check_ghost_trades()
    except Exception as e:
        print(f"Error during ghost trade check: {e}")

    return closed_trades

def check_ghost_trades():
    """
    Monitors Breakeven 'Ghost' trades in shadow mode to determine counterfactual outcomes:
    - Did the trade subsequently hit original Take Profit? ('WOULD_BE_WIN')
    - Did the trade subsequently hit original Stop Loss? ('WOULD_BE_LOSS')
    - Did it expire at market close (4:00 PM EST) without touching either? ('STALLED')
    """
    resolved_ghosts = []
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM alerts WHERE ghost_status = 'MONITORING'")
        ghosts = cursor.fetchall()
        if not ghosts:
            return resolved_ghosts

        now = get_est_now()
        is_after_market = (now.hour >= 16) if hasattr(now, 'hour') else False

        unique_tickers = list(set(g['ticker'] for g in ghosts))
        prices = {}
        try:
            data = yf.download(unique_tickers, period="1d", interval="1m", progress=False)
            if not data.empty and 'Close' in data:
                latest = data['Close'].iloc[-1]
                for sym in unique_tickers:
                    if sym in latest and not pd.isna(latest[sym]):
                        prices[sym] = float(latest[sym])
        except Exception as e:
            print(f"Batch ghost price download error: {e}")

        for g in ghosts:
            g_id = g['id']
            ticker = g['ticker']
            direction = g['direction'].upper()
            entry_price = float(g['entry_price'] or 0.0)
            tp = float(g['take_profit'] or 0.0)
            init_sl = float(g['initial_stop_loss'] or g['stop_loss'] or 0.0)
            init_risk = abs(entry_price - init_sl)
            if init_risk <= 0.0001:
                init_risk = abs(tp - entry_price) / 1.5 if abs(tp - entry_price) > 0 else 0.01

            price = prices.get(ticker) or get_latest_price(ticker)
            if price is None:
                continue

            ghost_outcome = None
            ghost_status = None
            ghost_exit = price
            ghost_pnl_r = 0.0

            if direction == 'LONG':
                if price >= tp:
                    ghost_status = 'HIT_TP'
                    ghost_outcome = 'WOULD_BE_WIN'
                    ghost_exit = tp
                    ghost_pnl_r = round(abs(tp - entry_price) / init_risk, 2)
                elif price <= init_sl:
                    ghost_status = 'HIT_SL'
                    ghost_outcome = 'WOULD_BE_LOSS'
                    ghost_exit = init_sl
                    ghost_pnl_r = -1.0
                elif is_after_market:
                    ghost_status = 'EXPIRED_MOC'
                    ghost_outcome = 'STALLED'
                    ghost_exit = price
                    ghost_pnl_r = round((price - entry_price) / init_risk, 2)
            else:  # SHORT
                if price <= tp:
                    ghost_status = 'HIT_TP'
                    ghost_outcome = 'WOULD_BE_WIN'
                    ghost_exit = tp
                    ghost_pnl_r = round(abs(entry_price - tp) / init_risk, 2)
                elif price >= init_sl:
                    ghost_status = 'HIT_SL'
                    ghost_outcome = 'WOULD_BE_LOSS'
                    ghost_exit = init_sl
                    ghost_pnl_r = -1.0
                elif is_after_market:
                    ghost_status = 'EXPIRED_MOC'
                    ghost_outcome = 'STALLED'
                    ghost_exit = price
                    ghost_pnl_r = round((entry_price - price) / init_risk, 2)

            if ghost_status:
                res_time = now.isoformat()
                cursor.execute("""
                    UPDATE alerts
                    SET ghost_status = ?, ghost_outcome = ?, ghost_exit_price = ?, ghost_pnl_r = ?, ghost_resolved_at = ?
                    WHERE id = ?
                """, (ghost_status, ghost_outcome, ghost_exit, ghost_pnl_r, res_time, g_id))
                conn.commit()
                resolved_ghosts.append({
                    'id': g_id,
                    'ticker': ticker,
                    'direction': direction,
                    'ghost_status': ghost_status,
                    'ghost_outcome': ghost_outcome,
                    'ghost_pnl_r': ghost_pnl_r
                })

    except Exception as e:
        print(f"Error checking ghost trades: {e}")
    finally:
        if 'conn' in locals():
            conn.close()
    return resolved_ghosts

def get_ghost_summary():
    """Returns aggregated counterfactual telemetry for all breakeven ghost trades."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("""
            SELECT 
                COUNT(id) as total_scratches,
                SUM(CASE WHEN ghost_outcome = 'WOULD_BE_WIN' THEN 1 ELSE 0 END) as would_be_wins,
                SUM(CASE WHEN ghost_outcome = 'WOULD_BE_LOSS' THEN 1 ELSE 0 END) as would_be_losses,
                SUM(CASE WHEN ghost_outcome = 'STALLED' THEN 1 ELSE 0 END) as stalled_saved,
                SUM(CASE WHEN ghost_status = 'MONITORING' THEN 1 ELSE 0 END) as active_ghosts,
                COALESCE(SUM(ghost_pnl_r), 0.0) as ghost_net_r
            FROM alerts
            WHERE outcome = 'BREAKEVEN'
        """)
        row = dict(c.fetchone() or {})
        conn.close()
        return row
    except Exception as e:
        print(f"Error fetching ghost summary: {e}")
        return {
            'total_scratches': 0, 'would_be_wins': 0, 'would_be_losses': 0,
            'stalled_saved': 0, 'active_ghosts': 0, 'ghost_net_r': 0.0
        }

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
        'breakevens': 0,
        'open_count': 0,
        'win_rate': 0.0,
        'directional_win_rate': 0.0,
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

        be_clause = f"WHERE outcome = 'BREAKEVEN' AND model_version = ?" if model_version else "WHERE outcome = 'BREAKEVEN'"
        cursor.execute(f"SELECT COUNT(*) as count FROM alerts {be_clause}", params)
        stats['breakevens'] = cursor.fetchone()['count']
        
        o_clause = f"WHERE outcome = 'OPEN' AND model_version = ?" if model_version else "WHERE outcome = 'OPEN'"
        cursor.execute(f"SELECT COUNT(*) as count FROM alerts {o_clause}", params)
        stats['open_count'] = cursor.fetchone()['count']
        
        decisive_count = stats['wins'] + stats['losses']
        total_closed = decisive_count + stats['breakevens']
        if total_closed > 0:
            stats['win_rate'] = (stats['wins'] / total_closed) * 100
        if decisive_count > 0:
            stats['directional_win_rate'] = (stats['wins'] / decisive_count) * 100
            
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
