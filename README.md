[1] Open the systemd service file, this file tells Ubuntu how to automatically start your node when the system boots up.
(do CTRL+ALT+T for open terminal in ubuntu desktop)

sudo nano /etc/systemd/system/quai.service

[2] Paste inside Nodeconfig.txt data and save and exit (Ctrl+O, Enter, Ctrl+X).

[3] Reload and start the service
sudo systemctl daemon-reload        #Detect the new file
sudo systemctl enable quai              #Activate to start at boot
sudo systemctl start  quai                 #Start the node now
sudo systemctl status quai                #Make sure it is active

[4] Prepare the Python environment, why Python? your control script is written in Python, you need an isolated (virtual) environment with only the libraries you use.

Install Python and pip

sudo apt install -y python3 python3-venv python3-pip

Create and activate virtual environment

mkdir -p ~/quai-control
python3 -m venv ~/quai-control/venv
source ~/quai-control/venv/bin/activate

Install necessary libraries

pip install aiohttp websockets

[5] Write the control script

sudo nano ~/quai-control/update_pref.py
Paste python-script text inside
Save and exit (Ctrl+O, Enter, Ctrl+X).

and add it to systemd

sudo nano /etc/systemd/system/quai-pref.service

Paste quai mining preference controller.txt inside
Save and exit (Ctrl+O, Enter, Ctrl+X).

Reload systemd, enable & start the service

sudo systemctl daemon-reload                                              #Detect the new file
sudo systemctl enable quai-pref.service                                 #Activate to start at boot
sudo systemctl start  quai-pref.service                                   #Start the node now
sudo systemctl status quai-pref.service                                   #Make sure it is active

You should see it active (running). To watch its logs in real time:
journalctl -u quai-pref.service -f

With that you have node and python script for preference already setup and starting with boot you only need to config stratum with quai network guide and do mining.
The script only change preference if the change its more than 1%.
