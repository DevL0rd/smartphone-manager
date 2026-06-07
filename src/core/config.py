import json
import os

CONFIG_FILE = "config.json"

DEFAULTS = {
    "enabled": True,
    "enable_tcpip": True,
    "tcpip_port": 5555,
    "launch_scrcpy": True,
    "scrcpy_args": [],
    "notify": False,
    "unlock": True,
    "lock_pin": "",
    "lock_swipe": [540, 1800, 540, 600],
    "tether_failover": False,
    "tether_function": "rndis"
}

def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {"defaults": dict(DEFAULTS), "devices": {}}
    with open(CONFIG_FILE, "r") as f:
        try:
            config = json.load(f)
        except Exception:
            return {"defaults": dict(DEFAULTS), "devices": {}}
    # Make sure the top level keys always exist
    config.setdefault("defaults", dict(DEFAULTS))
    config.setdefault("devices", {})
    return config

def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)

def ensure_device_in_config(config, serial, model=""):
    changed = False
    if serial not in config["devices"]:
        # New phone seen for the first time: seed it from the defaults
        entry = dict(config.get("defaults", DEFAULTS))
        entry["name"] = model or serial
        config["devices"][serial] = entry
        changed = True
    return changed
