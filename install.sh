#!/usr/bin/env bash
set -e

# Detecta el usuario que corre sudo (o fallback a quien invoque)
USER="${SUDO_USER:-$(whoami)}"
REPO="https://github.com/Clonners/Quai-Preference-Controller.git"
BASE="/home/$USER/quai-controller"
SERVICE="/etc/systemd/system/quai-pref.service"

echo "→ Installing Quai Preference Controller as user: $USER"

# 1) Clona o actualiza el repo
if [ ! -d "$BASE" ]; then
  echo "→ Cloning repository into $BASE"
  sudo -u "$USER" git clone "$REPO" "$BASE"
else
  echo "→ Updating repository in $BASE"
  cd "$BASE"
  sudo -u "$USER" git pull
fi

# 2) Prepara el virtualenv e instala deps
echo "→ Setting up Python virtualenv"
cd "$BASE"
sudo -u "$USER" python3 -m venv venv
# instala sin afectar al sistema
sudo -u "$USER" bash -c "source venv/bin/activate && pip install --upgrade pip aiohttp websockets"

# 3) Genera el servicio systemd
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

# 4) Habilita y arranca el servicio
echo "→ Enabling and starting quai-pref.service"
sudo systemctl daemon-reload
sudo systemctl enable quai-pref.service
sudo systemctl restart  quai-pref.service

echo "✅ Installation complete!"
echo "   To view logs: journalctl -u quai-pref.service -f"
