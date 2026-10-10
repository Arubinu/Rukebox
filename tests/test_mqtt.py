"""Home Assistant over MQTT: the client's packets against a little broker, and what the bridge says and does."""

import json
import os
import shutil
import socket
import struct
import tempfile
import threading
import unittest
from unittest import mock

import _path  # noqa: F401
import mqtt_bridge
import mqtt_client as mc


class FakeBroker:
    """Accepts one client: answers CONNECT with `code`, keeps what it is sent, may send one PUBLISH."""

    def __init__(self, code=0, send=None):
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(1)
        self.port = self.server.getsockname()[1]
        self.code, self.send = code, send
        self.received = []
        self.done = threading.Event()
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        conn, _ = self.server.accept()
        conn.settimeout(5)
        buffer = b""
        try:
            while True:
                data = conn.recv(65536)
                if not data:
                    break
                packets, buffer = mc.split_packets(buffer + data)
                for kind, flags, body in packets:
                    self.received.append((kind, flags, body))
                    if kind == mc.CONNECT:
                        conn.sendall(mc.packet(mc.CONNACK, 0, bytes([0, self.code])))
                    elif kind == mc.SUBSCRIBE and self.send:
                        conn.sendall(mc.publish_packet(*self.send))
                    elif kind == mc.DISCONNECT:
                        return
        except OSError:
            pass
        finally:
            conn.close()
            self.server.close()
            self.done.set()


class PacketTest(unittest.TestCase):
    def test_remaining_lengths_take_one_to_three_bytes(self):
        self.assertEqual([mc._length(n) for n in (0, 127, 128, 16383, 16384)],
                         [b"\x00", b"\x7f", b"\x80\x01", b"\xff\x7f", b"\x80\x80\x01"])

    def test_connect_carries_the_account_and_the_will(self):
        data = mc.connect_packet("radio", 60, "me", "secret", will=("r/availability", "offline", True))
        (kind, flags, body), = mc.split_packets(data)[0]
        self.assertEqual(kind, mc.CONNECT)
        self.assertEqual(body[:7], b"\x00\x04MQTT\x04")
        self.assertEqual(body[7], 0x80 | 0x40 | 0x20 | 0x04 | 0x02)
        self.assertEqual(struct.unpack("!H", body[8:10])[0], 60)
        self.assertTrue(body.endswith(b"\x00\x02me\x00\x06secret"))

    def test_packets_are_split_only_once_whole(self):
        one = mc.publish_packet("a/b", "x" * 200, retain=True)
        packets, rest = mc.split_packets(one[:50])
        self.assertEqual((packets, rest), ([], one[:50]))
        packets, rest = mc.split_packets(one + one[:3])
        self.assertEqual((len(packets), packets[0][1], rest), (1, 0x01, one[:3]))


class ClientTest(unittest.TestCase):
    def test_a_session_publishes_subscribes_and_receives_commands(self):
        broker = FakeBroker(send=("rukebox/x/set/next_track", "PRESS"))
        client = mc.Client("127.0.0.1", broker.port, client_id="x", keepalive=30)
        client.connect(timeout=5)
        client.publish("rukebox/x/state", '{"mode": "music"}', retain=True)
        client.subscribe(["rukebox/x/set/#"])
        messages = []
        for _ in range(20):
            messages += client.poll(0.2)
            if messages:
                break
        self.assertEqual(messages, [("rukebox/x/set/next_track", "PRESS")])
        client.close()
        self.assertTrue(broker.done.wait(5))
        kinds = [kind for kind, _, _ in broker.received]
        self.assertEqual(kinds, [mc.CONNECT, mc.PUBLISH, mc.SUBSCRIBE, mc.DISCONNECT])
        self.assertEqual(broker.received[1][1], 0x01, "the state is retained")

    def test_a_refused_account_and_an_absent_broker_are_error_codes(self):
        broker = FakeBroker(code=5)
        with self.assertRaises(mc.MqttError) as caught:
            mc.Client("127.0.0.1", broker.port, username="me", password="bad").connect(timeout=5)
        self.assertEqual(str(caught.exception), "auth_refused")
        closed = socket.socket()
        closed.bind(("127.0.0.1", 0))
        port = closed.getsockname()[1]
        closed.close()
        with self.assertRaises(mc.MqttError) as caught:
            mc.Client("127.0.0.1", port).connect(timeout=2)
        self.assertEqual(str(caught.exception), "unreachable")


class BridgeTest(unittest.TestCase):
    CFG = {"CONTROL_SOCKET": "/tmp/s.sock", "UPNP_NAME": "Cuisine", "SPEECH_LANGUAGE": "fr",
           "MQTT_DISCOVERY_PREFIX": "homeassistant"}

    def test_every_entity_is_discovered_under_this_radio_in_its_language(self):
        docs = mqtt_bridge.discovery(self.CFG, "rukebox_pi", "rukebox/rukebox_pi")
        self.assertIn("homeassistant/number/rukebox_pi/volume/config", docs)
        self.assertIn("homeassistant/button/rukebox_pi/next_track/config", docs)
        volume = docs["homeassistant/number/rukebox_pi/volume/config"]
        self.assertEqual((volume["name"], volume["command_topic"], volume["device"]["name"]),
                         ("Volume", "rukebox/rukebox_pi/set/volume", "Cuisine"))
        self.assertEqual(docs["homeassistant/sensor/rukebox_pi/title/config"]["name"], "Titre")
        ids = [doc["unique_id"] for doc in docs.values()]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(doc["availability_topic"] == "rukebox/rukebox_pi/availability" for doc in docs.values()))

    def test_the_state_comes_from_the_daemons_status(self):
        state = mqtt_bridge.state_of({"mode": "music", "paused": False, "track_title": "Fly",
                                      "track_artist": "Hilary Duff", "volume": 63.6, "muted": True,
                                      "speaker_connected": True})
        self.assertEqual(state, {"mode": "music", "title": "Fly", "artist": "Hilary Duff", "volume": 64,
                                 "muted": True, "playing": True, "speaker": True})
        self.assertFalse(mqtt_bridge.state_of({"mode": "music", "paused": True})["playing"])

    def test_commands_become_the_daemons_own(self):
        with mock.patch.object(mqtt_bridge, "send_control_command", return_value={"ok": True}) as send:
            mqtt_bridge.command(self.CFG, "volume", "42")
            mqtt_bridge.command(self.CFG, "mute", "ON")
            mqtt_bridge.command(self.CFG, "next_track", "PRESS")
            self.assertIsNone(mqtt_bridge.command(self.CFG, "poweroff", "PRESS"), "never switched off from outside")
        self.assertEqual(send.call_args_list, [
            mock.call("/tmp/s.sock", "set_volume", value=42.0, source="mqtt"),
            mock.call("/tmp/s.sock", "set_mute", on=True, source="mqtt"),
            mock.call("/tmp/s.sock", "next_track", source="mqtt"),
        ])
        self.assertEqual(mqtt_bridge.command(self.CFG, "volume", "loud")["error"], "invalid_value")

    def test_the_status_is_written_for_the_page(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        cfg = {"STATE_DIR": folder}
        mqtt_bridge.write_status(cfg, "error", "auth_refused")
        with open(os.path.join(folder, "mqtt_status.json"), encoding="utf-8") as f:
            doc = json.load(f)
        self.assertEqual((doc["state"], doc["error"]), ("error", "auth_refused"))

    def test_it_only_runs_when_switched_on_with_a_broker(self):
        self.assertFalse(mqtt_bridge.wanted({"MQTT_ENABLED": True, "MQTT_HOST": " "}))
        self.assertFalse(mqtt_bridge.wanted({"MQTT_ENABLED": False, "MQTT_HOST": "ha.local"}))
        self.assertTrue(mqtt_bridge.wanted({"MQTT_ENABLED": True, "MQTT_HOST": "ha.local"}))


if __name__ == "__main__":
    unittest.main()
