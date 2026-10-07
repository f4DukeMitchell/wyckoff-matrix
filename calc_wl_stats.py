import sqlite3
import json

def get_wl_breakdown(db_path="wyckoff_trades.db"):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # Overall
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
    overall = dict(c.fetchone() or {})
    tot = overall.get('total') or 0
    wins = overall.get('wins') or 0
    overall['win_rate'] = round((wins / tot * 100.0), 1) if tot > 0 else 0.0
    overall['net_r'] = round(float(overall.get('net_r') or 0.0), 2)
    overall['avg_win_r'] = round(float(overall.get('avg_win_r') or 0.0), 2)
    overall['avg_loss_r'] = round(float(overall.get('avg_loss_r') or 0.0), 2)
    
    # Profit factor
    tot_win_r = sum(r[0] for r in c.execute("SELECT pnl_r FROM alerts WHERE outcome = 'WIN' AND pnl_r > 0").fetchall())
    tot_loss_r = abs(sum(r[0] for r in c.execute("SELECT pnl_r FROM alerts WHERE outcome = 'LOSS' AND pnl_r < 0").fetchall()))
    overall['profit_factor'] = round((tot_win_r / tot_loss_r), 2) if tot_loss_r > 0 else 0.0
    
    # By Direction
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
    by_dir = {}
    for r in c.fetchall():
        d = dict(r)
        d_tot = d.get('total') or 0
        d_win = d.get('wins') or 0
        d_loss = d.get('losses') or 0
        d['win_rate'] = round((d_win / d_tot * 100.0), 1) if d_tot > 0 else 0.0
        d['directional_win_rate'] = round((d_win / (d_win + d_loss) * 100.0), 1) if (d_win + d_loss) > 0 else 0.0
        d['net_r'] = round(float(d.get('net_r') or 0.0), 2)
        by_dir[d['direction']] = d
        
    # By Timeframe
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
    by_tf = {}
    for r in c.fetchall():
        d = dict(r)
        tf_tot = d.get('total') or 0
        tf_win = d.get('wins') or 0
        tf_loss = d.get('losses') or 0
        d['win_rate'] = round((tf_win / tf_tot * 100.0), 1) if tf_tot > 0 else 0.0
        d['directional_win_rate'] = round((tf_win / (tf_win + tf_loss) * 100.0), 1) if (tf_win + tf_loss) > 0 else 0.0
        d['net_r'] = round(float(d.get('net_r') or 0.0), 2)
        by_tf[d['timeframe']] = d

    conn.close()
    return {
        'overall': overall,
        'by_direction': by_dir,
        'by_timeframe': by_tf
    }

if __name__ == '__main__':
    data = get_wl_breakdown()
    print(json.dumps(data, indent=2))
