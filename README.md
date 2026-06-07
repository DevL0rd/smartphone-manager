# Smartphone Manager

A small daemon that watches for an Android phone being plugged in over USB. The
moment one connects it:

1. **Re-enables wireless ADB** (`adb tcpip 5555`) so the phone stays reachable
   over WiFi until its next reboot.
2. **Launches scrcpy immediately** to mirror the phone — over the USB cable
   (faster and lag-free while it's plugged in).

It can also **auto-unlock** the phone (type your PIN over adb) on every scrcpy
launch — both the USB plug-in above and the WiFi desktop shortcut.

This pairs nicely with a WiFi scrcpy shortcut: plug in once to "arm" wireless
adb, then mirror over WiFi later with the cable unplugged.

This is mostly for myself, but in case my friends use it, here are the instructions:

## Requirements
- `adb` (android-tools)
- `scrcpy`
- `notify-send` (libnotify) for the optional desktop popups
- USB debugging enabled on the phone, with this computer authorized

## Installation
```bash
./install.sh
```
This generates a local `config.json` from `config.example.json` (git-ignored,
since it holds your device list and lock PIN) and starts the systemd user
service.

## Uninstallation
To disable the daemon and clean up the systemd service, run:
```bash
./uninstall.sh
```

## How it works
The daemon polls `adb devices` for phones on a **USB** transport. When a new one
appears it runs the connect routine; network (WiFi) transports are ignored. A
short grace period covers the brief adbd restart caused by `adb tcpip`, so that
blip is not mistaken for an unplug/replug loop.

Every scrcpy launch (the daemon and the WiFi shortcut) goes through
`src/scrcpy_launch.py`, which optionally unlocks the phone and then starts
scrcpy. You can also run it by hand:
```bash
python3 src/scrcpy_launch.py 192.168.50.3:5555   # network target
python3 src/scrcpy_launch.py RFCY8112TKV          # usb serial
```

Logs:
```bash
journalctl --user -u scrcpy-autolaunch.service -f
```

## Configuration (`config.json`)
`config.json` is **git-ignored** and generated from `config.example.json` on
install. Phones are then **automatically added** the first time you plug them in
— keyed by their ADB serial, seeded from `defaults`. You don't need to type
anything out by hand.

Each phone entry supports:
* **`name`**: Friendly name shown in notifications.
* **`enabled`**: Set to `false` to ignore this phone entirely.
* **`enable_tcpip`**: Run `adb tcpip <port>` on plug-in to re-arm wireless adb.
* **`tcpip_port`**: Port for wireless adb (default `5555`).
* **`launch_scrcpy`**: Launch scrcpy over USB on plug-in.
* **`scrcpy_args`**: Extra arguments passed to scrcpy on every launch, e.g.
  `["--turn-screen-off", "--stay-awake"]` or `["--max-size", "1920"]`.
* **`notify`**: Show desktop notifications (default `false`).
* **`unlock`**: Auto-unlock the phone before scrcpy starts (default `true`,
  but only acts when `lock_pin` is set).
* **`lock_pin`**: Your numeric PIN / password. Left blank by default so nothing
  is typed. The file is git-ignored, so this stays local. PIN/password only —
  pattern locks aren't supported.
* **`lock_swipe`**: `[startX, startY, endX, endY]` swipe used to reveal the PIN
  bouncer. Defaults to a centered swipe-up for 1080-wide; adjust for foldables /
  other resolutions if needed.

The `defaults` block at the top of the file seeds every newly-discovered phone.

Example:
```json
{
    "defaults": {
        "enabled": true,
        "enable_tcpip": true,
        "tcpip_port": 5555,
        "launch_scrcpy": true,
        "scrcpy_args": [],
        "notify": false,
        "unlock": true,
        "lock_pin": "",
        "lock_swipe": [540, 1800, 540, 600]
    },
    "devices": {
        "RFCY8112TKV": {
            "name": "Galaxy Z Fold",
            "enabled": true,
            "scrcpy_args": ["--stay-awake"],
            "lock_pin": "1234"
        }
    }
}
```

The config is re-read automatically whenever you edit and save it.

## Security note
`lock_pin` is stored in plaintext in the local (git-ignored) `config.json`.
Anyone with read access to your home directory can read it. Don't commit it and
don't enable this on a shared machine.
