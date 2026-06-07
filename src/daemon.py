import os
import re
import time
import signal
import threading
import subprocess
from core.config import load_config, save_config, ensure_device_in_config, CONFIG_FILE
from core.adb_monitor import AdbMonitor
from core.network_monitor import NetworkMonitor
import kdeconnect_notify

# A mirror seen running within this many seconds of a USB unplug is considered
# "was up at unplug" and gets reconnected over WiFi. Must exceed the adb monitor
# grace period so a session alive at unplug still counts when disconnect fires.
MIRROR_RECONNECT_WINDOW = 12

def send_notification(title, message, icon="smartphone", urgency="normal"):
    try:
        subprocess.run([
            "notify-send",
            "-a", "Linux-Android-Daemon",
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

        # Mirror state
        self.scrcpy_procs = {}        # serial -> Popen we launched (best-effort)
        self.phone_ip = {}            # serial -> last known LAN IP (for WiFi fallback)
        self.mirror_last_seen = {}    # serial -> monotonic time scrcpy was last seen up

        # Seed last-known IPs from config so a fresh daemon (e.g. after reboot)
        # already knows where to reach each phone over WiFi.
        for serial, dev in self.config.get("devices", {}).items():
            if dev.get("last_ip"):
                self.phone_ip[serial] = dev["last_ip"]

        # Tethering / network-failover state
        self.plugged = set()          # USB serials currently plugged in
        self.wifi_online = True       # does the PC have a real (non-phone) uplink?
        self.tether_active = False    # have we switched a phone into USB tethering?
        self.tether_phone = None      # serial of the phone we tethered through
        self.tether_lock = threading.Lock()

    def start(self):
        print("Starting Linux-Android-Daemon...")
        print("Waiting for a phone to be plugged in over USB.")
        self.wifi_online = self.net_monitor.has_real_uplink()
        print(f"[Net] Real uplink present at startup: {self.wifi_online}")
        self.adb_monitor.start()
        self.net_monitor.start()
        threading.Thread(target=self._mirror_watch, daemon=True).start()

        # Optional KDE Connect notification integration (click a phone
        # notification on the PC -> open scrcpy + expand the shade in the mirror)
        if self.config.get("defaults", {}).get("kdeconnect_notify", False):
            launcher = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scrcpy_launch.py")
            kdeconnect_notify.start(launcher)

        # Keep daemon alive
        while True:
            time.sleep(1)

    # ---- helpers ---------------------------------------------------------

    def _cfg_for(self, serial):
        """Effective config for a serial: defaults overlaid with its device entry."""
        cfg = dict(self.config.get("defaults", {}))
        cfg.update(self.config.get("devices", {}).get(serial, {}))
        return cfg

    def _reload_config_if_changed(self):
        if os.path.exists(CONFIG_FILE):
            current_mtime = os.path.getmtime(CONFIG_FILE)
            if current_mtime > self.last_config_mtime:
                self.config = load_config()
                self.last_config_mtime = current_mtime
                print("[Config] Reloaded changes from config.json")

    def _get_phone_ip(self, serial):
        """The phone's wlan0 IPv4, used to reconnect scrcpy over WiFi later."""
        try:
            out = subprocess.run(
                ["adb", "-s", serial, "shell", "ip", "-f", "inet", "addr", "show", "wlan0"],
                capture_output=True, text=True, timeout=10
            ).stdout
            m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", out)
            if m:
                return m.group(1)
        except Exception:
            pass
        return None

    def _scrcpy_pids(self, serial):
        """PIDs of the actual scrcpy *binary* targeting this phone (by serial or
        its LAN IP), regardless of who launched it. We deliberately do NOT match
        the `scrcpy_launch.py` wrapper: killing only the scrcpy binary lets the
        wrapper run its rotation-restore on exit. (Also excludes scrcpy's
        `adb shell ... scrcpy-server.jar` helper, which dies with the client.)"""
        patterns = [serial]
        ip = self.phone_ip.get(serial)
        if ip:
            patterns.append(ip)
        pids = []
        try:
            out = subprocess.run(["pgrep", "-af", "scrcpy"], capture_output=True, text=True).stdout
        except FileNotFoundError:
            return pids
        for line in out.splitlines():
            parts = line.split(None, 1)
            if len(parts) < 2:
                continue
            pid, cmd = parts
            if "scrcpy_launch.py" in cmd:
                continue  # the wrapper — leave it to restore rotation and exit
            is_client = re.search(r"(^|/|\s)scrcpy(\s|$)", cmd)
            if is_client and any(p in cmd for p in patterns):
                try:
                    pids.append(int(pid))
                except ValueError:
                    pass
        return pids

    def _scrcpy_running(self, serial):
        return bool(self._scrcpy_pids(serial))

    def _kill_scrcpy(self, serial):
        """Immediately SIGKILL every scrcpy session for this phone. No graceful
        SIGTERM-then-wait — we want the swap to a new transport to be instant.
        Returns True if any were running (so callers know to relaunch)."""
        pids = self._scrcpy_pids(serial)
        self.scrcpy_procs.pop(serial, None)
        if not pids:
            return False
        print(f"[scrcpy] Killing mirror for {serial} (pids {pids})")
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        return True

    def _launch_scrcpy(self, serial, target=None, name=None):
        """Launch scrcpy for a serial over `target` (a USB serial or ip:port) via
        scrcpy_launch.py, sharing the unlock + scrcpy_args path. Callers kill any
        existing session first, so this always launches."""
        target = target or serial
        name = name or serial
        launcher = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scrcpy_launch.py")
        print(f"[scrcpy] Launching for {name} over {target}")
        try:
            self.scrcpy_procs[serial] = subprocess.Popen(["python3", launcher, target])
            self.mirror_last_seen[serial] = time.monotonic()
        except FileNotFoundError:
            send_notification("scrcpy Not Found", "Install scrcpy to enable mirroring", "dialog-error", "critical")
            print("[scrcpy] launcher/scrcpy not found.")

    def _ensure_shortcut(self, serial):
        """Create/refresh a single "Phone" desktop launcher (app menu + Desktop)
        with StartupWMClass=scrcpy so KDE groups the window under the icon. The
        clone-vs-extended behaviour is driven by the `mode` config setting, not
        by separate shortcuts."""
        launcher = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scrcpy_launch.py")
        content = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Phone\n"
            "Comment=Mirror phone — USB if plugged in, else last known WiFi IP\n"
            f"Exec=/usr/bin/python3 {launcher} --auto {serial}\n"
            "Icon=smartphone\n"
            "Terminal=false\n"
            "StartupWMClass=scrcpy\n"
            "Categories=Utility;RemoteAccess;\n"
            "Keywords=phone;android;mirror;scrcpy;screen;\n"
        )
        for d in (os.path.expanduser("~/.local/share/applications"),
                  os.path.expanduser("~/Desktop")):
            if not os.path.isdir(d):
                continue
            # Remove the old extended-display variant shortcut if present
            old = os.path.join(d, f"phone-{serial}-display.desktop")
            if os.path.exists(old):
                try:
                    os.remove(old)
                    print(f"[Shortcut] Removed {old}")
                except OSError:
                    pass
            path = os.path.join(d, f"phone-{serial}.desktop")
            try:
                if not os.path.exists(path) or open(path).read() != content:
                    with open(path, "w") as f:
                        f.write(content)
                    os.chmod(path, 0o755)
                    if d.endswith("Desktop"):
                        subprocess.run(["gio", "set", path, "metadata::trusted", "true"], capture_output=True)
                    else:
                        subprocess.run(["update-desktop-database", d], capture_output=True)
                    print(f"[Shortcut] Wrote {path}")
            except Exception as e:
                print(f"[Shortcut] failed to write {path}: {e}")

    def _wait_until_ready(self, serial, timeout=20):
        """Block until the phone's USB transport is back and responsive.

        After `adb tcpip` or a USB-function switch the transport flaps; polling
        `adb shell true` confirms the device is genuinely ready before scrcpy.
        """
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

    def _mirror_watch(self):
        """Continuously record when a mirror is actually running for each known
        phone, so on_phone_disconnect can tell whether one was up at unplug."""
        while True:
            for serial in set(self.phone_ip) | set(self.plugged):
                if self._scrcpy_running(serial):
                    self.mirror_last_seen[serial] = time.monotonic()
            time.sleep(2)

    # ---- events ----------------------------------------------------------

    def on_phone_connect(self, serial, model):
        print(f"\n[Event] Phone Connected: {model or serial} ({serial})")
        self._reload_config_if_changed()

        if ensure_device_in_config(self.config, serial, model):
            save_config(self.config)
            self.last_config_mtime = os.path.getmtime(CONFIG_FILE)

        cfg = self.config["devices"][serial]
        name = cfg.get("name") or model or serial

        if not cfg.get("enabled", True):
            print(f"[Skip] {name} is disabled in config.json")
            return

        self.plugged.add(serial)
        self._ensure_shortcut(serial)
        notify = cfg.get("notify", False)
        if notify:
            send_notification("Phone Connected", f"{name} plugged in over USB", "smartphone")

        # Remember the LAN IP so we can move scrcpy to WiFi when unplugged, and
        # persist it to config so it survives a daemon/PC reboot.
        ip = self._get_phone_ip(serial)
        if ip:
            self.phone_ip[serial] = ip
            print(f"[Net] {name} LAN IP: {ip}")
            if self.config["devices"][serial].get("last_ip") != ip:
                self.config["devices"][serial]["last_ip"] = ip
                save_config(self.config)
                self.last_config_mtime = os.path.getmtime(CONFIG_FILE)
                print(f"[Config] Saved last_ip={ip} for {name}")

        # 1. Re-enable wireless ADB on this phone (survives until it reboots)
        if cfg.get("enable_tcpip", True):
            port = str(cfg.get("tcpip_port", 5555))
            print(f"[ADB] Enabling wireless adb on {name} (port {port})")
            try:
                subprocess.run(["adb", "-s", serial, "tcpip", port], timeout=15)
            except subprocess.TimeoutExpired:
                print("[ADB] tcpip timed out, continuing anyway")
            if self._wait_until_ready(serial):
                if notify:
                    send_notification("Wireless ADB Enabled", f"{name} is now reachable on port {port}", "network-wireless")
            else:
                print("[ADB] device did not become ready in time after tcpip")

        # 2. Plugging in over USB takes over the mirror: kill any running scrcpy
        # (e.g. a WiFi session) and reconnect over the faster USB cable.
        if cfg.get("launch_scrcpy", True):
            self._kill_scrcpy(serial)
            self._launch_scrcpy(serial, target=serial, name=name)

        # 3. If there's no real uplink, fall back to this phone's USB tethering
        self.evaluate_tethering()

    def on_phone_disconnect(self, serial):
        cfg = self._cfg_for(serial)
        name = cfg.get("name") or serial
        print(f"\n[Event] Phone Disconnected: {name} ({serial})")

        self.plugged.discard(serial)
        if self.tether_phone == serial:
            self.tether_active = False
            self.tether_phone = None

        # If a mirror was up at unplug, follow the phone onto WiFi.
        mirror_was_up = (time.monotonic() - self.mirror_last_seen.get(serial, 0)) <= MIRROR_RECONNECT_WINDOW
        ip = self.phone_ip.get(serial)
        if mirror_was_up and cfg.get("launch_scrcpy", True) and ip:
            target = f"{ip}:{cfg.get('tcpip_port', 5555)}"
            self._kill_scrcpy(serial)  # the USB session is dead; clear any leftovers
            print(f"[scrcpy] USB unplugged — reconnecting over WiFi ({target})")
            self._launch_scrcpy(serial, target=target, name=name)
        else:
            self._kill_scrcpy(serial)

        self.evaluate_tethering()

    def on_network_change(self, online):
        print(f"\n[Net] Real uplink {'available' if online else 'lost'}")
        self.wifi_online = online
        self.evaluate_tethering()

    # ---- tethering -------------------------------------------------------

    def evaluate_tethering(self):
        """Keep exactly one uplink active: prefer wifi/ethernet, fall back to the
        phone's USB tethering only when no real uplink exists."""
        with self.tether_lock:
            self._reload_config_if_changed()

            candidate = None
            for serial in self.plugged:
                if self._cfg_for(serial).get("tether_failover", False):
                    candidate = serial
                    break

            want_tether = candidate is not None and not self.wifi_online

            if want_tether and not self.tether_active:
                func = self._cfg_for(candidate).get("tether_function", "rndis")
                print(f"[Tether] No real uplink — enabling USB tethering ({func}) via {candidate}")
                self._switch_usb_function(candidate, ["svc", "usb", "setFunctions", func])
                self.tether_active = True
                self.tether_phone = candidate

            elif not want_tether and self.tether_active:
                serial = self.tether_phone
                print(f"[Tether] Real uplink available — disabling USB tethering via {serial}")
                if serial in self.plugged:
                    self._switch_usb_function(serial, ["svc", "usb", "setFunctions"])
                self.tether_active = False
                self.tether_phone = None

    def _switch_usb_function(self, serial, svc_cmd):
        """Run an `svc usb setFunctions ...` switch. Because that re-enumerates
        the USB device and kills a USB-bound scrcpy, stop any running mirror
        first and bring it back (over USB) afterwards if it was up."""
        was_running = self._scrcpy_running(serial)
        self._kill_scrcpy(serial)
        try:
            subprocess.run(["adb", "-s", serial] + ["shell"] + svc_cmd, timeout=15)
        except Exception as e:
            print(f"[Tether] usb function switch failed: {e}")
        if was_running and serial in self.plugged and self._cfg_for(serial).get("launch_scrcpy", True):
            self._wait_until_ready(serial)
            print(f"[scrcpy] Relaunching after USB mode switch ({serial})")
            self._launch_scrcpy(serial, target=serial, name=self._cfg_for(serial).get("name"))

if __name__ == "__main__":
    watcher = PhoneWatcher()
    watcher.start()
