#!/bin/bash
set -e

echo "Stopping and disabling systemd user service..."
systemctl --user disable --now linux-android-daemon.service 2>/dev/null || true

echo "Removing service file..."
rm -f ~/.config/systemd/user/linux-android-daemon.service
systemctl --user daemon-reload

echo "Uninstallation complete!"
