#!/bin/bash
# Autonomous Git Watchdog & Auto-Deploy Daemon
# Checks GitHub every loop; if a new commit is detected on main, pulls and restarts cleanly.

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR" || exit 1

export HOME="/home/matt_smith"
export GIT_TERMINAL_PROMPT=0
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"

git fetch origin main --quiet 2>/dev/null
LOCAL=$(git rev-parse HEAD 2>/dev/null)
REMOTE=$(git rev-parse origin/main 2>/dev/null)

if [ -n "$REMOTE" ] && [ "$LOCAL" != "$REMOTE" ]; then
    TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[$TIMESTAMP] New algo update detected ($REMOTE). Applying..." >> "$REPO_DIR/updater.log"
    git reset --hard origin/main >> "$REPO_DIR/updater.log" 2>&1
    
    PY_BIN="$REPO_DIR/venv/bin/python"
    [ ! -f "$PY_BIN" ] && PY_BIN="python3"
    
    $PY_BIN migrate_db.py >> "$REPO_DIR/updater.log" 2>&1
    
    pkill -9 -f wyckoff_alert_bot 2>/dev/null
    sleep 1
    sudo systemctl restart wyckoff-bot.service wyckoff-terminal.service 2>/dev/null || (echo 'M642423s$' | sudo -S systemctl restart wyckoff-bot.service wyckoff-terminal.service 2>/dev/null)
    
    echo "[$TIMESTAMP] Algo successfully updated and services restarted on commit $REMOTE." >> "$REPO_DIR/updater.log"
fi
