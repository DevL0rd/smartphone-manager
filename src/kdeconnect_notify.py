"""Optional KDE Connect notification integration.

Watches KDE Connect's `notificationPosted` D-Bus signal for the paired phone and
posts our own *clickable* desktop notification. Clicking it opens scrcpy (if not
already running) via scrcpy_launch.py and expands the phone's notification shade
in the mirror — so a single tap inside scrcpy opens the app to that message
(KDE Connect can't fire the notification's tap action remotely itself).

Implemented with plain subprocesses (gdbus monitor + qdbus + notify-send) so it
needs no extra Python dependencies. Started as a daemon thread when the
`kdeconnect_notify` config option is enabled.
"""
import os
import re
import shutil
import threading
import subprocess

KDECONNECT = "org.kde.kdeconnect"
NOTIF_IFACE = "org.kde.kdeconnect.device.notifications"

def _qdbus_bin():
    return shutil.which("qdbus6") or shutil.which("qdbus") or shutil.which("qdbus-qt6")

def _qdbus(*args, timeout=10):
    db = _qdbus_bin()
    if not db:
        return ""
    try:
        return subprocess.run([db, *args], capture_output=True, text=True, timeout=timeout).stdout.strip()
    except Exception:
        return ""

def _device_id():
    """The first reachable + paired KDE Connect device."""
    out = _qdbus(KDECONNECT, "/modules/kdeconnect",
                 "org.kde.kdeconnect.daemon.devices", "true", "true")
    ids = out.split()
    return ids[0] if ids else None

def _notif_prop(dev, nid, prop):
    return _qdbus(KDECONNECT, f"/modules/kdeconnect/devices/{dev}/notifications/{nid}",
                  f"{NOTIF_IFACE}.notification.{prop}")

def _adb_target():
    """USB serial if the phone is plugged in, otherwise its network ip:port."""
    try:
        out = subprocess.run(["adb", "devices", "-l"], capture_output=True, text=True).stdout
    except Exception:
        return None
    usb = net = None
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2 or parts[1] != "device":
            continue
        if "usb:" in line:
            usb = parts[0]
        elif ":" in parts[0]:
            net = parts[0]
    return usb or net

def _scrcpy_running():
    return subprocess.run(["pgrep", "-x", "scrcpy"], capture_output=True).returncode == 0

def _open_and_expand(launcher):
    """Open scrcpy if it isn't already, then expand the phone's notification
    shade so the notification is right there to tap in the mirror."""
    if not _scrcpy_running():
        try:
            subprocess.Popen(["python3", launcher, "--auto"])
        except Exception as e:
            print(f"[kdeconnect] failed to launch scrcpy: {e}")
    target = _adb_target()
    if target:
        try:
            subprocess.run(["adb", "-s", target, "shell", "cmd", "statusbar", "expand-notifications"], timeout=10)
        except Exception:
            pass

def _handle(dev, nid, launcher):
    app = _notif_prop(dev, nid, "appName")
    title = _notif_prop(dev, nid, "title")
    text = _notif_prop(dev, nid, "text")
    summary = (f"{app}: {title}".strip(": ").strip()) or app or "Phone notification"
    try:
        # --action implies --wait: blocks until clicked/closed; prints the action
        # key ("default" = the notification body was clicked) to stdout.
        res = subprocess.run(
            ["notify-send", "-a", "Phone", "-i", "smartphone",
             "--action=default=Open in scrcpy", summary, text or ""],
            capture_output=True, text=True
        )
        if res.stdout.strip() == "default":
            _open_and_expand(launcher)
    except Exception as e:
        print(f"[kdeconnect] notify failed: {e}")

def _loop(dev, launcher):
    path = f"/modules/kdeconnect/devices/{dev}/notifications"
    print(f"[kdeconnect] watching notifications for device {dev}")
    while True:
        try:
            proc = subprocess.Popen(
                ["gdbus", "monitor", "-e", "-d", KDECONNECT, "-o", path],
                stdout=subprocess.PIPE, text=True
            )
            for line in proc.stdout:
                if "notificationPosted" not in line:
                    continue
                # gdbus formats a single-string signal arg as ('value',) — grab
                # the first single-quoted token, regardless of trailing comma.
                m = re.search(r"notificationPosted \(.*?'([^']*)'", line)
                if not m:
                    continue
                nid = m.group(1)
                print(f"[kdeconnect] notificationPosted: {nid}")
                threading.Thread(target=_handle, args=(dev, nid, launcher), daemon=True).start()
        except Exception as e:
            print(f"[kdeconnect] monitor error: {e}")
        import time
        time.sleep(5)  # gdbus monitor died; retry

def start(launcher):
    """Start the KDE Connect notification listener in a background thread."""
    if not _qdbus_bin() or not shutil.which("gdbus"):
        print("[kdeconnect] qdbus/gdbus not available; integration disabled")
        return
    dev = _device_id()
    if not dev:
        print("[kdeconnect] no paired/reachable device; integration disabled")
        return
    threading.Thread(target=_loop, args=(dev, launcher), daemon=True).start()
