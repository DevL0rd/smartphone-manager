#!/bin/bash
set -e

echo "Stopping and disabling systemd user service..."
systemctl --user disable --now smartphone-manager.service 2>/dev/null || true

echo "Removing service file..."
rm -f ~/.config/systemd/user/smartphone-manager.service
systemctl --user daemon-reload

echo "Uninstallation complete!"
