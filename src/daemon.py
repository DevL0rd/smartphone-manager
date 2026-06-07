import os
import time
import threading
import subprocess
from core.config import load_config, save_config, ensure_device_in_config, CONFIG_FILE
from core.adb_monitor import AdbMonitor
from core.network_monitor import NetworkMonitor

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
        self.net_monitor = NetworkMonitor(self.on_network_change)

        # State: track the scrcpy process we spawned per phone
        self.scrcpy_procs = {}

        # Tethering / network-failover state
        self.plugged = set()          # USB serials currently plugged in
        self.wifi_online = True       # does the PC have a real (non-phone) uplink?
        self.tether_active = False    # have we switched a phone into USB tethering?
        self.tether_phone = None      # serial of the phone we tethered through
        self.tether_lock = threading.Lock()

    def start(self):
        print("Starting Smartphone Manager...")
        print("Waiting for a phone to be plugged in over USB.")
        self.wifi_online = self.net_monitor.has_real_uplink()
        print(f"[Net] Real uplink present at startup: {self.wifi_online}")
        self.adb_monitor.start()
        self.net_monitor.start()

        # Keep daemon alive
        while True:
            time.sleep(1)

    def _cfg_for(self, serial):
        """Effective config for a serial: defaults overlaid with its device entry."""
        cfg = dict(self.config.get("defaults", {}))
        cfg.update(self.config.get("devices", {}).get(serial, {}))
        return cfg

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

        self.plugged.add(serial)
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
        if cfg.get("launch_scrcpy", True):
            self._launch_scrcpy(serial, name)

        # 3. If there's no real uplink, fall back to this phone's USB tethering
        self.evaluate_tethering()

    def _scrcpy_alive(self, serial):
        proc = self.scrcpy_procs.get(serial)
        return proc is not None and proc.poll() is None

    def _launch_scrcpy(self, serial, name=None):
        """Launch scrcpy for a serial via scrcpy_launch.py (shared unlock +
        scrcpy_args path). No-op if it's already running."""
        name = name or serial
        if self._scrcpy_alive(serial):
            print(f"[scrcpy] Already running for {name}, not launching another")
            return
        launcher = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scrcpy_launch.py")
        print(f"[scrcpy] Launching for {name} ({serial})")
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
        self.plugged.discard(serial)
        # The USB tether interface vanishes with the cable; reset our state
        if self.tether_phone == serial:
            self.tether_active = False
            self.tether_phone = None
        self.evaluate_tethering()

    def on_network_change(self, online):
        print(f"\n[Net] Real uplink {'available' if online else 'lost'}")
        self.wifi_online = online
        self.evaluate_tethering()

    def evaluate_tethering(self):
        """Keep exactly one uplink active: prefer wifi/ethernet, fall back to the
        phone's USB tethering only when no real uplink exists."""
        with self.tether_lock:
            self._reload_config_if_changed()

            # Find a plugged-in phone that opted into tethering failover
            candidate = None
            for serial in self.plugged:
                if self._cfg_for(serial).get("tether_failover", False):
                    candidate = serial
                    break

            want_tether = candidate is not None and not self.wifi_online

            if want_tether and not self.tether_active:
                func = self._cfg_for(candidate).get("tether_function", "rndis")
                print(f"[Tether] No real uplink — enabling USB tethering ({func}) via {candidate}")
                # The USB function switch re-enumerates the device, so cleanly
                # stop scrcpy first and bring it back afterwards.
                was_running = self._stop_scrcpy(candidate)
                try:
                    subprocess.run(["adb", "-s", candidate, "shell", "svc", "usb", "setFunctions", func], timeout=15)
                    self.tether_active = True
                    self.tether_phone = candidate
                except Exception as e:
                    print(f"[Tether] enable failed: {e}")
                self._relaunch_after_switch(candidate, was_running)

            elif not want_tether and self.tether_active:
                serial = self.tether_phone
                print(f"[Tether] Real uplink available — disabling USB tethering via {serial}")
                was_running = self._stop_scrcpy(serial)
                try:
                    if serial in self.plugged:
                        subprocess.run(["adb", "-s", serial, "shell", "svc", "usb", "setFunctions"], timeout=15)
                except Exception as e:
                    print(f"[Tether] disable failed: {e}")
                self.tether_active = False
                self.tether_phone = None
                self._relaunch_after_switch(serial, was_running)

    def _stop_scrcpy(self, serial):
        """Terminate the scrcpy session for a serial. Returns True if one was
        actually running (so the caller knows whether to relaunch it)."""
        proc = self.scrcpy_procs.pop(serial, None)
        if proc and proc.poll() is None:
            print(f"[scrcpy] Stopping before USB mode switch ({serial})")
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            return True
        return False

    def _relaunch_after_switch(self, serial, was_running):
        """If scrcpy was running before a USB mode switch, wait for the device to
        re-enumerate and come back, then relaunch it. Manually-closed sessions
        (was_running False) are left alone."""
        if not was_running or serial not in self.plugged:
            return
        cfg = self._cfg_for(serial)
        if not cfg.get("launch_scrcpy", True):
            return
        self._wait_until_ready(serial)
        print(f"[scrcpy] Relaunching after USB mode switch ({serial})")
        self._launch_scrcpy(serial, cfg.get("name"))

if __name__ == "__main__":
    watcher = PhoneWatcher()
    watcher.start()
