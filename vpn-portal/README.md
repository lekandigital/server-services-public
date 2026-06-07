# Proton VPN Portal (:8011)

Flask control panel for the server's Proton VPN CLI session.

## Features

- Shows current tunnel status from `protonvpn status`
- Lists cached Proton logical servers from `~/.cache/Proton/VPN/serverlist.json`
- Connects by fastest, country, city, random, P2P, Tor, Secure Core, or exact server ID
- Disconnects the tunnel
- Applies recommended CLI settings such as kill switch, NetShield, and VPN Accelerator

## Requirements

The Proton VPN Linux CLI must already be installed and logged in for the service user.

```bash
pip3 install -r requirements.txt
python3 server.py --port 8011
```

## Systemd

```bash
sudo cp proton-vpn-portal.service /etc/systemd/system/
# Replace __USER__ and __INSTALL_DIR__ placeholders if installing manually.
sudo systemctl daemon-reload
sudo systemctl enable --now proton-vpn-portal
```
