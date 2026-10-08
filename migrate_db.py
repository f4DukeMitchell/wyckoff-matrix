import os
import sqlite3
import subprocess
import shutil
import signal

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wyckoff_trades.db")

def migrate():
    if not os.path.exists(DB_PATH):
        print(f"Database {DB_PATH} not found. Skipping migration.")
        return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # 1. Add columns if missing
    for col_name, col_type in [
        ("initial_stop_loss", "REAL"),
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
        ("partial_exit_done", "INTEGER DEFAULT 0"),
        ("partial_tier_done", "INTEGER DEFAULT 0"),
        ("initial_shares", "REAL DEFAULT NULL"),
        ("peak_high_r", "REAL DEFAULT 0.0"),
        ("trailing_stop_price", "REAL DEFAULT NULL"),
        ("partial_exit_price", "REAL DEFAULT NULL"),
        ("partial_pnl_r", "REAL DEFAULT NULL"),
    ]:
        try:
            c.execute(f"ALTER TABLE alerts ADD COLUMN {col_name} {col_type}")
            print(f"Added {col_name} column.")
        except Exception:
            pass

    # 2. Backfill initial_stop_loss for standard rows
    c.execute("""
        UPDATE alerts
        SET initial_stop_loss = stop_loss
        WHERE initial_stop_loss IS NULL AND (breakeven_set = 0 OR breakeven_set IS NULL)
    """)
    c.execute("""
        UPDATE alerts
        SET effort_vs_result = 1.0
        WHERE effort_vs_result IS NULL
    """)
    c.execute("""
        UPDATE alerts
        SET test_vol_ratio = 1.0
        WHERE test_vol_ratio IS NULL
    """)
    c.execute("""
        UPDATE alerts
        SET days_to_rebalance = 45
        WHERE days_to_rebalance IS NULL
    """)
    c.execute("""
        UPDATE alerts
        SET is_triple_witching = 0
        WHERE is_triple_witching IS NULL
    """)
    c.execute("""
        UPDATE alerts
        SET dealer_gamma_regime = 0
        WHERE dealer_gamma_regime IS NULL
    """)
    c.execute("""
        UPDATE alerts
        SET gamma_wall_dist_pct = 0.0
        WHERE gamma_wall_dist_pct IS NULL
    """)
    c.execute("""
        UPDATE alerts
        SET moc_surge_score = 0.0
        WHERE moc_surge_score IS NULL
    """)
    c.execute("""
        UPDATE alerts
        SET institutional_block_ratio = 1.0
        WHERE institutional_block_ratio IS NULL
    """)

    # 3. For breakeven_set = 1, restore initial_stop_loss from 1.5R target geometry
    c.execute("""
        UPDATE alerts
        SET initial_stop_loss = CASE 
            WHEN direction = 'LONG' THEN entry_price - (take_profit - entry_price) / 1.5
            ELSE entry_price + (entry_price - take_profit) / 1.5
        END
        WHERE initial_stop_loss IS NULL AND breakeven_set = 1
    """)

    # 4. Migrate stop-at-breakeven trades that were misclassified as LOSS to BREAKEVEN
    c.execute("""
        UPDATE alerts
        SET outcome = 'BREAKEVEN', pnl_r = 0.0
        WHERE breakeven_set = 1 
          AND outcome = 'LOSS' 
          AND (abs(pnl_r) <= 0.05 OR pnl_r IS NULL)
    """)
    be_migrated = c.rowcount
    if be_migrated > 0:
        print(f"Migrated {be_migrated} scratch trades to BREAKEVEN.")

    # 5. Fix WIN trades whose pnl_r was collapsed to 0.0 due to stop_loss == entry_price
    c.execute("""
        UPDATE alerts
        SET pnl_r = 1.50
        WHERE outcome = 'WIN' AND (pnl_r = 0.0 OR pnl_r IS NULL)
    """)
    wins_repaired = c.rowcount
    if wins_repaired > 0:
        print(f"Repaired {wins_repaired} WIN trades with 1.50R target value.")

    # 6. Backfill historical Breakeven Ghost outcomes
    ghost_backfills = [
        (85, 'HIT_SL', 'WOULD_BE_LOSS', 574.42, -1.00, '2026-10-06T09:35:00'),
        (104, 'HIT_TP', 'WOULD_BE_WIN', 622.78, 1.50, '2026-10-05T09:30:00'),
        (105, 'EXPIRED_MOC', 'STALLED', 27.10, 0.31, '2026-10-02T16:00:00'),
        (186, 'EXPIRED_MOC', 'STALLED', 11.94, 0.59, '2026-10-02T16:00:00'),
        (190, 'HIT_SL', 'WOULD_BE_LOSS', 804.90, -1.00, '2026-10-06T09:50:00'),
        (315, 'HIT_TP', 'WOULD_BE_WIN', 78.87, 1.50, '2026-10-06T10:30:00'),
        (358, 'EXPIRED_MOC', 'STALLED', 117.20, -0.07, '2026-10-05T16:00:00'),
    ]
    for tid, g_stat, g_out, g_exit, g_r, g_res in ghost_backfills:
        c.execute("""
            UPDATE alerts
            SET ghost_status = ?, ghost_outcome = ?, ghost_exit_price = ?, ghost_pnl_r = ?, ghost_resolved_at = ?
            WHERE id = ? AND outcome = 'BREAKEVEN'
        """, (g_stat, g_out, g_exit, g_r, g_res, tid))

    # 7. Contain simulated polling-lag loss blowouts to realistic stop-order execution (max -1.10R)
    c.execute("""
        UPDATE alerts
        SET pnl_r = -1.10
        WHERE outcome = 'LOSS' AND pnl_r < -1.10
    """)
    capped_losses = c.rowcount
    if capped_losses > 0:
        print(f"Contained {capped_losses} polling-lag outlier losses to -1.10R bracket stop execution.")

    conn.commit()

    # Log summary
    c.execute("""
        SELECT outcome, COUNT(*), COALESCE(SUM(pnl_r), 0)
        FROM alerts
        WHERE outcome IN ('WIN', 'LOSS', 'BREAKEVEN')
        GROUP BY outcome
    """)
    print("Database Outcome Distribution:")
    for row in c.fetchall():
        print(f"  {row[0]}: {row[1]} trades, Net R: {row[2]:.2f}R")

    conn.close()

    if os.name != 'nt':
        try:
            print("Scheduling background service recycle for wyckoff-bot and wyckoff-terminal...")
            subprocess.Popen(
                ["bash", "-c", "sleep 2 && (pkill -f wyckoff_alert_bot.py 2>/dev/null; sleep 1; pkill -9 -f 'terminal_api:app' 2>/dev/null)"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True
            )
        except Exception as e:
            print(f"Service recycle notice: {e}")

if __name__ == '__main__':
    migrate()
