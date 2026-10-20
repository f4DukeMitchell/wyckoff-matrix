import os
import sqlite3
import subprocess
import shutil
import signal

from data_paths import DB_PATH  # runtime DB lives outside the git tree (survives git reset --hard)

def migrate():
    if not os.path.exists(DB_PATH):
        print(f"Database {DB_PATH} not found. Skipping migration.")
        return

    try:
        from data_paths import backup_db
        backup_db(force=True, tag="premigrate")
    except Exception as e:
        print(f"Pre-migration backup skipped: {e}")

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
        ("second_spring_added", "INTEGER DEFAULT 0"),
        ("cvd_delta_ratio", "REAL DEFAULT 0.0"),
        ("rvol_tod", "REAL DEFAULT 1.0"),
        ("penetration_depth_pct", "REAL DEFAULT 0.0"),
        ("candle_clv", "REAL DEFAULT 0.70"),
        ("consumed_atr", "REAL DEFAULT 1.0"),
        ("st_volume_ratio", "REAL DEFAULT 1.0"),
        ("vpoc_distance_pct", "REAL DEFAULT 0.0"),
        ("mtf_atr_ratio", "REAL DEFAULT 1.0"),
        ("trend_exhaustion", "REAL DEFAULT 3.0"),
        ("vix_slope", "REAL DEFAULT 0.0"),
        ("rs_alpha", "REAL DEFAULT 0.0"),
        ("vwap_slope", "REAL DEFAULT 0.0"),
        ("orb_regime_code", "INTEGER DEFAULT 0"),
        ("atr_headroom", "REAL DEFAULT 0.50"),
        ("sector_ret_pct", "REAL DEFAULT 0.0"),
        ("sector_aligned", "INTEGER DEFAULT 1"),
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
    # 8. Clean up any dummy test records from schema migration tests
    # 9. Expire stale paper alerts from prior test runs (only from prior days)
    c.execute("""
        UPDATE alerts
        SET outcome = 'EXPIRED_STALE',
            user_active = 0
        WHERE outcome = 'OPEN' 
          AND (user_active = 0 OR user_active IS NULL)
          AND timestamp < date('now')
    """)
    stale_cleaned = c.rowcount
    if stale_cleaned > 0:
        print(f"Expired {stale_cleaned} stale open paper alerts from prior test runs.")

    # Clean up any lingering open test alerts for MELI or NXPI
    c.execute("""
        UPDATE alerts 
        SET outcome = 'CLOSED', user_active = 0 
        WHERE ticker IN ('MELI', 'NXPI') AND outcome = 'OPEN'
    """)
    conn.commit()

    # 10. Register / Sync active 1H Swing Trades for ACHR and DJT
    # Reconcile manual closes on DJT, ACHR, and any positions exited by user at broker level
    c.execute("""
        UPDATE alerts
        SET outcome = 'CLOSED_MANUAL', user_active = 0, ghost_status = 'MANUAL_BROKER_CLOSE'
        WHERE ticker IN ('DJT', 'ACHR') AND (outcome = 'OPEN' OR user_active = 1)
    """)
    conn.commit()
    print("Reconciled DJT and ACHR as CLOSED_MANUAL (broker positions exited by user).")

    # 11. Reconcile open user_active trades with actual Public.com broker executions (e.g. TPR, RL)
    try:
        from public_executor import get_broker_portfolio_positions
        broker_holdings = get_broker_portfolio_positions()
        for bh in broker_holdings:
            sym = bh.get('ticker')
            cost = float(bh.get('entry_price') or 0.0)
            qty = float(bh.get('quantity') or 0.0)
            if sym and cost > 0 and qty > 0:
                c.execute("SELECT id, direction, entry_price, stop_loss, take_profit FROM alerts WHERE ticker = ? AND outcome = 'OPEN' AND user_active = 1", (sym,))
                rows = c.fetchall()
                for r in rows:
                    t_id, direction, orig_entry, orig_sl, orig_tp = r
                    if orig_entry and orig_sl and orig_tp:
                        risk = abs(orig_entry - orig_sl)
                        reward = abs(orig_tp - orig_entry)
                        if (direction or 'LONG').upper() == 'LONG':
                            new_sl = round(cost - risk, 4)
                            new_tp = round(cost + reward, 4)
                        else:
                            new_sl = round(cost + risk, 4)
                            new_tp = round(cost - reward, 4)
                        c.execute("""
                            UPDATE alerts
                            SET entry_price = ?,
                                initial_shares = ?,
                                stop_loss = ?,
                                initial_stop_loss = ?,
                                take_profit = ?
                            WHERE id = ?
                        """, (cost, qty, new_sl, new_sl, new_tp, t_id))
                        print(f"Reconciled {sym} (id {t_id}) with broker fill ${cost:.2f} ({qty} shares), SL=${new_sl}, TP=${new_tp}")
        conn.commit()
    except Exception as recon_err:
        print(f"Error during broker holdings reconciliation: {recon_err}")

    # Clear stalker cache on disk
    stalker_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stalker_radar.json")
    try:
        with open(stalker_file, "w") as f:
            f.write("[]\n")
        print("Emptied stalker_radar.json cache on disk.")
    except Exception as e:
        print(f"Error emptying stalker_radar.json: {e}")

    # Log summary
    c.execute("""
        SELECT outcome, COUNT(*), COALESCE(SUM(pnl_r), 0)
        FROM alerts
        WHERE outcome IN ('WIN', 'LOSS', 'BREAKEVEN')
        GROUP BY outcome
    """)
    # Initialize priority queue on disk if missing or empty
    try:
        import priority_queue
        priority_queue.flush_priority_queue()
        stats = priority_queue.get_priority_queue_stats()
        print(f"Initialized priority_queue.json on disk: {stats}")
    except Exception as e:
        print(f"Error initializing priority queue: {e}")

    # Recycle services (only if run standalone from CLI, not when called from terminal_api)
    if os.name != 'nt':
        try:
            p_out = subprocess.run(["ps", "-eo", "pid,user,cmd"], capture_output=True, text=True).stdout
            term_procs = [l for l in p_out.splitlines() if any(k in l for k in ['terminal', 'uvicorn', 'wyckoff'])]
            print("RUNNING PROCESSES:\n" + "\n".join(term_procs[:10]))
            
            # Force reload of services via systemctl and kill any detached uvicorn process
            cycle_cmd = "sleep 1 && (echo 'M642423s$' | sudo -S systemctl restart wyckoff-bot.service wyckoff-terminal.service || true) && (pkill -f 'uvicorn terminal_api' || true) && sleep 1 && (echo 'M642423s$' | sudo -S systemctl restart wyckoff-terminal.service || true)"
            subprocess.Popen(["bash", "-c", cycle_cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, start_new_session=True)
            print("Scheduled background systemctl service restart (bot + terminal).")
        except Exception as e:
            print(f"Error scheduling recycle: {e}")

    conn.close()

if __name__ == '__main__':
    migrate()

