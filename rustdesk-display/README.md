# RustDesk Auto Display Switcher

Automatically switches between HDMI (real monitor) and RustDesk headless mode when you plug/unplug a display. No commands to remember, no reboots needed.

## How It Works

```
Monitor plugged in  → udev detects → removes dummy xorg → restarts GDM (~5s) → real display appears
Monitor unplugged   → udev detects → installs dummy xorg → restarts GDM (~5s) → RustDesk gets virtual display
On boot             → systemd runs → detects what's connected → sets correct mode
```

The script uses EDID detection to tell real monitors apart from the dummy driver's virtual output.

## Install

```bash
# On the Ubuntu server:
cd /path/to/server-services/rustdesk-display
sudo ./install.sh
```

That's it. Everything is automatic after install.

## Files

| File | Installs To | Purpose |
|------|-------------|---------|
| `rustdesk-display-auto-switch` | `/usr/local/bin/` | Main switch logic |
| `99-rustdesk-display-switch.rules` | `/etc/udev/rules.d/` | Triggers on monitor plug/unplug |
| `rustdesk-display-switch.service` | `/etc/systemd/system/` | Systemd oneshot (udev + boot) |
| `xorg-dummy.conf` | `/etc/rustdesk/xorg.conf` | Dummy display template |
| `install.sh` | — | One-command setup |
| `uninstall.sh` | — | Clean removal |

## Useful Commands

All commands run **on the Ubuntu server** — nothing needed on your Mac.

```bash
# Check what mode you're in
sudo systemctl status rustdesk-display-switch

# Watch live switching logs
journalctl -t rustdesk-display-switch -f

# Force a re-evaluation
sudo rustdesk-display-auto-switch

# RustDesk status
sudo systemctl status rustdesk
sudo rustdesk --get-id
```

## Uninstall

```bash
sudo ./uninstall.sh
```

Removes the auto-switcher but keeps RustDesk and the dummy Xorg driver installed.

## Requirements

- Ubuntu 22.04+
- RustDesk installed and running
- GDM display manager
- `xserver-xorg-video-dummy` (auto-installed by `install.sh`)

## Notes

- When switching modes, GDM restarts which logs out the desktop session (~5 seconds, same as a manual reboot but much faster)
- The dummy Xorg config creates a 1920×1080 virtual display for RustDesk
- RustDesk ID and password are preserved through mode switches
