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

    print(f"Read {len(rows)} alerts from local database.")
    
    url = f"http://{cloud_ip}:{port}/api/alerts/sync"
    creds = base64.b64encode(b"F4DukeMitchell:M642423s$").decode("utf-8")
    data = json.dumps(rows).encode("utf-8")
    
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
