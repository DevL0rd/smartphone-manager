# scrcpy Autolaunch

A small daemon that watches for an Android phone being plugged in over USB. The
moment one connects it:

1. **Re-enables wireless ADB** (`adb tcpip 5555`) so the phone stays reachable
   over WiFi until its next reboot.
2. **Launches scrcpy immediately** to mirror the phone — over the USB cable
   (faster and lag-free while it's plugged in).

This pairs nicely with a WiFi scrcpy shortcut: plug in once to "arm" wireless
adb, then mirror over WiFi later with the cable unplugged.

This is mostly for myself, but in case my friends use it, here are the instructions:

## Requirements
- `adb` (android-tools)
- `scrcpy`
- `notify-send` (libnotify) for the desktop popups
- USB debugging enabled on the phone, with this computer authorized

## Installation
```bash
./install.sh
```

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

Logs:
```bash
journalctl --user -u scrcpy-autolaunch.service -f
```

## Configuration (`config.json`)
Phones are **automatically added** to `config.json` the first time you plug them
in — keyed by their ADB serial. You don't need to type anything out by hand;
just connect the phone and an entry appears, seeded from `defaults`.

Each phone entry supports:
* **`name`**: Friendly name shown in notifications.
* **`enabled`**: Set to `false` to ignore this phone entirely.
* **`enable_tcpip`**: Run `adb tcpip <port>` on plug-in to re-arm wireless adb.
* **`tcpip_port`**: Port for wireless adb (default `5555`).
* **`launch_scrcpy`**: Launch scrcpy over USB on plug-in.
* **`scrcpy_args`**: Extra arguments passed to scrcpy, e.g.
  `["--turn-screen-off", "--stay-awake"]` or `["--max-size", "1920"]`.

The `defaults` block at the top of the file seeds every newly-discovered phone.

Example:
```json
{
    "defaults": {
        "enabled": true,
        "enable_tcpip": true,
        "tcpip_port": 5555,
        "launch_scrcpy": true,
        "scrcpy_args": []
    },
    "devices": {
        "RFCY8112TKV": {
            "name": "Galaxy Z Fold",
            "enabled": true,
            "enable_tcpip": true,
            "tcpip_port": 5555,
            "launch_scrcpy": true,
            "scrcpy_args": ["--stay-awake"]
        }
    }
}
```

The config is re-read automatically whenever you edit and save it.
