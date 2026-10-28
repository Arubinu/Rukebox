"""Music sent to the radio (cast mode): web addresses only, what ends it, and when it is offered."""
import os
import shutil
import socket
import tempfile
import unittest
from unittest import mock

import _path  # noqa: F401
import control_client
import dlna_renderer as dr
from test_empty_library import DaemonCase

URL = "http://192.168.1.30:8080/song.mp3"


class CastTest(DaemonCase):
    def setUp(self):
        super().setUp()
        self.daemon.mode = "music"
        self.daemon._paused = False
        self.daemon._last_music_track = os.path.join(self.music, "a.mp3")
        self.daemon._position = 42.0

    def test_only_a_web_address_is_played(self):
        for url in ("/etc/passwd", "file:///home/pi/x.mp3", "ftp://x/y.mp3", "http://a b/c.mp3", ""):
            self.assertEqual(self.daemon._cast_play({"url": url}, "dlna")["error"], "bad_url", url)
        self.assertEqual(self.daemon.mode, "music", "the radio's music was never touched")

    def test_the_music_waits_under_what_is_sent_and_comes_back_where_it_was(self):
        answer = self.daemon._cast_play({"url": URL, "title": "Sent song", "art": "file:///x.jpg"}, "dlna")
        self.assertTrue(answer["ok"])
        self.assertEqual((self.daemon.mode, self.daemon.mpv.files[-1]), ("cast", URL))
        self.assertEqual(self.daemon._cast["title"], "Sent song")
        self.assertIsNone(self.daemon._cast["art"], "a picture is a web address too")
        self.assertEqual(self.daemon._resume_track, (os.path.join(self.music, "a.mp3"), 42.0))
        with mock.patch.object(self.daemon, "_resume_after_announce") as resume:
            self.daemon._end_cast()
        resume.assert_called_once()
        self.assertIsNone(self.daemon._cast)

    def test_the_end_of_what_was_sent_waits_a_moment_for_the_next(self):
        self.daemon._cast_play({"url": URL}, "dlna")
        with mock.patch.object(self.daemon, "_set_timer") as timer:
            self.daemon._on_mpv_event({"event": "end-file", "reason": "eof"})
        self.assertEqual(timer.call_args[0][:2], ("cast_end", self.daemon.CAST_GRACE_SEC))
        self.assertEqual(self.daemon.mode, "cast", "not back to the radio at once")
        self.daemon._cast_play({"url": URL + "?2"}, "dlna")
        self.assertEqual(self.daemon._resume_track, (os.path.join(self.music, "a.mp3"), 42.0),
                         "the next one sent keeps where the radio's music was")

    def test_a_radio_that_was_quiet_goes_back_to_its_quiet(self):
        self.daemon.mode = "stopped"
        self.daemon._cast_play({"url": URL}, "dlna")
        with mock.patch.object(self.daemon, "_start_keepalive") as quiet:
            self.daemon._end_cast()
        self.assertEqual(self.daemon.mode, "stopped")
        quiet.assert_called_once()

    def test_the_cutoff_ends_what_was_sent(self):
        self.daemon._cast_play({"url": URL}, "dlna")
        with mock.patch.object(self.daemon, "_cutoff_due", return_value=True), \
                mock.patch.object(self.daemon, "_check_usb_music"), \
                mock.patch.object(self.daemon, "_trigger_cutoff_from_idle") as cutoff:
            self.daemon._scheduler_tick()
        cutoff.assert_called_once()
        self.assertIsNone(self.daemon._cast)

    @unittest.skipUnless(hasattr(socket, "AF_UNIX"), "the control socket is a Unix socket")
    def test_next_and_stop_go_back_to_the_radio(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        path = os.path.join(folder, "control.sock")
        self.daemon.cfg["CONTROL_SOCKET"] = path
        self.daemon._start_control_socket()
        self.addCleanup(self.daemon._stop_event.set)
        with mock.patch.object(self.daemon, "_resume_after_announce"):
            for cmd in ("next_track", "cast_stop"):
                self.assertTrue(control_client.send_control_command(path, "cast_play", url=URL)["ok"])
                self.assertTrue(control_client.send_control_command(path, cmd)["ok"], cmd)
                self.assertIsNone(self.daemon._cast, cmd)


class ReceivingRendererTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(dr.options.update, {"receiving": False})
        self.addCleanup(dr._pending.clear)

    def call(self, action, now, **arguments):
        sent = []

        def send(command, **kwargs):
            sent.append((command, kwargs))
            return {"ok": True}
        return dr.handle("AVTransport", action, arguments, now, send), sent

    def test_nothing_is_offered_while_not_receiving(self):
        now = dr.state({"mode": "music"}, {}, "http://r", "")
        (status, _body), sent = self.call("SetAVTransportURI", now, CurrentURI=URL)
        self.assertEqual((status, sent), (500, []))
        self.assertNotIn("SetAVTransportURI", dr.service_description("AVTransport"))

    def test_a_sent_song_plays_on_play_with_what_it_says_of_itself(self):
        dr.options["receiving"] = True
        self.assertIn("SetAVTransportURI", dr.service_description("AVTransport"))
        self.assertIn("audio/flac", dr.connection_variables()["SinkProtocolInfo"])
        meta = ('<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
                '<item><dc:title>Sent song</dc:title><upnp:artist>Someone</upnp:artist>'
                '<upnp:albumArtURI>http://phone/art.jpg</upnp:albumArtURI></item></DIDL-Lite>')
        playing = dr.state({"mode": "music"}, {}, "http://r", "")
        (status, _body), sent = self.call("SetAVTransportURI", playing, CurrentURI=URL, CurrentURIMetaData=meta)
        self.assertEqual((status, sent), (200, []), "nothing plays before Play")
        self.assertTrue(dr.state({"mode": "music"}, {}, "http://r", "")["CurrentTransportActions"]
                        .startswith("Play,"), "a controller waiting for Play is offered it")
        (status, _body), sent = self.call("Play", playing)
        self.assertEqual(sent, [("cast_play", {"url": URL, "title": "Sent song", "artist": "Someone",
                                               "art": "http://phone/art.jpg"})])
        self.assertFalse(dr.pending())
        (status, _body), _sent = self.call("SetAVTransportURI", playing, CurrentURI="file:///etc/passwd")
        self.assertEqual(status, 500, "a web address only")

    def test_what_was_sent_is_what_the_controller_sees_and_stop_ends_it(self):
        dr.options["receiving"] = True
        status = {"mode": "cast", "paused": False, "duration": 200, "position": 10,
                  "cast": {"url": URL, "title": "Sent song", "artist": "Someone", "art": "http://phone/a.jpg"}}
        now = dr.state(status, {}, "http://r", "")
        self.assertEqual((now["TransportState"], now["CurrentTransportActions"]), ("PLAYING", "Pause,Stop"))
        self.assertIn("Sent song", now["CurrentTrackMetaData"])
        self.assertIn("http://phone/a.jpg", now["CurrentTrackMetaData"])
        self.assertEqual(self.call("Stop", now)[1], [("cast_stop", {})])
        self.assertEqual(self.call("Pause", now)[1], [("toggle_pause", {})])
        self.assertEqual(self.call("Next", now)[0][0], 500)


if __name__ == "__main__":
    unittest.main()
