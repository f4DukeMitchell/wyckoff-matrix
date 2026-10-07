import os
import sqlite3

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

if __name__ == '__main__':
    migrate()
