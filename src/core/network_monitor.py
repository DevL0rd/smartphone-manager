import os
import time
import threading
import subprocess

class NetworkMonitor:
    """Polls NetworkManager and reports whether the PC has a *real* uplink.

    A "real" uplink is a connected wifi or (non-USB) ethernet device. The phone's
    own USB-tether interface is itself an ethernet device, so it is explicitly
    excluded — otherwise enabling tethering would look like "we have wifi" and we
    would immediately turn it back off.
    """

    def __init__(self, on_change, poll_interval=3):
        self.on_change = on_change
        self.poll_interval = poll_interval
        self.online = None

    def start(self):
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    @staticmethod
    def _is_usb_iface(dev):
        # USB-attached NICs have 'usb' in their /sys/class/net device path
        try:
            return "usb" in os.readlink(f"/sys/class/net/{dev}")
        except OSError:
            return False

    def has_real_uplink(self):
        try:
            out = subprocess.run(
                ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device", "status"],
                capture_output=True, text=True, timeout=10
            ).stdout
        except Exception:
            return self.online if self.online is not None else True
        for line in out.splitlines():
            parts = line.split(":")
            if len(parts) < 3:
                continue
            dev, typ, state = parts[0], parts[1], parts[2]
            if typ not in ("wifi", "ethernet"):
                continue
            if not state.startswith("connected"):
                continue
            if typ == "ethernet" and self._is_usb_iface(dev):
                continue  # this is the phone's USB tether, not a real uplink
            return True
        return False

    def _loop(self):
        while True:
            online = self.has_real_uplink()
            if online != self.online:
                self.online = online
                self.on_change(online)
            time.sleep(self.poll_interval)
