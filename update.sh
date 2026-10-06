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
    
    # Restart the bot and terminal services
    sudo systemctl restart wyckoff-bot.service
    sudo systemctl restart wyckoff-terminal.service
    
    echo "[$(date)] Update complete! Services restarted."
else
    echo "[$(date)] System is up to date."
fi
