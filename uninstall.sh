#!/bin/bash
set -e

echo "Stopping and disabling systemd user service..."
systemctl --user disable --now scrcpy-autolaunch.service 2>/dev/null || true

echo "Removing service file..."
rm -f ~/.config/systemd/user/scrcpy-autolaunch.service
systemctl --user daemon-reload

echo "Uninstallation complete!"
