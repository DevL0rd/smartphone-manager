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

    # Swipe up to reveal the PIN bouncer (coords overridable for foldables)
    sx, sy, ex, ey = cfg.get("lock_swipe", [540, 1800, 540, 600])
    adb(target, "shell", "input", "swipe", str(sx), str(sy), str(ex), str(ey), "120")
    time.sleep(0.6)

    # Enter the PIN / password and submit
    adb(target, "shell", "input", "text", pin)
    adb(target, "shell", "input", "keyevent", "66")  # KEYCODE_ENTER

def main():
    args = sys.argv[1:]
    if not args:
        print("usage: scrcpy_launch.py <adb-target> [extra scrcpy args...]")
        return 1

    target = args[0]
    extra = args[1:]

    # Network targets need an explicit connect before anything else
    if ":" in target:
        try:
            adb(None, "connect", target, timeout=10)
        except Exception:
            pass

    config = load_config()
    serial = get_serial(target)
    cfg = dict(config.get("defaults", {}))
    cfg.update(config.get("devices", {}).get(serial, {}))

    try:
        unlock(target, cfg)
    except Exception as e:
        print(f"[unlock] failed: {e}")

    # Hand off to scrcpy, bound to this exact target. Config scrcpy_args are
    # applied here so they take effect on every launch path.
    scrcpy_cmd = ["scrcpy", "-s", target] + list(extra) + list(cfg.get("scrcpy_args", []))
    os.execvp("scrcpy", scrcpy_cmd)

if __name__ == "__main__":
    sys.exit(main())
