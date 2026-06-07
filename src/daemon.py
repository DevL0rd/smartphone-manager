import os
import time
import subprocess
from core.config import load_config, save_config, ensure_device_in_config, CONFIG_FILE
from core.adb_monitor import AdbMonitor

def send_notification(title, message, icon="smartphone", urgency="normal"):
    try:
        subprocess.run([
            "notify-send",
            "-a", "Smartphone Manager",
            "-i", icon,
            "-u", urgency,
            title,
            message
        ])
    except Exception as e:
        print(f"[Notify] Failed to send notification: {e}")

class PhoneWatcher:
    def __init__(self):
        self.config = load_config()
        self.last_config_mtime = os.path.getmtime(CONFIG_FILE) if os.path.exists(CONFIG_FILE) else 0
        self.adb_monitor = AdbMonitor(self.on_phone_connect, self.on_phone_disconnect)

        # State: track the scrcpy process we spawned per phone
        self.scrcpy_procs = {}

    def start(self):
        print("Starting Smartphone Manager...")
        print("Waiting for a phone to be plugged in over USB.")
        self.adb_monitor.start()

        # Keep daemon alive
        while True:
            time.sleep(1)

    def _wait_until_ready(self, serial, timeout=20):
        """Block until the phone's USB transport is back and responsive.

        After `adb tcpip` restarts adbd, the transport flaps; `wait-for-device`
        can return on the stale connection. Polling `adb shell true` over the
        USB serial confirms the device is genuinely ready for scrcpy.
        """
        # Give adbd a moment to actually tear down the old connection first
        time.sleep(1.5)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                result = subprocess.run(
                    ["adb", "-s", serial, "shell", "true"],
                    capture_output=True, timeout=5
                )
                if result.returncode == 0:
                    return True
            except subprocess.TimeoutExpired:
                pass
            time.sleep(0.5)
        return False

    def _reload_config_if_changed(self):
        if os.path.exists(CONFIG_FILE):
            current_mtime = os.path.getmtime(CONFIG_FILE)
            if current_mtime > self.last_config_mtime:
                self.config = load_config()
                self.last_config_mtime = current_mtime
                print("[Config] Reloaded changes from config.json")

    def on_phone_connect(self, serial, model):
        print(f"\n[Event] Phone Connected: {model or serial} ({serial})")
        self._reload_config_if_changed()

        # Auto-add the phone to the config the first time we ever see it
        if ensure_device_in_config(self.config, serial, model):
            save_config(self.config)
            self.last_config_mtime = os.path.getmtime(CONFIG_FILE)

        cfg = self.config["devices"][serial]
        name = cfg.get("name") or model or serial

        if not cfg.get("enabled", True):
            print(f"[Skip] {name} is disabled in config.json")
            return

        notify = cfg.get("notify", False)
        if notify:
            send_notification("Phone Connected", f"{name} plugged in over USB", "smartphone")

        # 1. Re-enable wireless ADB on this phone (survives until it reboots)
        if cfg.get("enable_tcpip", True):
            port = str(cfg.get("tcpip_port", 5555))
            print(f"[ADB] Enabling wireless adb on {name} (port {port})")
            try:
                subprocess.run(["adb", "-s", serial, "tcpip", port], timeout=15)
            except subprocess.TimeoutExpired:
                print("[ADB] tcpip timed out, continuing anyway")
            # `adb tcpip` restarts adbd on the phone, so the USB transport drops
            # and comes back. Wait until it's actually responsive again before
            # launching scrcpy, otherwise scrcpy connects mid-restart and fails.
            if self._wait_until_ready(serial):
                if notify:
                    send_notification("Wireless ADB Enabled", f"{name} is now reachable on port {port}", "network-wireless")
            else:
                print("[ADB] device did not become ready in time after tcpip")

        # 2. Launch scrcpy immediately over USB (targeted by serial).
        # We go through scrcpy_launch.py so the unlock + scrcpy_args behaviour
        # is shared with the WiFi desktop shortcut.
        if cfg.get("launch_scrcpy", True):
            existing = self.scrcpy_procs.get(serial)
            if existing and existing.poll() is None:
                print(f"[scrcpy] Already running for {name}, not launching another")
                return
            launcher = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scrcpy_launch.py")
            print(f"[scrcpy] Launching over USB for {name} ({serial})")
            try:
                self.scrcpy_procs[serial] = subprocess.Popen(["python3", launcher, serial])
            except FileNotFoundError:
                send_notification("scrcpy Not Found", "Install scrcpy to enable mirroring", "dialog-error", "critical")
                print("[scrcpy] launcher/scrcpy not found.")

    def on_phone_disconnect(self, serial):
        cfg = self.config.get("devices", {}).get(serial, {})
        name = cfg.get("name") or serial
        print(f"\n[Event] Phone Disconnected: {name} ({serial})")
        # scrcpy over USB exits on its own when the cable is pulled; just forget it
        self.scrcpy_procs.pop(serial, None)

if __name__ == "__main__":
    watcher = PhoneWatcher()
    watcher.start()
