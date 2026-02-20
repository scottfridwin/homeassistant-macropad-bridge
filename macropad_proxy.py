#!/usr/bin/env python3
import requests
import evdev
from evdev import InputDevice, categorize, ecodes
import glob
import select
import sys
import os

# --------------------------
# LOAD ENVIRONMENT VARIABLES
# --------------------------
HA_URL = os.environ.get("HA_URL")
HA_TOKEN = os.environ.get("HA_TOKEN")
EVENT_TYPE = os.environ.get("EVENT_TYPE", "macropad_key")

MACROPAD_VID = os.environ.get("MACROPAD_VID")
MACROPAD_PID = os.environ.get("MACROPAD_PID")
DEVICE_NAME = os.environ.get("DEVICE_NAME", "macropad")

# --------------------------
# VALIDATE REQUIRED VARS
# --------------------------
required_vars = {
    "HA_URL": HA_URL,
    "HA_TOKEN": HA_TOKEN,
    "MACROPAD_VID": MACROPAD_VID,
    "MACROPAD_PID": MACROPAD_PID,
}

missing = [k for k, v in required_vars.items() if not v]

if missing:
    print(f"Missing required environment variables: {', '.join(missing)}")
    sys.exit(1)

# --------------------------
# FIND MACROPAD DEVICES
# --------------------------


def find_macropad_devices():
    pattern = f"/dev/input/by-id/usb-{MACROPAD_VID}_{MACROPAD_PID}*-event-kbd"
    matches = glob.glob(pattern)

    devices = []
    for path in matches:
        try:
            devices.append(InputDevice(path))
        except Exception as e:
            print(f"Failed to open {path}: {e}")

    return devices


devices = find_macropad_devices()

if not devices:
    print(f"Macropad with VID={MACROPAD_VID} PID={MACROPAD_PID} not found")
    sys.exit(1)

for dev in devices:
    print(f"Listening on {dev.path} ({dev.name})")

# --------------------------
# SETUP HA HEADERS
# --------------------------
headers = {
    "Authorization": f"Bearer {HA_TOKEN}",
    "Content-Type": "application/json",
}

# --------------------------
# MULTI-DEVICE EVENT LOOP
# --------------------------
while True:
    r, _, _ = select.select(devices, [], [])

    for device in r:
        for event in device.read():
            if event.type == ecodes.EV_KEY:
                key_event = categorize(event)

                if key_event.keystate == key_event.key_down:
                    key_name = key_event.keycode

                    if isinstance(key_name, list):
                        key_name = "_".join(key_name)

                    print(f"{device.path} → {key_name}")

                    payload = {
                        "event_type": EVENT_TYPE,
                        "event_data": {
                            "key": key_name,
                            "device": DEVICE_NAME,
                            "source": device.path,
                        },
                    }

                    try:
                        response = requests.post(
                            f"{HA_URL}/api/events/{EVENT_TYPE}",
                            headers=headers,
                            json=payload,
                            timeout=5,
                        )
                        if response.status_code != 200:
                            print(
                                f"HA returned {response.status_code}: {response.text}")
                    except Exception as e:
                        print(f"Error sending event to HA: {e}")
