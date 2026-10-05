"""Audio outputs (src/audio_output.py): which kind a PipeWire sink is, and which
mpv device a setting names.

The virtual sink is the newest of them and the only one that is not hardware:
it is what a container plays into, and what the network stream encodes. It has
to be recognised by name, because nothing about it looks like a card."""
import unittest

import _path  # noqa: F401
import audio_output


def sink(name, **props):
    props.setdefault("node.name", name)
    props.setdefault("node.description", name)
    return audio_output.classify(props)


class ClassifyTest(unittest.TestCase):
    def test_the_virtual_sink_is_its_own_kind(self):
        self.assertEqual(sink("rukebox_output"), "docker")
        self.assertIn("docker", audio_output.KINDS,
                      "a setting must be able to name it")

    def test_a_bluetooth_sink(self):
        self.assertEqual(sink("bluez_output.AA_BB_CC.1", **{"device.api": "bluez5"}),
                         "bluetooth")

    def test_a_usb_sink(self):
        self.assertEqual(sink("alsa_output.usb-Generic.analog-stereo"), "usb")

    def test_an_hdmi_sink_is_recognised_by_its_description(self):
        self.assertEqual(sink("alsa_output.pci-0000_00.analog-stereo",
                              **{"alsa.card_name": "HDA Intel HDMI"}), "hdmi")

    def test_a_jack_sink(self):
        self.assertEqual(sink("alsa_output.platform-bcm2835.analog-stereo",
                              **{"device.api": "alsa"}), "jack")

    def test_something_else_is_left_alone(self):
        self.assertEqual(sink("some.mystery.node"), "other")


class DeviceTest(unittest.TestCase):
    def sinks(self):
        return [
            {"name": "bluez_output.speaker.1", "description": "Speaker", "kind": "bluetooth",
             "codec": "sbc", "address": None},
            {"name": "rukebox_output", "description": "Rukebox output", "kind": "docker",
             "codec": None, "address": None},
        ]

    def test_the_virtual_sink_is_found_by_its_kind(self):
        self.assertEqual(audio_output.mpv_device("docker", self.sinks()),
                         ("pipewire/rukebox_output", True))

    def test_an_unknown_kind_prefers_the_virtual_sink(self):
        self.assertEqual(audio_output.any_device(self.sinks()),
                         ("pipewire/rukebox_output", True))

    def test_an_unknown_kind_takes_anything_else_when_there_is_no_virtual_sink(self):
        sinks = [s for s in self.sinks() if s["kind"] != "docker"]
        self.assertEqual(audio_output.any_device(sinks),
                         ("pipewire/bluez_output.speaker.1", True))

    def test_nothing_to_play_to_is_said_rather_than_guessed(self):
        self.assertEqual(audio_output.any_device([]), ("auto", False))

    def test_bluetooth_asks_pipewire_for_the_default(self):
        self.assertEqual(audio_output.mpv_device("bluetooth", self.sinks()), ("auto", True))


if __name__ == "__main__":
    unittest.main()
