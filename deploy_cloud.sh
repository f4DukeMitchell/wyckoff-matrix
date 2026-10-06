#!/usr/bin/env bash
# ==============================================================================
# Wyckoff Terminal & Alert Bot - 1-Click Cloud Deployment Script (Ubuntu/Debian)
# ==============================================================================
set -e

echo "=== 1. Updating System Packages ==="
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv git curl ufw

echo "=== 2. Setting Up Python Virtual Environment ==="
cd "$(dirname "$0")"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip

echo "=== 3. Installing Project Dependencies ==="
pip install -r requirements.txt

echo "=== 4. Verifying Environment File ==="
if [ ! -f .env ]; then
  echo "PUBLIC_API_KEY=FGeoRuju0sM5xhlCLybqc982UnJwYh9F" > .env
  echo "Created default .env file."
fi

CURRENT_DIR=$(pwd)
CURRENT_USER=$(whoami)

echo "=== 5. Creating Systemd Service for Wyckoff Alert Bot ==="
sudo tee /etc/systemd/system/wyckoff-bot.service > /dev/null <<EOF
[Unit]
Description=Wyckoff Matrix Alert Bot Daemon
After=network.target

[Service]
Type=simple
User=${CURRENT_USER}
WorkingDirectory=${CURRENT_DIR}
Environment="PATH=${CURRENT_DIR}/venv/bin"
Environment="PYTHONUNBUFFERED=1"
Environment="PYTHONIOENCODING=utf-8"
ExecStart=${CURRENT_DIR}/venv/bin/python wyckoff_alert_bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

echo "=== 6. Creating Systemd Service for Wyckoff Terminal Server ==="
sudo tee /etc/systemd/system/wyckoff-terminal.service > /dev/null <<EOF
[Unit]
Description=Wyckoff Matrix Standalone Terminal Server
After=network.target

[Service]
Type=simple
User=${CURRENT_USER}
WorkingDirectory=${CURRENT_DIR}
Environment="PATH=${CURRENT_DIR}/venv/bin"
ExecStart=${CURRENT_DIR}/venv/bin/python -m uvicorn terminal_api:app --host 0.0.0.0 --port 8080
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

echo "=== 7. Enabling and Starting Services ==="
sudo systemctl daemon-reload
sudo systemctl enable wyckoff-bot.service
sudo systemctl enable wyckoff-terminal.service

sudo systemctl restart wyckoff-bot.service
sudo systemctl restart wyckoff-terminal.service

echo "=== 8. Configuring Firewall (Opening Port 8080) ==="
sudo ufw allow 8080/tcp || true
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 8080 -j ACCEPT || true

echo "=============================================================================="
echo "DEPLOYMENT COMPLETE!"
echo "Terminal is running at: http://$(curl -s ifconfig.me):8080"
echo "Alert Bot status: sudo systemctl status wyckoff-bot"
echo "Terminal status:  sudo systemctl status wyckoff-terminal"
echo "=============================================================================="
