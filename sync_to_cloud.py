import sqlite3
import json
import urllib.request
import base64
import sys
import os

def sync(cloud_ip="34.60.86.160", port=8080):
    creds = base64.b64encode(b"F4DukeMitchell:M642423s$").decode("utf-8")
    auth_header = f"Basic {creds}"
    
    # 1. Sync Alerts Table (All 66 columns, all rows)
    conn = sqlite3.connect("wyckoff_trades.db")
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM alerts ORDER BY id ASC")
    rows = [dict(r) for r in c.fetchall()]
    
    print(f"Preparing to sync {len(rows)} alerts with all columns to cloud...")
    url = f"http://{cloud_ip}:{port}/api/alerts/sync"
    data = json.dumps(rows).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Authorization": auth_header},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            print(f"Alerts sync success! Cloud response: {res}")
    except Exception as e:
        print(f"Alerts sync failed: {e}")

    # 2. Sync ml_model_history Table
    try:
        c.execute("SELECT * FROM ml_model_history ORDER BY id ASC")
        ml_rows = [dict(r) for r in c.fetchall()]
        if ml_rows:
            print(f"Syncing {len(ml_rows)} ML history records...")
            url_ml = f"http://{cloud_ip}:{port}/api/ml/sync_history"
            data_ml = json.dumps(ml_rows).encode("utf-8")
            req_ml = urllib.request.Request(
                url_ml,
                data=data_ml,
                headers={"Content-Type": "application/json", "Authorization": auth_header},
                method="POST"
            )
            with urllib.request.urlopen(req_ml, timeout=30) as resp:
                res_ml = json.loads(resp.read().decode("utf-8"))
                print(f"ML history sync success! Cloud response: {res_ml}")
    except Exception as e:
        print(f"ML history sync failed: {e}")

    conn.close()

    # 3. Sync wyckoff_model.pkl artifact
    model_path = "wyckoff_model.pkl"
    if os.path.exists(model_path):
        try:
            with open(model_path, "rb") as f:
                model_bytes = f.read()
            print(f"Uploading {len(model_bytes)} bytes of wyckoff_model.pkl...")
            url_model = f"http://{cloud_ip}:{port}/api/ml/sync_model"
            req_model = urllib.request.Request(
                url_model,
                data=model_bytes,
                headers={"Content-Type": "application/octet-stream", "Authorization": auth_header},
                method="POST"
            )
            with urllib.request.urlopen(req_model, timeout=30) as resp:
                res_model = json.loads(resp.read().decode("utf-8"))
                print(f"Model upload success! Cloud response: {res_model}")
        except Exception as e:
            print(f"Model upload failed: {e}")

if __name__ == "__main__":
    ip = sys.argv[1] if len(sys.argv) > 1 else "34.60.86.160"
    sync(ip)
