import time
import threading
import subprocess

class AdbMonitor:
    """Watches `adb` for phones connected over USB and fires callbacks when
    one is plugged in or unplugged.

    A grace period is used on disconnect because `adb tcpip` restarts adbd on
    the phone, which makes the USB transport vanish for ~1-2s. Without the
    grace period that blip would look like an unplug/replug and retrigger the
    whole connect routine in a loop.
    """

    def __init__(self, on_connect, on_disconnect, poll_interval=2, grace=6):
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self.poll_interval = poll_interval
        self.grace = grace
        self.last_seen = {}   # serial -> monotonic timestamp last present
        self.active = set()   # serials we've already fired on_connect for

    def start(self):
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def get_usb_devices(self):
        """Return {serial: model} for phones on a USB transport in 'device' state."""
        devices = {}
        try:
            out = subprocess.run(
                ["adb", "devices", "-l"],
                capture_output=True, text=True
            ).stdout
        except FileNotFoundError:
            print("[AdbMonitor] 'adb' not found in PATH.")
            return devices

        for line in out.splitlines()[1:]:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            serial, state = parts[0], parts[1] if len(parts) > 1 else ""
            if state != "device":
                continue
            # Only USB transports carry a 'usb:' descriptor; this skips the
            # network (ip:port) transports we may have already connected to.
            if "usb:" not in line:
                continue
            model = ""
            for p in parts:
                if p.startswith("model:"):
                    model = p.split(":", 1)[1].replace("_", " ")
            devices[serial] = model
        return devices

    def _loop(self):
        while True:
            current = self.get_usb_devices()
            now = time.monotonic()

            for serial, model in current.items():
                self.last_seen[serial] = now
                if serial not in self.active:
                    self.active.add(serial)
                    self.on_connect(serial, model)

            # Only treat a device as truly gone once it's been absent past the
            # grace window (rides out the adbd restart from `adb tcpip`).
            for serial in list(self.active):
                if serial not in current and now - self.last_seen.get(serial, 0) >= self.grace:
                    self.active.discard(serial)
                    self.on_disconnect(serial)

            time.sleep(self.poll_interval)
