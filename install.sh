#!/bin/bash
set -e

REPO_DIR=$(pwd)
if [ ! -f "$REPO_DIR/src/daemon.py" ]; then
    echo "Please run this script from the repository directory."
    exit 1
fi

# Sanity check for the tools the daemon shells out to
for bin in adb scrcpy; do
    if ! command -v "$bin" >/dev/null 2>&1; then
        echo "Warning: '$bin' is not installed or not in PATH. Install it before using the service."
    fi
done

# Generate the local (git-ignored) config from the template on first install.
# It holds per-device settings and the optional lock PIN, so it never goes in git.
if [ ! -f "$REPO_DIR/config.json" ]; then
    cp "$REPO_DIR/config.example.json" "$REPO_DIR/config.json"
    echo "Created config.json from config.example.json."
    echo "  -> To auto-unlock, set \"lock_pin\" in config.json (it is git-ignored)."
fi

echo "Setting up systemd user service..."
mkdir -p ~/.config/systemd/user

cat <<EOF > ~/.config/systemd/user/smartphone-manager.service
[Unit]
Description=Smartphone Manager (wireless ADB + scrcpy + USB tethering failover)
After=graphical-session.target

[Service]
Type=simple
WorkingDirectory=$REPO_DIR
ExecStart=/usr/bin/python3 $REPO_DIR/src/daemon.py
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=$REPO_DIR/src

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now smartphone-manager.service

echo ""
echo "Done! The manager is now running in the background."
echo "Plug in your phone over USB: it will enable wireless adb and launch scrcpy."
echo "Edit config.json to tweak per-phone behavior (scrcpy args, tcpip port, etc.)."
echo "Logs: journalctl --user -u smartphone-manager.service -f"
