#!/usr/bin/env python3
"""Forward key presses from a USB macropad to Home Assistant as events."""

import contextlib
import glob
import json
import logging
import os
import select
import signal
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from evdev import InputDevice, categorize, ecodes

LOG = logging.getLogger("macropad")
HEARTBEAT_FILE = os.environ.get("HEARTBEAT_FILE", "/tmp/macropad-bridge.heartbeat")  # noqa: S108


@dataclass(frozen=True)
class Config:
    ha_url: str
    ha_token: str
    vid: str
    pid: str
    event_type: str = "macropad_key"
    device_name: str = "macropad"
    input_dir: str = "/dev/input/by-id"
    rescan_seconds: float = 5.0

    @classmethod
    def from_env(cls, env=os.environ):
        token = env.get("HA_TOKEN", "")
        token_file = env.get("HA_TOKEN_FILE")
        if token_file:
            token = Path(token_file).read_text().strip()

        required = {
            "HA_URL": env.get("HA_URL", ""),
            "HA_TOKEN or HA_TOKEN_FILE": token,
            "MACROPAD_VID": env.get("MACROPAD_VID", ""),
            "MACROPAD_PID": env.get("MACROPAD_PID", ""),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(f"Missing required settings: {', '.join(missing)}")

        ha_url = required["HA_URL"].rstrip("/")
        if not ha_url.startswith(("http://", "https://")):
            raise ValueError("HA_URL must start with http:// or https://")

        return cls(
            ha_url=ha_url,
            ha_token=token,
            vid=required["MACROPAD_VID"],
            pid=required["MACROPAD_PID"],
            event_type=env.get("EVENT_TYPE", cls.event_type),
            device_name=env.get("DEVICE_NAME", cls.device_name),
            input_dir=env.get("INPUT_DIR", cls.input_dir),
            rescan_seconds=float(env.get("RESCAN_SECONDS", cls.rescan_seconds)),
        )

    @property
    def device_glob(self):
        return os.path.join(self.input_dir, f"usb-{self.vid}_{self.pid}*-event-kbd")


def key_name(keycode):
    """evdev reports keys with several names as a tuple (older versions: a list)."""
    if isinstance(keycode, (list, tuple)):
        return "_".join(keycode)
    return str(keycode)


def build_payload(config, key, source):
    # Home Assistant automations read trigger.event.data.event_data.key, so keep this nesting.
    return {
        "event_type": config.event_type,
        "event_data": {"key": key, "device": config.device_name, "source": source},
    }


def post_event(config, payload, urlopen=urllib.request.urlopen):
    request = urllib.request.Request(  # noqa: S310 - scheme validated in Config.from_env
        f"{config.ha_url}/api/events/{config.event_type}",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {config.ha_token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        return response.status


class Bridge:
    def __init__(
        self,
        config,
        open_device=lambda path: InputDevice(path, readonly=True),
        post=post_event,
        find_paths=glob.glob,
        heartbeat=Path(HEARTBEAT_FILE),
        clock=time.monotonic,
    ):
        self.config = config
        self._open_device = open_device
        self._post = post
        self._find_paths = find_paths
        self._heartbeat = heartbeat
        self._clock = clock
        self.devices = {}
        self.running = True
        self._last_scan = None
        self._reported_missing = False

    def stop(self, *_):
        self.running = False

    def sync_devices(self):
        """Open newly attached macropad interfaces and forget ones that disappeared."""
        paths = set(self._find_paths(self.config.device_glob))
        for path in sorted(set(self.devices) - paths):
            self._drop(path, "removed")
        for path in sorted(paths - set(self.devices)):
            try:
                self.devices[path] = self._open_device(path)
                LOG.info("Listening on %s", path)
            except OSError as e:
                LOG.warning("Cannot open %s: %s", path, e)
        if not self.devices and not self._reported_missing:
            LOG.warning("Macropad %s:%s not found, waiting", self.config.vid, self.config.pid)
        self._reported_missing = not self.devices

    def _drop(self, path, reason):
        device = self.devices.pop(path, None)
        if device is not None:
            LOG.warning("Stopped listening on %s (%s)", path, reason)
            with contextlib.suppress(OSError):
                device.close()

    def handle_event(self, path, event):
        if event.type != ecodes.EV_KEY:
            return
        key_event = categorize(event)
        if key_event.keystate != key_event.key_down:
            return
        key = key_name(key_event.keycode)
        LOG.info("%s -> %s", path, key)
        try:
            status = self._post(self.config, build_payload(self.config, key, path))
            if status != 200:
                LOG.warning("Home Assistant returned HTTP %s", status)
        except (urllib.error.URLError, OSError) as e:
            LOG.warning("Could not send %s to Home Assistant: %s", key, e)

    def poll_once(self, timeout=1.0):
        now = self._clock()
        if self._last_scan is None or now - self._last_scan >= self.config.rescan_seconds:
            self.sync_devices()
            self._last_scan = now
        self._heartbeat.touch()

        if not self.devices:
            time.sleep(timeout)
            return
        by_fd = {device.fd: path for path, device in self.devices.items()}
        readable, _, _ = select.select(list(by_fd), [], [], timeout)
        for fd in readable:
            path = by_fd[fd]
            try:
                events = list(self.devices[path].read())
            except OSError as e:
                self._drop(path, e)
                continue
            for event in events:
                self.handle_event(path, event)

    def run(self):
        while self.running:
            self.poll_once()
        for path in list(self.devices):
            self._drop(path, "shutting down")


def main():
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    try:
        config = Config.from_env()
    except (ValueError, OSError) as e:
        LOG.error("%s", e)
        return 1

    bridge = Bridge(config)
    signal.signal(signal.SIGTERM, bridge.stop)
    signal.signal(signal.SIGINT, bridge.stop)
    LOG.info("Forwarding %s key presses to %s as %s", config.device_name, config.ha_url, config.event_type)
    bridge.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
