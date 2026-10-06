"""The network output (src/stream.py): the radio encoded and served over HTTP.

This is how a container is heard at all - it has no sound card - so what
matters here is that nothing is started when the feature is off, that the
encoder is chosen from what ffmpeg can actually do, that a listener falling
behind follows the present rather than the past, and that an encoder which
cannot start is given up on rather than restarted for ever. ffmpeg and pactl
are faked: there is no PipeWire under this test."""
import io
import json
import os
import time
import unittest
from unittest import mock

import _path  # noqa: F401
import stream


def fake_run(stdout="", returncode=0):
    def run(*_args, **_kwargs):
        return mock.Mock(returncode=returncode, stdout=stdout, stderr="")
    return run


def ogg_page(flags=0, granule=0, payload=b"x", serial=1, seq=0):
    """One complete Ogg page: ffmpeg writes them like this for Opus or Vorbis."""
    segments = bytes([len(payload)])
    return (b"OggS" + bytes([0, flags]) + granule.to_bytes(8, "little")
            + serial.to_bytes(4, "little") + seq.to_bytes(4, "little")
            + b"\x00\x00\x00\x00" + bytes([len(segments)]) + segments + payload)


class ProbeSourceTest(unittest.TestCase):
    """`pactl` answers first, `pw-dump` when there is no Pulse layer."""

    def pactl(self, sources="", default="", fails=False):
        def run(command, **_kwargs):
            if command[0] != "pactl":
                return mock.Mock(returncode=127, stdout="", stderr="")
            if fails:
                return mock.Mock(returncode=1, stdout="", stderr="connection refused")
            if command[1] == "get-default-sink":
                return mock.Mock(returncode=0, stdout=default, stderr="")
            return mock.Mock(returncode=0, stdout=sources, stderr="")
        return mock.patch.object(stream.subprocess, "run", side_effect=run)

    def test_the_default_sinks_monitor_is_preferred(self):
        sources = ("32\talsa_output.usb.monitor\tPipeWire\tfloat32le\n"
                   "33\talsa_output.pci.monitor\tPipeWire\tfloat32le\n")
        with self.pactl(sources=sources, default="alsa_output.pci\n"):
            self.assertEqual(stream.probe_source(), "alsa_output.pci.monitor")

    def test_any_monitor_is_the_fallback(self):
        sources = "32\tbluez_output.speaker.monitor\tPipeWire\tfloat32le\n"
        with self.pactl(sources=sources, default="something.else\n"):
            self.assertEqual(stream.probe_source(), "bluez_output.speaker.monitor")

    def test_the_first_monitor_when_pactl_cannot_name_the_default(self):
        sources = "32\trukebox_output.monitor\tPipeWire\tfloat32le\n"
        with self.pactl(sources=sources, default=""):
            self.assertEqual(stream.probe_source(), "rukebox_output.monitor")

    def test_no_monitor_at_all_is_an_empty_source(self):
        with self.pactl(sources="32\talsa_input.mic\tPipeWire\tfloat32le\n"):
            self.assertEqual(stream.probe_source(), "")

    def test_a_pactl_that_cannot_connect_falls_through_to_pw_dump(self):
        dump = json.dumps([{"info": {"props": {"node.name": "snd.monitor",
                                               "media.class": "Audio/Source"}}}])
        calls = []

        def run(command, **_kwargs):
            calls.append(command[0])
            if command[0] == "pactl":
                return mock.Mock(returncode=1, stdout="", stderr="connection refused")
            return mock.Mock(returncode=0, stdout=dump, stderr="")

        with mock.patch.object(stream.subprocess, "run", side_effect=run):
            self.assertEqual(stream.probe_source(), "snd.monitor")
        self.assertEqual(calls, ["pactl", "pw-dump"],
                         "a pactl that cannot connect is not asked twice")

    def test_no_sound_server_at_all_is_an_empty_source_not_a_crash(self):
        with mock.patch.object(stream.subprocess, "run", side_effect=OSError):
            self.assertEqual(stream.probe_source(), "")

    def test_a_dump_that_is_not_json_falls_through(self):
        with self.pactl(fails=True):
            with mock.patch.object(stream.subprocess, "run",
                                   side_effect=fake_run(stdout="oops")):
                self.assertEqual(stream.probe_source(), "")


class EncodersTest(unittest.TestCase):
    def test_what_ffmpeg_can_do_is_read_from_it(self):
        listing = " A..... libopus   Opus\n A..... libmp3lame  MP3\n"
        with mock.patch.object(stream.subprocess, "run", side_effect=fake_run(stdout=listing)):
            self.assertEqual(stream.encoders_available(), ["opus", "mp3"])

    def test_opus_comes_first_whatever_the_listing_order(self):
        listing = " libmp3lame \n libopus \n"
        with mock.patch.object(stream.subprocess, "run", side_effect=fake_run(stdout=listing)):
            self.assertEqual(stream.encoders_available(), ["opus", "mp3"])

    def test_no_ffmpeg_is_no_encoder(self):
        with mock.patch.object(stream.subprocess, "run", side_effect=OSError):
            self.assertEqual(stream.encoders_available(), [])


class BuildTest(unittest.TestCase):
    def test_off_by_default(self):
        self.assertIsNone(stream.build({"STREAM_ENABLED": False}))

    def test_the_built_command_encodes_the_monitor(self):
        with mock.patch.object(stream, "encoders_available", return_value=["opus"]):
            server = stream.build({"STREAM_ENABLED": True, "STREAM_SOURCE": "sink.monitor"},
                                  probe=False)
        self.assertEqual(server.command()[0], "ffmpeg")
        self.assertIn("sink.monitor", server.command())
        self.assertIn("libopus", server.command())
        self.assertEqual(server.suffix, "opus")
        self.assertEqual(server.content_type, "audio/ogg")
        self.assertEqual(server.command()[-1], "pipe:1")

    def test_the_asked_for_codec_wins_when_ffmpeg_has_it(self):
        with mock.patch.object(stream, "encoders_available", return_value=["opus", "mp3"]):
            server = stream.build({"STREAM_ENABLED": True, "STREAM_SOURCE": "s",
                                   "STREAM_ENCODER": "mp3"}, probe=False)
        self.assertEqual(server.encoder, "mp3")
        self.assertEqual(server.content_type, "audio/mpeg")

    def test_an_unavailable_codec_falls_back_to_what_works(self):
        with mock.patch.object(stream, "encoders_available", return_value=["mp3"]):
            server = stream.build({"STREAM_ENABLED": True, "STREAM_SOURCE": "s",
                                   "STREAM_ENCODER": "opus"}, probe=False)
        self.assertEqual(server.encoder, "mp3")

    def test_no_source_is_still_a_server_that_says_so(self):
        with mock.patch.object(stream, "encoders_available", return_value=["opus"]):
            server = stream.build({"STREAM_ENABLED": True}, probe=False)
        self.assertIsNotNone(server)
        self.assertEqual(server.source, "")
        self.assertFalse(server.start(), "nothing to encode")
        self.assertFalse(stream.status(server)["available"])


class FakeProcess:
    """An ffmpeg whose output blocks until the test feeds it.

    A BytesIO would not do: the encoder thread reads it to the end in one go,
    before any listener had a chance to attach, and what it broadcast to nobody
    is gone - which is exactly the behaviour of a real pipe."""

    def __init__(self):
        self.read_fd, self.write_fd = os.pipe()
        self.stdout = os.fdopen(self.read_fd, "rb", buffering=0)
        self.stderr = io.BytesIO(b"")
        self._done = False

    def feed(self, data):
        os.write(self.write_fd, data)

    def eof(self):
        if self.write_fd is not None:
            os.close(self.write_fd)
            self.write_fd = None

    def poll(self):
        return 0 if self._done else None

    def terminate(self):
        self._done = True
        self.eof()


def build_server():
    with mock.patch.object(stream, "encoders_available", return_value=["opus"]):
        return stream.build({"STREAM_ENABLED": True, "STREAM_SOURCE": "s.monitor"},
                            probe=False)


class ListenerTest(unittest.TestCase):
    def test_a_listener_gets_the_bytes_and_then_the_end(self):
        """What ffmpeg writes reaches the listener, and its end ends the
        response.

        The listener attaches before anything is fed: the encoder thread reads
        the pipe as soon as it exists, and what it read while nobody was
        listening is gone - a real pipe behaves exactly like this, and a test
        that fed first would be racing it."""
        server = build_server()
        process = FakeProcess()
        with mock.patch.object(stream.subprocess, "Popen", return_value=process):
            self.assertTrue(server.start())
            box = server.listen()
            process.feed(b"aaa")
            process.feed(b"bbb")
            process.eof()
            chunks = [box.get(timeout=1) for _ in range(3)]
            process.stdout.close()
        self.assertEqual(chunks, [b"aaa", b"bbb", None],
                         "two chunks of audio, then the end")

    def test_every_listener_gets_what_is_encoded_from_then_on(self):
        """Two listeners, one encoder: the second hears what follows its
        arrival, which is what makes this a live stream and not a file."""
        server = build_server()
        first = server.listen()
        server._broadcast(b"early")
        self.assertEqual(first.get_nowait(), b"early")
        second = server.listen()
        self.assertTrue(first.empty(), "the first listener already had it")
        server._broadcast(b"late")
        self.assertEqual(first.get_nowait(), b"late")
        self.assertEqual(second.get_nowait(), b"late")

    def test_a_slow_listener_drops_the_past_rather_than_the_present(self):
        server = build_server()
        server.queue_chunks = 2
        box = server.listen()
        for _ in range(5):
            server._broadcast(b"chunk")
        self.assertEqual(box.qsize(), 2, "the queue never grows past its size")
        server._broadcast(b"last")
        self.assertEqual(box.get_nowait(), b"chunk")
        self.assertEqual(box.get_nowait(), b"last", "the newest chunk is kept")

    def test_listeners_are_counted_and_forgotten(self):
        server = build_server()
        self.assertEqual(server.listener_count(), 0)
        box = server.listen()
        self.assertEqual(server.listener_count(), 1)
        server.forget(box)
        self.assertEqual(server.listener_count(), 0)

    def test_a_stopped_stream_ends_every_listener(self):
        server = build_server()
        box = server.listen()
        server.stop()
        self.assertIsNone(box.get_nowait(), "None is what tells a client to reconnect")
        self.assertEqual(server.listener_count(), 0)

    def test_a_listener_whose_encoder_vanished_does_not_wait_for_ever(self):
        """An ffmpeg killed without closing its pipe must end the response
        rather than leave a request thread parked on an empty queue."""
        server = build_server()
        with mock.patch.object(stream, "CHUNK_WAIT_SEC", 0.01):
            self.assertEqual(b"".join(server.chunks()), b"")
        self.assertEqual(server.listener_count(), 0, "and it let go of its slot")

    def test_an_encoder_that_cannot_start_says_why(self):
        server = build_server()
        with mock.patch.object(stream.subprocess, "Popen", side_effect=OSError("no ffmpeg")):
            self.assertFalse(server.start())
        self.assertEqual(server.last_error, "no ffmpeg")
        self.assertFalse(server.alive())

    def test_stopping_is_not_a_crash(self):
        """A deliberate stop must not look like an encoder that died."""
        server = build_server()
        with mock.patch.object(stream.subprocess, "Popen", return_value=FakeProcess()):
            server.start()
            server.stop()
        self.assertEqual(server._restarts, 0)
        self.assertFalse(server._gave_up)

    def test_a_stream_that_gave_up_does_not_retry(self):
        server = build_server()
        server._restarts = stream.MAX_RESTARTS
        with mock.patch.object(stream, "RESTART_DELAY_SEC", 0):
            server._on_stopped_unexpectedly()
        self.assertTrue(server._gave_up)
        self.assertEqual(server._restarts, stream.MAX_RESTARTS)
        self.assertFalse(server.start(), "it stays given up")


class OggPageTest(unittest.TestCase):
    """The headers of an Ogg stream are what a late listener needs first."""

    def test_a_stream_that_is_not_ogg_has_no_headers_to_keep(self):
        self.assertEqual(stream._ogg_header_length(b"\xff\xfb\x90\x00 music"), 0)

    def test_half_a_page_waits_for_the_rest(self):
        self.assertIsNone(stream._ogg_header_length(b"Ogg"))
        self.assertIsNone(stream._ogg_header_length(ogg_page()[:-1]))

    def test_the_headers_end_where_the_audio_begins(self):
        headers = ogg_page(flags=2, payload=b"OpusHead") + ogg_page(payload=b"OpusTags")
        audio = ogg_page(granule=96000, payload=b"audio")
        self.assertEqual(stream._ogg_header_length(headers + audio), len(headers))
        self.assertIsNone(stream._ogg_header_length(headers), "the audio page is not in yet")
        self.assertEqual(stream._ogg_header_length(headers + audio + audio), len(headers))

    def test_vorbis_has_three_of_them(self):
        headers = (ogg_page(flags=2, payload=b"id") + ogg_page(payload=b"comment")
                   + ogg_page(payload=b"setup"))
        self.assertEqual(stream._ogg_header_length(headers + ogg_page(granule=1)), len(headers))


class LateListenerTest(unittest.TestCase):
    """A player that arrives after the encoder started must still hear it.

    Without the headers an Ogg stream is undecodable - VLC says "couldn't find
    any ogg logical stream" and plays nothing - so they are replayed first."""

    def setUp(self):
        patcher = mock.patch.object(stream.subprocess, "Popen",
                                    side_effect=OSError("no ffmpeg here"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def headers(self):
        return ogg_page(flags=2, payload=b"OpusHead") + ogg_page(payload=b"OpusTags")

    def feed(self, server, granule):
        server._broadcast(ogg_page(granule=granule, payload=b"audio"))

    def test_the_headers_come_first(self):
        server = build_server()
        headers = self.headers()
        server._broadcast(headers)
        self.feed(server, 96000)
        box = server.listen()
        self.feed(server, 192000)
        self.assertEqual(box.get_nowait(), headers)
        self.assertEqual(box.get_nowait(), ogg_page(granule=192000, payload=b"audio"),
                         "and then the live stream, not what it missed")

    def test_the_first_listener_gets_them_only_once(self):
        """It is fed the live stream from its first byte: what it reads already
        begins with the headers, and repeating them would look like a new
        stream starting."""
        server = build_server()
        headers = self.headers()
        box = server.listen()
        server._broadcast(headers)
        self.feed(server, 96000)
        self.assertEqual(box.get_nowait(), headers)
        self.assertEqual(box.get_nowait(), ogg_page(granule=96000, payload=b"audio"))

    def test_an_mp3_stream_keeps_nothing(self):
        with mock.patch.object(stream, "encoders_available", return_value=["mp3"]):
            server = stream.build({"STREAM_ENABLED": True, "STREAM_SOURCE": "s.monitor"},
                                  probe=False)
        server._broadcast(b"\xff\xfb\x90\x00" * 100)
        box = server.listen()
        self.assertEqual(box.qsize(), 0, "nothing to replay to a late listener")

    def test_a_new_encoder_starts_with_new_headers(self):
        server = build_server()
        server._broadcast(self.headers())
        self.feed(server, 96000)
        self.assertTrue(server.header)
        process = FakeProcess()
        with mock.patch.object(stream.subprocess, "Popen", return_value=process):
            server.start()
            self.assertEqual(server.header, b"", "the headers of the process before")
            process.eof()
            process.stdout.close()


class StatusTest(unittest.TestCase):
    def test_off_says_off(self):
        data = stream.status(None)
        self.assertFalse(data["enabled"])
        self.assertFalse(data["available"])
        self.assertEqual(data["url"], "")
        self.assertEqual(data["why"], "off")

    def test_a_server_with_no_source_is_not_offered(self):
        with mock.patch.object(stream, "encoders_available", return_value=["opus"]):
            server = stream.build({"STREAM_ENABLED": True}, probe=False)
        data = stream.status(server, url="http://radio/stream.opus")
        self.assertTrue(data["enabled"])
        self.assertFalse(data["available"])
        self.assertEqual(data["url"], "", "a URL that plays silence is worse than none")
        self.assertEqual(data["why"], "no_source")

    def test_a_ready_server_carries_its_url_and_codec(self):
        server = build_server()
        data = stream.status(server, url="http://radio/stream.opus")
        self.assertTrue(data["available"])
        self.assertEqual(data["url"], "http://radio/stream.opus")
        self.assertEqual(data["encoder"], "opus")
        self.assertEqual(data["content_type"], "audio/ogg")
        self.assertEqual(data["listeners"], 0)
        self.assertEqual(data["why"], "")


class WhyUnavailableTest(unittest.TestCase):
    """"Nothing plays over the network" has several causes, and a browser
    cannot tell them apart: each one gets its own code."""

    def with_tools(self, present, monitors=None):
        def has(name):
            return name in present

        patchers = [mock.patch.object(stream, "_has_program", side_effect=has)]
        if monitors is not None:
            patchers.append(mock.patch.object(stream, "_all_sources",
                                              return_value=monitors))
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_no_tools_at_all(self):
        self.with_tools(set(), [])
        self.assertEqual(stream.why_unavailable(), "no_tools")

    def test_no_ffmpeg(self):
        self.with_tools({"pw-dump"}, [])
        self.assertEqual(stream.why_unavailable(), "no_ffmpeg")

    def test_no_sound_server(self):
        self.with_tools({"pw-dump", "ffmpeg"}, None)
        self.assertEqual(stream.why_unavailable(), "no_sound_server")

    def test_a_sound_server_with_nothing_to_encode(self):
        """The container before its virtual sink exists."""
        self.with_tools({"pw-dump", "ffmpeg"}, [])
        self.assertEqual(stream.why_unavailable(), "no_source")

    def test_a_monitor_is_there(self):
        self.with_tools({"pw-dump", "ffmpeg"}, ["sink.monitor"])
        self.assertEqual(stream.why_unavailable(), "unknown")


if __name__ == "__main__":
    unittest.main()
