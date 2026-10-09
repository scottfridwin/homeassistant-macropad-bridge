import json
import os
import tempfile
import unittest
import unittest.mock
import urllib.error
from pathlib import Path

from evdev import InputEvent, ecodes

import healthcheck
from macropad_bridge import Bridge, Config, build_payload, key_name, post_event

BASE_ENV = {"HA_URL": "http://ha.local:8123/", "HA_TOKEN": "secret", "MACROPAD_VID": "1189", "MACROPAD_PID": "8890"}


def key(code, value):
    return InputEvent(0, 0, ecodes.EV_KEY, code, value)


class FakeDevice:
    def __init__(self, fd, events=(), error=None):
        self.fd = fd
        self._events = events
        self._error = error
        self.closed = False

    def read(self):
        if self._error:
            raise self._error
        return iter(self._events)

    def close(self):
        self.closed = True


class ConfigTests(unittest.TestCase):
    def test_from_env_defaults_and_trailing_slash(self):
        config = Config.from_env(BASE_ENV)
        self.assertEqual(config.ha_url, "http://ha.local:8123")
        self.assertEqual(config.event_type, "macropad_key")
        self.assertEqual(config.device_glob, "/dev/input/by-id/usb-1189_8890*-event-kbd")

    def test_token_file_overrides_env(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("from-file\n")
        self.addCleanup(os.unlink, f.name)
        config = Config.from_env({**BASE_ENV, "HA_TOKEN_FILE": f.name})
        self.assertEqual(config.ha_token, "from-file")

    def test_missing_settings_are_listed(self):
        with self.assertRaisesRegex(ValueError, "HA_TOKEN or HA_TOKEN_FILE.*MACROPAD_PID"):
            Config.from_env({"HA_URL": "http://ha", "MACROPAD_VID": "1"})

    def test_rejects_non_http_url(self):
        with self.assertRaisesRegex(ValueError, "http"):
            Config.from_env({**BASE_ENV, "HA_URL": "file:///etc/passwd"})


class PayloadTests(unittest.TestCase):
    def test_key_name_joins_aliases(self):
        self.assertEqual(key_name("KEY_MUTE"), "KEY_MUTE")
        self.assertEqual(key_name(("KEY_MIN_INTERESTING", "KEY_MUTE")), "KEY_MIN_INTERESTING_KEY_MUTE")
        self.assertEqual(key_name(["A", "B"]), "A_B")

    def test_payload_keeps_event_data_nesting(self):
        config = Config.from_env({**BASE_ENV, "DEVICE_NAME": "diningroom_kiosk"})
        self.assertEqual(
            build_payload(config, "KEY_MUTE", "/dev/input/x"),
            {
                "event_type": "macropad_key",
                "event_data": {"key": "KEY_MUTE", "device": "diningroom_kiosk", "source": "/dev/input/x"},
            },
        )

    def test_post_event_request(self):
        captured = {}

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def urlopen(request, timeout):
            captured.update(request=request, timeout=timeout)
            return Response()

        config = Config.from_env(BASE_ENV)
        self.assertEqual(post_event(config, {"a": 1}, urlopen=urlopen), 200)
        request = captured["request"]
        self.assertEqual(request.full_url, "http://ha.local:8123/api/events/macropad_key")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer secret")
        self.assertEqual(json.loads(request.data), {"a": 1})


class BridgeTests(unittest.TestCase):
    def make_bridge(self, paths, devices, posts):
        heartbeat = Path(tempfile.mkdtemp()) / "hb"
        return Bridge(
            Config.from_env(BASE_ENV),
            open_device=lambda path: devices[path],
            post=lambda config, payload: posts.append(payload) or 200,
            find_paths=lambda pattern: list(paths),
            heartbeat=heartbeat,
            clock=lambda: 0.0,
        )

    def test_key_down_is_forwarded_and_up_ignored(self):
        posts = []
        bridge = self.make_bridge([], {}, posts)
        bridge.handle_event("/dev/in", key(ecodes.KEY_VOLUMEUP, 1))
        bridge.handle_event("/dev/in", key(ecodes.KEY_VOLUMEUP, 0))
        bridge.handle_event("/dev/in", key(ecodes.KEY_VOLUMEUP, 2))
        self.assertEqual([p["event_data"]["key"] for p in posts], ["KEY_VOLUMEUP"])

    def test_non_key_events_ignored(self):
        posts = []
        bridge = self.make_bridge([], {}, posts)
        bridge.handle_event("/dev/in", InputEvent(0, 0, ecodes.EV_SYN, 0, 0))
        self.assertEqual(posts, [])

    def test_post_errors_do_not_raise(self):
        bridge = self.make_bridge([], {}, [])
        bridge._post = lambda config, payload: (_ for _ in ()).throw(urllib.error.URLError("down"))
        bridge.handle_event("/dev/in", key(ecodes.KEY_MUTE, 1))

    def test_sync_adds_and_removes_devices(self):
        paths = ["/a", "/b"]
        devices = {"/a": FakeDevice(10), "/b": FakeDevice(11)}
        bridge = self.make_bridge(paths, devices, [])
        bridge.sync_devices()
        self.assertEqual(set(bridge.devices), {"/a", "/b"})
        paths.remove("/b")
        bridge.sync_devices()
        self.assertEqual(set(bridge.devices), {"/a"})
        self.assertTrue(devices["/b"].closed)

    def test_unplugged_device_is_dropped_on_read_error(self):
        devices = {"/a": FakeDevice(10, error=OSError(19, "No such device"))}
        bridge = self.make_bridge(["/a"], devices, [])
        bridge.sync_devices()
        with unittest.mock.patch("select.select", return_value=([10], [], [])):
            bridge.poll_once(timeout=0)
        self.assertEqual(bridge.devices, {})
        self.assertTrue(devices["/a"].closed)

    def test_poll_reads_events_and_touches_heartbeat(self):
        posts = []
        devices = {"/a": FakeDevice(10, events=[key(ecodes.KEY_PLAYPAUSE, 1)])}
        bridge = self.make_bridge(["/a"], devices, posts)
        with unittest.mock.patch("select.select", return_value=([10], [], [])):
            bridge.poll_once(timeout=0)
        self.assertEqual(posts[0]["event_data"]["key"], "KEY_PLAYPAUSE")
        self.assertTrue(bridge._heartbeat.exists())

    def test_stop_ends_run_and_closes_devices(self):
        devices = {"/a": FakeDevice(10)}
        bridge = self.make_bridge(["/a"], devices, [])
        bridge.sync_devices()
        bridge.stop()
        bridge.run()
        self.assertTrue(devices["/a"].closed)


class HealthcheckTests(unittest.TestCase):
    def test_fresh_and_stale_heartbeat(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            pass
        self.addCleanup(os.unlink, f.name)
        mtime = os.path.getmtime(f.name)
        self.assertTrue(healthcheck.is_healthy(f.name, now=mtime + 5))
        self.assertFalse(healthcheck.is_healthy(f.name, now=mtime + 60))
        self.assertFalse(healthcheck.is_healthy("/nonexistent/heartbeat"))


if __name__ == "__main__":
    unittest.main()
