PREREQUISITES: QUAI NODE ALREADY INSTALLED, STRATUM ALREADY INSTALLED, IF YOU DONT HAVE IT GUIDE BELOW.

https://docs.qu.ai/guides/client/node

https://docs.qu.ai/guides/client/stratum

[1] Open the systemd service file, this file tells Ubuntu how to automatically start your node when the system boots up.
(do CTRL+ALT+T for open terminal in ubuntu desktop)

sudo nano /etc/systemd/system/quai.service

[2] Paste inside Nodeconfig.txt data, REPLACE UBUNTU USER, and QUAI and QI ADDRESS, save and exit (Ctrl+O, Enter, Ctrl+X).

[3] Reload and start the service:

sudo systemctl daemon-reload

sudo systemctl enable quai

sudo systemctl start  quai

sudo systemctl status quai

[4] Run instalation:

curl -fsSL https://raw.githubusercontent.com/Clonners/Quai-Preference-Controller/main/install.sh | sudo bash

You should see it active (running). To watch its logs in real time:
journalctl -u quai-pref.service -f

With that you have node and python script for preference already setup and starting with boot you only need to config stratum with quai network guide and do mining.
The script only change preference if the change its more than 1%.
