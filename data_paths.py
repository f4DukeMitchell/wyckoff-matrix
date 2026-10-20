"""
Central location for all RUNTIME STATE (live trade DB, auto-buyer config, ML model artifact).

WHY THIS EXISTS
---------------
`wyckoff_trades.db`, `auto_buyer_config.json` and `wyckoff_model.pkl` used to live inside the git
working tree and were tracked by git. Every deploy runs `git reset --hard origin/main`
(auto_updater.sh / POST /api/system/update), which silently overwrote the VM's LIVE database,
config and trained model with whatever stale copy was committed (this wiped a day of paper trades).

Runtime state now lives OUTSIDE the repo checkout, in DATA_DIR (default: `<repo>/../wyckoff_data`,
override with env WYCKOFF_DATA_DIR). `git reset --hard` can no longer touch it.

The copies still tracked in the repo are SEEDS ONLY:
  * DB      : seeded once, only if no live DB exists (and no backup can be restored).
  * config  : seeded once, only if no live config exists.
  * model   : copied over the live model only when the repo's pkl is NEWER (i.e. a deliberate model push).

Safety nets: rolling SQLite backups in DATA_DIR/backups, automatic restore if the live DB is
missing / corrupt / empty.
"""
import os
import glob
import shutil
import sqlite3
import time
import datetime

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("WYCKOFF_DATA_DIR") or os.path.join(os.path.dirname(REPO_DIR), "wyckoff_data")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")

DB_PATH = os.path.join(DATA_DIR, "wyckoff_trades.db")
CONFIG_PATH = os.path.join(DATA_DIR, "auto_buyer_config.json")
MODEL_PATH = os.path.join(DATA_DIR, "wyckoff_model.pkl")

SEED_DB = os.path.join(REPO_DIR, "wyckoff_trades.db")
SEED_CONFIG = os.path.join(REPO_DIR, "auto_buyer_config.json")
SEED_MODEL = os.path.join(REPO_DIR, "wyckoff_model.pkl")

BACKUP_KEEP = 60
_last_backup_ts = 0.0


def _alert_rows(path):
    """Number of rows in alerts table, or -1 if the file is missing / not a valid DB."""
    if not os.path.exists(path):
        return -1
    try:
        conn = sqlite3.connect(path, timeout=10)
        try:
            return conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        finally:
            conn.close()
    except Exception:
        return -1


def _atomic_copy(src, dst):
    tmp = dst + f".tmp{os.getpid()}"
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


def list_backups():
    return sorted(glob.glob(os.path.join(BACKUP_DIR, "wyckoff_trades_*.db")))


def backup_db(force=False, min_interval_sec=1800, tag=""):
    """Consistent online SQLite backup (safe while the bot is writing). Returns path or None."""
    global _last_backup_ts
    now = time.time()
    if not force and (now - _last_backup_ts) < min_interval_sec:
        return None
    try:
        if _alert_rows(DB_PATH) <= 0:
            return None  # never back up an empty / broken DB over good history
        os.makedirs(BACKUP_DIR, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        dst = os.path.join(BACKUP_DIR, f"wyckoff_trades_{stamp}{('_' + tag) if tag else ''}.db")
        src = sqlite3.connect(DB_PATH, timeout=30)
        try:
            out = sqlite3.connect(dst)
            try:
                src.backup(out)
            finally:
                out.close()
        finally:
            src.close()
        _last_backup_ts = now
        for old in list_backups()[:-BACKUP_KEEP]:
            try:
                os.remove(old)
            except OSError:
                pass
        return dst
    except Exception as e:
        print(f"[data_paths] backup failed: {e}")
        return None


def restore_latest_backup():
    """Restore the newest usable backup into DB_PATH. Returns the backup path or None."""
    for b in reversed(list_backups()):
        if _alert_rows(b) > 0:
            _atomic_copy(b, DB_PATH)
            print(f"[data_paths] RESTORED live DB from backup {b}")
            return b
    return None


def ensure_data_files():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(BACKUP_DIR, exist_ok=True)

    # ---- live DB: restore from backup, else seed from the repo copy
    rows = _alert_rows(DB_PATH)
    if rows <= 0:
        if os.path.exists(DB_PATH):
            # corrupt or empty: keep it for forensics, then recover
            try:
                os.replace(DB_PATH, DB_PATH + f".bad_{int(time.time())}")
            except OSError:
                pass
        if not restore_latest_backup():
            if _alert_rows(SEED_DB) > 0:
                _atomic_copy(SEED_DB, DB_PATH)
                print(f"[data_paths] Seeded live DB from repo seed {SEED_DB}")

    # ---- auto-buyer config: seed once
    if not os.path.exists(CONFIG_PATH) and os.path.exists(SEED_CONFIG):
        _atomic_copy(SEED_CONFIG, CONFIG_PATH)

    # ---- model: adopt repo pkl only when it is newer (deliberate push), never overwrite a newer evolved model
    if os.path.exists(SEED_MODEL):
        if (not os.path.exists(MODEL_PATH)) or os.path.getmtime(SEED_MODEL) > os.path.getmtime(MODEL_PATH):
            _atomic_copy(SEED_MODEL, MODEL_PATH)


ensure_data_files()
