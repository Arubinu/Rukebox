"""WLED: the scenes sent, the time shared both ways, and the beat packets."""
import json
import math
import os
import shutil
import struct
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import _path  # noqa: F401
from config_and_scan import load_config
import rukebox_daemon
import wled


class FakeWled:
    """A WLED that keeps a clock of its own, `tz` seconds away from UTC."""

    def __init__(self, tz=7200, now=None):
        self.tz = tz
        self.utc = now if now is not None else time.time()
        self.states = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, body):
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/json/info":
                    local = datetime.fromtimestamp(fake.utc + fake.tz, timezone.utc)
                    hour = local.hour % 12 or 12
                    stamp = "%d-%d-%d, %02d:%02d:%02d%s" % (local.year, local.month, local.day, hour,
                                                         local.minute, local.second,
                                                         "PM" if local.hour >= 12 else "AM")
                    self._send({"ver": "0.15.0", "brand": "WLED", "name": "Salon",
                                "leds": {"count": 60}, "mac": "a0b1c2d3e4f5", "time": stamp,
                                "uptime": 1234})
                elif self.path == "/presets.json":
                    self._send({"0": {}, "1": {"n": "Sunrise"}, "3": {"n": "Party"}, "2": {"n": "Calm"}})
                else:
                    self.send_error(404)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
                fake.states.append(body)
                if "time" in body:
                    fake.utc = float(body["time"])
                self._send({"success": True})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.host = "127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class ModuleTest(unittest.TestCase):
    def test_the_addresses_are_kept_clean(self):
        cfg = {"WLED_HOSTS": "192.168.4.20, wled.local;;bad/host 192.168.4.20 10.0.0.2:8080"}
        self.assertEqual(wled.hosts(cfg), ["192.168.4.20", "wled.local", "10.0.0.2:8080"])

    def test_a_scene_is_a_preset_off_or_nothing(self):
        cfg = {"WLED_SCENE_PLAY": "4", "WLED_SCENE_PAUSE": "-1", "WLED_SCENE_IDLE": "x"}
        self.assertEqual(wled.state_for(wled.scene_preset(cfg, "play")), {"on": True, "ps": 4})
        self.assertEqual(wled.state_for(wled.scene_preset(cfg, "pause")), {"on": False})
        self.assertIsNone(wled.state_for(wled.scene_preset(cfg, "idle")))
        self.assertIsNone(wled.state_for(wled.scene_preset(cfg, "game")))

    def test_wled_s_clock_is_read_with_its_twelve_hour_form(self):
        self.assertEqual(wled.parse_time("2026-10-9, 07:03:05"), datetime(2026, 10, 9, 7, 3, 5))
        self.assertEqual(wled.parse_time("2026-10-9, 12:00:00AM"), datetime(2026, 10, 9, 0, 0, 0))
        self.assertEqual(wled.parse_time("2026-10-9, 01:30:00PM"), datetime(2026, 10, 9, 13, 30, 0))
        self.assertIsNone(wled.parse_time("nothing"))
        self.assertIsNone(wled.parse_time("2026-13-40, 01:00:00"))

    def test_the_timezone_is_learned_to_the_quarter_hour(self):
        utc = datetime(2026, 10, 9, 5, 0, 0, tzinfo=timezone.utc).timestamp()
        self.assertEqual(wled.learn_offset(datetime(2026, 10, 9, 7, 0, 2), utc), 7200)
        self.assertEqual(wled.learn_offset(datetime(2026, 10, 9, 10, 45, 1), utc), 20700)
        self.assertEqual(wled.learn_offset(datetime(2026, 10, 9, 5, 0, 0), utc), 0)

    def test_a_clock_that_cannot_be_right_is_not_trusted(self):
        self.assertIsNone(wled.trusted_time(datetime(1970, 1, 1, 0, 5), 0))
        later = datetime(2026, 10, 9, 7, 0, 0)
        self.assertIsNone(wled.trusted_time(later, 7200, not_before=wled.wall_seconds(later)),
                          "behind the newest time the radio wrote down")
        self.assertEqual(wled.trusted_time(later, 7200), wled.wall_seconds(later) - 7200)


class DeviceTest(unittest.TestCase):
    def setUp(self):
        self.device = FakeWled(tz=7200)
        self.addCleanup(self.device.close)

    def test_presets_by_number(self):
        self.assertEqual(wled.presets(self.device.host), [(1, "Sunrise"), (2, "Calm"), (3, "Party")])

    def test_a_wled_is_recognised_and_found(self):
        found = wled.describe(self.device.host)
        self.assertEqual((found["name"], found["leds"], found["ver"]), ("Salon", 60, "0.15.0"))
        with mock.patch.object(wled, "_avahi_hosts", return_value=[]), \
                mock.patch.object(wled, "_neighbours", return_value=["127.0.0.1:1"]):
            self.assertEqual([d["host"] for d in wled.discover(extra=[self.device.host], timeout=1)],
                             [self.device.host])

    def test_the_time_goes_one_way_and_comes_back_the_other(self):
        self.device.utc = 0
        offset = wled.push_time(self.device.host)
        self.assertEqual(offset, 7200, "learned from what WLED showed after taking the time")
        self.assertAlmostEqual(self.device.utc, time.time(), delta=2)
        self.device.utc = time.time() + 3600
        self.assertAlmostEqual(wled.read_time(self.device.host, offset), time.time() + 3600, delta=2)

    def test_scenes_are_sent_once_and_a_flash_gives_way(self):
        cfg = {"WLED_ENABLED": True, "WLED_HOSTS": self.device.host,
               "WLED_SCENE_PLAY": 2, "WLED_SCENE_BUTTON": 3}
        scene = {"now": "play"}
        lights = wled.Lights(lambda: cfg, lambda: scene["now"])
        lights.send = mock.Mock(wraps=lights.send)
        lights._tick()
        lights._tick()
        self.assertEqual(lights.send.call_args_list, [mock.call(2)], "a scene that stays is not resent")
        with mock.patch.object(wled, "FLASH_SEC", 0.05):
            lights.flash("button")
            lights._tick()
            self.assertEqual(lights.send.call_args_list[-1], mock.call(3))
            time.sleep(0.08)
            lights._tick()
        self.assertEqual(lights.send.call_args_list[-1], mock.call(2), "the scene comes back")

    def test_the_lights_go_off_with_the_radio_and_not_otherwise(self):
        cfg = {"WLED_ENABLED": True, "WLED_HOSTS": self.device.host, "WLED_OFF_AT_POWEROFF": True}
        wled.Lights(lambda: cfg, lambda: None).switch_off()
        self.assertEqual(self.device.states[-1], {"on": False})
        cfg["WLED_OFF_AT_POWEROFF"] = False
        wled.Lights(lambda: cfg, lambda: None).switch_off()
        self.assertEqual(len(self.device.states), 1)


class BeatTest(unittest.TestCase):
    def test_a_packet_is_what_wled_expects(self):
        tone = [0.3 * math.sin(2 * math.pi * 440 * i / wled.RATE) for i in range(wled.WINDOW)]
        packet = wled.Analyser().packet(tone)
        self.assertEqual(len(packet), 44)
        self.assertEqual(packet[:6], b"00002\x00")
        fft = list(packet[18:34])
        self.assertEqual(fft.index(max(fft)), 5, "440 Hz lands in the 430-560 Hz channel")
        self.assertAlmostEqual(struct.unpack("<f", packet[40:44])[0], 437.5, delta=70)

    def test_silence_is_all_zero(self):
        packet = wled.Analyser().packet([0.0] * wled.WINDOW)
        self.assertEqual(list(packet[18:34]), [0] * 16)
        self.assertEqual(packet[16], 0)

    def test_a_kick_is_a_peak(self):
        analyser = wled.Analyser()
        quiet = [0.02 * math.sin(2 * math.pi * 80 * i / wled.RATE) for i in range(wled.WINDOW)]
        kick = [0.8 * math.sin(2 * math.pi * 80 * i / wled.RATE) for i in range(wled.WINDOW)]
        for n in range(20):
            analyser.packet(quiet, now=n * 0.04)
        self.assertEqual(analyser.packet(kick, now=1.0)[16], 1)
        self.assertEqual(analyser.packet(kick, now=1.04)[16], 0, "not twice within 150 ms")


class FakeMpv:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class DaemonTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.addCleanup(mock.patch.stopall)
        self.device = FakeWled(tz=3600)
        self.addCleanup(self.device.close)
        cfg = load_config()
        cfg.update({
            "MUSIC_DIR": os.path.join(self.dir, "music"), "STATE_DIR": self.dir,
            "MUSIC_CACHE_FILE": os.path.join(self.dir, "cache.json"),
            "STATS_ENABLED": False, "STATS_DB_FILE": os.path.join(self.dir, "stats.db"),
            "ANNOUNCEMENTS_FILE": os.path.join(self.dir, "announcements.json"),
            "WLED_ENABLED": True, "WLED_HOSTS": self.device.host, "WLED_CLOCK": True,
            "CLOCK_SYNC_GRACE_SEC": 5,
        })
        os.makedirs(cfg["MUSIC_DIR"])
        mock.patch.object(rukebox_daemon, "audio_env", return_value={}).start()
        self.daemon = rukebox_daemon.RadioDaemon(cfg)
        self.daemon.mpv = FakeMpv()
        self.daemon._clock_ready = threading.Event()
        self.daemon._cue_played = threading.Event()

    def test_each_moment_has_its_scene(self):
        d = self.daemon
        d.mode = "idle"
        self.assertEqual(d._light_scene(), "idle")
        d.mode = "music"
        d._paused = False
        self.assertEqual(d._light_scene(), "play")
        d._light_start_until = time.monotonic() + 5
        self.assertEqual(d._light_scene(), "start")
        d._paused = True
        self.assertEqual(d._light_scene(), "pause")
        d._paused = False
        d._duck_proc = "starting"
        self.assertEqual(d._light_scene(), "announce")
        d._duck_proc = None
        for mode, scene in (("meme", "announce"), ("custom:morning", "announce"),
                            ("cutoff_announce", "cutoff"), ("game", "game"), ("restarting", None)):
            d.mode = mode
            self.assertEqual(d._light_scene(), scene, mode)
        d.mode = "music"
        d._powering_off = True
        self.assertIsNone(d._light_scene())

    def test_a_trusted_clock_is_given_and_its_timezone_remembered(self):
        self.daemon._declare_clock("rtc")
        self.device.utc = 0
        self.daemon._wled_clock_next = 0
        self.daemon._share_clock_with_wled(self.daemon.cfg)
        self.assertAlmostEqual(self.device.utc, time.time(), delta=2)
        memory = self.daemon.state.value("wled_clock")
        self.assertEqual(memory[self.device.host]["offset"], 3600)

    def test_a_doubtful_clock_gives_nothing(self):
        self.daemon._declare_clock("none", trusted=False)
        with mock.patch.object(rukebox_daemon.system_actions, "ntp_synchronized", return_value=False):
            self.daemon._wled_clock_next = 0
            self.daemon._share_clock_with_wled(self.daemon.cfg)
        self.assertEqual(self.device.states, [])

    def test_a_radio_without_a_clock_module_takes_wled_s(self):
        self.daemon.state.set_value("wled_clock", {self.device.host: {"offset": 3600, "at": 1}})
        real = time.time() + 7200
        self.device.utc = real
        with mock.patch.object(rukebox_daemon.system_actions, "set_clock",
                               return_value=(True, "")) as set_clock:
            self.assertTrue(self.daemon._try_wled_clock_sync())
        value, kwargs = set_clock.call_args[0][0], set_clock.call_args[1]
        self.assertTrue(kwargs.get("utc"))
        self.assertAlmostEqual(datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
                               .replace(tzinfo=timezone.utc).timestamp(), real, delta=2)
        self.assertEqual(self.daemon._time_source, "wled")
        self.assertTrue(self.daemon._clock_ready.is_set())

    def test_a_wled_whose_timezone_is_unknown_is_not_asked(self):
        self.assertFalse(self.daemon._try_wled_clock_sync())

    def test_wled_comes_first_then_bluetooth(self):
        d = self.daemon
        d.cfg.update({"BT_CLOCK_ENABLED": True, "BT_CLOCK_MAC": "AA:BB:CC:DD:EE:FF"})
        with mock.patch.object(d, "_try_wled_clock_sync", return_value=False), \
                mock.patch.object(d, "_try_bt_clock_sync") as bt:
            d._recover_clock()
        self.assertTrue(bt.called)
        with mock.patch.object(d, "_try_wled_clock_sync", return_value=True), \
                mock.patch.object(d, "_try_bt_clock_sync") as bt:
            d._recover_clock()
        self.assertFalse(bt.called)


if __name__ == "__main__":
    unittest.main()
