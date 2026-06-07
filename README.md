# Linux-Android-Daemon

A small daemon that watches for an Android phone being plugged in over USB. The
moment one connects it:

1. **Re-enables wireless ADB** (`adb tcpip 5555`) so the phone stays reachable
   over WiFi until its next reboot.
2. **Launches scrcpy immediately** to mirror the phone — over the USB cable
   (faster and lag-free while it's plugged in).

It can also **auto-unlock** the phone (type your PIN over adb) on every scrcpy
launch — both the USB plug-in above and the WiFi desktop shortcut.

The **mirror follows the connection**: plugging in over USB takes over any
running scrcpy (even one launched outside this tool, like the WiFi shortcut) and
re-mirrors over the faster cable; unplugging hands the mirror back to WiFi using
the phone's LAN IP captured while it was plugged in.

And it does **USB-tethering failover**: if the PC has no real wifi/ethernet
uplink, the plugged-in phone is switched into USB tethering so the PC gets
internet over the cable; when wifi/ethernet comes back, tethering is turned off
again. Only one uplink is ever active at a time.

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

Every scrcpy launch (the daemon and the desktop shortcut) goes through
`src/scrcpy_launch.py`, which optionally unlocks the phone and then starts
scrcpy. You can also run it by hand:
```bash
python3 src/scrcpy_launch.py 192.168.50.3:5555   # network target
python3 src/scrcpy_launch.py RFCY8112TKV          # usb serial
python3 src/scrcpy_launch.py --auto RFCY8112TKV   # smart: USB if plugged, else saved last_ip
```

The desktop shortcut uses `--auto`: it mirrors over USB when the phone is
plugged in, and otherwise connects over WiFi using the saved `last_ip`.

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
* **`scrcpy_args`**: Arguments passed to scrcpy. The **`defaults`** block ships
  with `["--turn-screen-off", "--stay-awake"]` so every phone mirrors with the
  physical screen blanked and the device kept awake. A device's own
  `scrcpy_args` are **appended** to (not a replacement for) the defaults, so you
  can add per-phone extras like `["--max-size", "1920"]` without losing the
  baseline.
* **`notify`**: Show desktop notifications (default `false`).
* **`unlock`**: Auto-unlock the phone before scrcpy starts (default `true`,
  but only acts when `lock_pin` is set). Wakes the screen, presses Space to
  bring up the PIN pad, types the PIN, and hits Enter.
* **`lock_pin`**: Your numeric PIN / password. Left blank by default so nothing
  is typed. The file is git-ignored, so this stays local. PIN/password only —
  pattern locks aren't supported.
* **`tether_failover`**: When `true`, if the PC loses its wifi/ethernet uplink
  this phone is switched into USB tethering so the PC keeps internet over the
  cable; tethering is turned off again when a real uplink returns (default
  `false`).
* **`tether_function`**: USB function used for tethering — `rndis` (default) or
  `ncm`.
* **`last_ip`**: The phone's LAN IP, **auto-updated on every USB connect** so it's
  remembered across daemon/PC reboots. Used by the WiFi fallback and the
  smart-connect shortcut. You don't normally set this by hand.

## USB-tethering failover
With `tether_failover` enabled, a background monitor watches NetworkManager:

* **No real uplink + phone plugged in** → `svc usb setFunctions rndis`, the PC
  gets internet over USB.
* **Real uplink returns** → tethering is disabled, traffic goes back over
  wifi/ethernet.

Only one uplink is ever active at a time, so there's no route-priority guessing.

Switching the USB function **re-enumerates** the USB device, which would drop a
USB-bound scrcpy session. So if scrcpy is running when a switch happens, the
daemon **stops it cleanly, performs the switch, waits for the device to come
back, and relaunches it** — the mirror briefly blips (a few seconds) but returns
on its own. Manually closed scrcpy windows are not relaunched.

The `defaults` block at the top of the file seeds every newly-discovered phone.

Example:
```json
{
    "defaults": {
        "enabled": true,
        "enable_tcpip": true,
        "tcpip_port": 5555,
        "launch_scrcpy": true,
        "scrcpy_args": ["--turn-screen-off", "--stay-awake"],
        "notify": false,
        "unlock": true,
        "lock_pin": "",
        "tether_failover": false,
        "tether_function": "rndis",
        "last_ip": ""
    },
    "devices": {
        "RFCY8112TKV": {
            "name": "Galaxy Z Fold",
            "enabled": true,
            "scrcpy_args": ["--max-size", "1920"],
            "lock_pin": "1234",
            "tether_failover": true
        }
    }
}
```

The config is re-read automatically whenever you edit and save it.

## Security note
`lock_pin` is stored in plaintext in the local (git-ignored) `config.json`.
Anyone with read access to your home directory can read it. Don't commit it and
don't enable this on a shared machine.
