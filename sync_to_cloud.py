import sqlite3
import json
import urllib.request
import base64
import sys

def sync(cloud_ip="34.60.86.160", port=8080):
    conn = sqlite3.connect("wyckoff_trades.db")
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT 200")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()

    if not rows:
        print("No alerts to sync.")
        return

    # Filter to columns safely accepted by cloud schema
    valid_cols = set(['ticker', 'direction', 'entry_price', 'stop_loss', 'take_profit', 'regime', 'timestamp', 'outcome', 'exit_price', 'pnl_r', 'pcr', 'sentiment', 'timeframe', 'bars_in_regime', 'vwap_distance', 'hour_of_day', 'spy_bullish', 'atr_expansion', 'bid_ask_ratio', 'spread_width_pct', 'implied_volatility', 'model_version', 'ml_confidence', 'initial_stop_loss', 'effort_vs_result', 'test_vol_ratio', 'days_to_rebalance', 'is_triple_witching', 'dealer_gamma_regime', 'gamma_wall_dist_pct', 'moc_surge_score', 'institutional_block_ratio', 'optimal_target_r', 'cvd_delta_ratio', 'rvol_tod', 'resolved_at', 'source', 'notes', 'partial_exit_done', 'partial_tier_done', 'initial_shares', 'peak_high_r', 'trailing_stop_price', 'partial_exit_price', 'partial_pnl_r', 'second_spring_added', 'ghost_status', 'ghost_outcome', 'ghost_exit_price', 'ghost_pnl_r', 'ghost_resolved_at'])
    filtered_rows = [{k: v for k, v in r.items() if k in valid_cols} for r in rows]

    url = f"http://{cloud_ip}:{port}/api/alerts/sync"
    creds = base64.b64encode(b"F4DukeMitchell:M642423s$").decode("utf-8")
    data = json.dumps(filtered_rows).encode("utf-8")
    
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Basic {creds}"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            print(f"Sync success! Cloud response: {res}")
    except Exception as e:
        print(f"Sync failed: {e}")

if __name__ == "__main__":
    ip = sys.argv[1] if len(sys.argv) > 1 else "34.60.86.160"
    sync(ip)
