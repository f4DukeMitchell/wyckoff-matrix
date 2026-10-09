#!/usr/bin/env bash
# ==============================================================================
# Wyckoff Cloud Auto-Updater (Runs periodically or on-demand)
# Pulls latest code from GitHub and hot-reloads services if new commits exist
# ==============================================================================
set -e

cd "$(dirname "$0")"

git fetch origin main

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" != "$REMOTE" ]; then
    echo "[$(date)] New changes detected on GitHub! Updating..."
    git pull origin main
    
    # Update any new dependencies
    if [ -f venv/bin/pip ]; then
        venv/bin/pip install -r requirements.txt --quiet
    fi
    
    # Run database schema migration & outcome calibration
    if [ -f venv/bin/python ]; then
        venv/bin/python migrate_db.py
    elif command -v python3 &>/dev/null; then
        python3 migrate_db.py
    fi
    
    # Restart the bot and terminal services
    echo 'M642423s$' | sudo -S systemctl restart wyckoff-bot.service || true
    echo 'M642423s$' | sudo -S systemctl restart wyckoff-terminal.service || true
    
    echo "[$(date)] Update complete! Services restarted."
else
    echo "[$(date)] System is up to date."
fi
