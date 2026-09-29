"""mpv's IPC answers (src/mpv_controller.py): what a property write was
refused with is asked for rather than guessed, the lavfi wrapper is the
fallback for an older mpv, and the compression chain comes from the setting.
A socket pair stands in for mpv, so this runs off-hardware."""
import json
import socket
import threading
import unittest

import _path  # noqa: F401
from mpv_controller import MPVController, compression_filter


class FakeMpv:
    """Answers on the other end of a socket pair, the way mpv does."""

    def __init__(self, errors=("success",), silent=False):
        self.controller_end, self.mpv_end = socket.socketpair()
        self.errors = list(errors)
        self.silent = silent
        self.seen = []
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        buf = b""
        while True:
            try:
                data = self.mpv_end.recv(4096)
            except OSError:
                return
            if not data:
                return
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                message = json.loads(line.decode("utf-8"))
                self.seen.append(message["command"])
                if self.silent:
                    continue
                error = self.errors.pop(0) if self.errors else "success"
                answer = {"request_id": message["request_id"], "error": error}
                self.mpv_end.sendall((json.dumps(answer) + "\n").encode("utf-8"))

    def close(self):
        self.mpv_end.close()


class FilterChainTest(unittest.TestCase):
    def test_a_chain_per_mode(self):
        self.assertEqual(compression_filter("off"), "")
        self.assertEqual(compression_filter(""), "")
        self.assertEqual(compression_filter("nonsense"), "")
        self.assertEqual(compression_filter(None), "")
        for mode in ("soft", "strong"):
            chain = compression_filter(mode.upper())
            self.assertIn("acompressor=", chain, mode)
            self.assertIn("alimiter=", chain, mode)
            self.assertTrue(chain.endswith("level=false"), chain)

    def test_stronger_is_louder(self):
        self.assertIn("volume=9dB", compression_filter("strong"))
        self.assertIn("threshold=0.063", compression_filter("strong"))
        self.assertIn("volume=5dB", compression_filter("soft"))


class RequestTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeMpv()
        self.mpv = MPVController("/tmp/never-used")
        self.mpv.sock = self.fake.controller_end
        threading.Thread(target=self.mpv._listen_events, daemon=True).start()

    def tearDown(self):
        self.fake.close()
        self.mpv.sock.close()

    def test_the_answer_is_waited_for(self):
        self.assertEqual(self.mpv.request(["set_property", "af", "x"])["error"], "success")
        self.assertEqual(self.fake.seen[-1], ["set_property", "af", "x"])

    def test_no_answer_is_not_a_crash(self):
        self.fake.silent = True
        self.assertIsNone(self.mpv.request(["set_property", "af", "x"], timeout=0.2))
        self.assertEqual(self.mpv._pending, {}, "the waiter is always forgotten")

    def test_a_filter_is_set_and_cleared(self):
        self.assertTrue(self.mpv.set_audio_filter("acompressor=x"))
        self.assertEqual(self.fake.seen[-1], ["set_property", "af", "acompressor=x"])
        self.assertTrue(self.mpv.set_audio_filter(""))
        self.assertEqual(self.fake.seen[-1], ["set_property", "af", ""])

    def test_the_lavfi_wrapper_is_the_fallback(self):
        self.fake.errors = ["property not found"]
        self.assertTrue(self.mpv.set_audio_filter("acompressor=x"))
        self.assertEqual(self.fake.seen[-1], ["set_property", "af", "lavfi=[acompressor=x]"])

    def test_a_build_without_the_filter_is_reported(self):
        self.fake.errors = ["property not found", "property not found"]
        self.assertFalse(self.mpv.set_audio_filter("acompressor=x"))
        self.assertEqual(len(self.fake.seen), 2, "both syntaxes were tried")


if __name__ == "__main__":
    unittest.main()
