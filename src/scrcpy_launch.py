#!/usr/bin/env python3
"""Unlock a phone (optional, via adb) and then launch scrcpy against it.

This is the single entry point used for *every* scrcpy launch so the unlock
behaviour is identical whether the daemon triggers it on a USB plug-in or the
WiFi desktop shortcut runs it. The target may be a USB serial or a network
`ip:port`; either way it is resolved to the phone's hardware serial so the right
config entry (and PIN) is found.

Usage:
    scrcpy_launch.py <adb-target> [extra scrcpy args...]
    e.g. scrcpy_launch.py RFCY8112TKV
         scrcpy_launch.py 192.168.50.3:5555 --turn-screen-off

    scrcpy_launch.py --auto [serial] [extra scrcpy args...]
        Pick the transport automatically: use USB if the phone is plugged in,
        otherwise fall back to its saved last_ip from config (over WiFi).
"""
import os
import sys
import json
import time
import subprocess

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(REPO_DIR, "config.json")

# Lock-state tokens seen in `dumpsys window` on a locked device
LOCK_TOKENS = ("isKeyguardShowing=true", "mDreamingLockscreen=true")

def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except Exception:
        return {"defaults": {}, "devices": {}}

def adb(target, *args, timeout=10, capture=False):
    cmd = ["adb"]
    if target:
        cmd += ["-s", target]
    cmd += list(args)
    return subprocess.run(cmd, capture_output=capture, text=True, timeout=timeout)

def get_serial(target):
    """Resolve any adb target (usb serial or ip:port) to the hardware serial.

    `adb get-serialno` returns the ip:port for network transports, so we read
    ro.serialno instead — it's the hardware serial on USB and WiFi alike, which
    keeps both paths pointed at the same config entry.
    """
    try:
        out = adb(target, "shell", "getprop", "ro.serialno", capture=True, timeout=10).stdout.strip()
        if out:
            return out
    except Exception:
        pass
    return target

def usb_present(serial):
    """True if `serial` is connected on a USB transport in 'device' state."""
    try:
        out = adb(None, "devices", "-l", capture=True, timeout=10).stdout
    except Exception:
        return False
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[0] == serial and parts[1] == "device" and "usb:" in line:
            return True
    return False

def resolve_auto_target(serial, config):
    """Choose the best transport for a phone: USB if plugged in, else the saved
    last_ip over WiFi."""
    if serial and usb_present(serial):
        print(f"[auto] {serial} is on USB — using the cable")
        return serial
    cfg = dict(config.get("defaults", {}))
    cfg.update(config.get("devices", {}).get(serial, {}))
    ip = cfg.get("last_ip")
    if ip:
        target = f"{ip}:{cfg.get('tcpip_port', 5555)}"
        print(f"[auto] {serial} not on USB — trying last known WiFi address {target}")
        return target
    print(f"[auto] No USB and no saved last_ip for {serial}; trying serial anyway")
    return serial

def is_locked(target):
    try:
        out = adb(target, "shell", "dumpsys", "window", capture=True, timeout=10).stdout
        return any(tok in out for tok in LOCK_TOKENS)
    except Exception:
        return False

def unlock(target, cfg):
    """Wake the phone and, if it's on a secure lock, type the PIN to unlock."""
    pin = str(cfg.get("lock_pin", "") or "")
    if not cfg.get("unlock", False) or not pin:
        return

    # Wake the screen (handles screen-off / always-on-display)
    adb(target, "shell", "input", "keyevent", "224")  # KEYCODE_WAKEUP
    time.sleep(0.5)

    if not is_locked(target):
        return  # already unlocked / no keyguard, don't type into a focused field

    # Press SPACE to bring up the PIN entry (no swipe needed)
    adb(target, "shell", "input", "keyevent", "62")  # KEYCODE_SPACE
    time.sleep(0.5)

    # Enter the PIN / password and submit
    adb(target, "shell", "input", "text", pin)
    adb(target, "shell", "input", "keyevent", "66")  # KEYCODE_ENTER

def main():
    args = sys.argv[1:]
    # --display is a launcher-level flag (not a scrcpy flag): mirror onto a NEW
    # virtual display (extended-display / DeX-style) instead of cloning the screen.
    display_mode = "--display" in args
    args = [a for a in args if a != "--display"]
    if not args:
        print("usage: scrcpy_launch.py <adb-target>|--auto [serial] [--display] [extra scrcpy args...]")
        return 1

    config = load_config()

    if args[0] == "--auto":
        rest = args[1:]
        # Optional explicit serial; otherwise use the single configured device
        serial = None
        if rest and not rest[0].startswith("-"):
            serial, rest = rest[0], rest[1:]
        if not serial:
            devices = list(config.get("devices", {}).keys())
            serial = devices[0] if devices else None
        if not serial:
            print("[auto] No serial given and no devices in config.json")
            return 1
        target = resolve_auto_target(serial, config)
        extra = rest
    else:
        target = args[0]
        extra = args[1:]

    # Network targets need an explicit connect before anything else
    if ":" in target:
        try:
            adb(None, "connect", target, timeout=10)
        except Exception:
            pass

    serial = get_serial(target)
    defaults = config.get("defaults", {})
    dev = config.get("devices", {}).get(serial, {})
    cfg = dict(defaults)
    cfg.update(dev)

    try:
        unlock(target, cfg)
    except Exception as e:
        print(f"[unlock] failed: {e}")

    # scrcpy_args from defaults are the baseline applied to every phone; a
    # device's own scrcpy_args are appended on top (not a replacement).
    scrcpy_args = list(defaults.get("scrcpy_args", []))
    for a in dev.get("scrcpy_args", []):
        if a not in scrcpy_args:
            scrcpy_args.append(a)

    # Extended-display mode: spin up a new virtual display the size of the phone
    # screen and live-resize it to the window. With no launcher specified the
    # phone decides what fills it (DeX on Samsung); set "display_launcher" in
    # config to force a specific launcher app (a home screen without DeX).
    if display_mode:
        scrcpy_args = ["--new-display", "--flex-display"] + scrcpy_args
        launcher_app = str(cfg.get("display_launcher", "") or "")
        if launcher_app:
            scrcpy_args.append(f"--start-app={launcher_app}")

    # Hand off to scrcpy, bound to this exact target.
    scrcpy_cmd = ["scrcpy", "-s", target] + list(extra) + scrcpy_args
    print(f"[scrcpy] exec: {' '.join(scrcpy_cmd)}")
    os.execvp("scrcpy", scrcpy_cmd)

if __name__ == "__main__":
    sys.exit(main())
