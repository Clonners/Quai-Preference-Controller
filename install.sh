#!/usr/bin/env bash
set -e

# ————— CONFIG —————
REPO="https://github.com/Clonners/Quai-Preference-Controller.git"
USER="${SUDO_USER:-$(whoami)}"
BASE="/home/$USER/quai-controller"
SERVICE="/etc/systemd/system/quai-pref.service"
# —————————————————

echo "→ Installing Quai Preference Controller as user: $USER"

# 0) Ensure required system packages are present
echo "→ Checking required system packages..."
apt-get update -qq
for pkg in git python3-venv python3-pip; do
  if ! dpkg -s "$pkg" >/dev/null 2>&1; then
    echo "   • $pkg not found — installing..."
    apt-get install -y "$pkg"
  fi
done

# 1) Clone or update the repository
if [ ! -d "$BASE" ]; then
  echo "→ Cloning repository into $BASE"
  sudo -u "$USER" git clone "$REPO" "$BASE"
else
  echo "→ Repository already exists at $BASE — pulling latest changes"
  cd "$BASE"
  sudo -u "$USER" git pull
fi

# 2) Set up Python virtualenv and install dependencies
echo "→ Setting up Python virtual environment"
cd "$BASE"
sudo -u "$USER" python3 -m venv venv

echo "→ Installing Python packages (aiohttp, websockets, numpy)"
# activate and install
sudo -u "$USER" bash -c "\
  source venv/bin/activate && \
  pip install --upgrade pip && \
  pip install aiohttp websockets numpy \
"

# 3) Write the systemd service unit
echo "→ Writing systemd unit file to $SERVICE"
cat << EOF | sudo tee "$SERVICE" >/dev/null
[Unit]
Description=Quai Preference Controller
After=network.target

[Service]
User=$USER
WorkingDirectory=$BASE
ExecStart=$BASE/venv/bin/python $BASE/update_pref.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# 4) Reload systemd and start the service
echo "→ Reloading systemd, enabling and starting service"
sudo systemctl daemon-reload
sudo systemctl enable quai-pref.service
sudo systemctl restart  quai-pref.service

echo "✅ Installation complete!"
echo "   To view live logs: journalctl -u quai-pref.service -f"

