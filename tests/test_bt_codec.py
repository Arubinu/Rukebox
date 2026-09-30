"""The Bluetooth codecs offered to the speaker (src/bt_codec.py): the setting
becomes a WirePlumber drop-in, and only when it changed."""
import os
import shutil
import tempfile
import unittest

import _path  # noqa: F401
import bt_codec


class ParseTest(unittest.TestCase):
    def test_drops_what_pipewire_does_not_know(self):
        self.assertEqual(bt_codec.parse("sbc,aac,pony"), ["aac", "sbc"])
        self.assertEqual(bt_codec.parse(""), [])
        self.assertEqual(bt_codec.parse(None), [])

    def test_accepts_spaces_semicolons_and_any_case(self):
        self.assertEqual(bt_codec.parse(" SBC ; sbc_xq "), ["sbc_xq", "sbc"])
        self.assertEqual(bt_codec.parse(["AAC", "ldac"]), ["ldac", "aac"])

    def test_no_duplicate(self):
        self.assertEqual(bt_codec.parse("sbc,sbc,sbc"), ["sbc"])


class RenderTest(unittest.TestCase):
    def test_the_list_and_the_sbc_xq_switch(self):
        text = bt_codec.render(["sbc_xq", "sbc"])
        self.assertIn("bluez5.codecs = [ sbc_xq sbc ]", text)
        self.assertIn("bluez5.enable-sbc-xq = true", text)

    def test_sbc_xq_off_when_it_is_not_offered(self):
        self.assertIn("bluez5.enable-sbc-xq = false", bt_codec.render(["sbc"]))

    def test_an_empty_list_still_offers_the_one_codec_every_speaker_has(self):
        self.assertIn("bluez5.codecs = [ sbc ]", bt_codec.render([]))


class WriteTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "20-rukebox-codecs.conf")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_writes_once_and_only_when_the_list_changes(self):
        self.assertTrue(bt_codec.write(path=self.path, codecs=["sbc"]))
        self.assertFalse(bt_codec.write(path=self.path, codecs=["sbc"]))
        self.assertTrue(bt_codec.write(path=self.path, codecs=["sbc_xq", "sbc"]))
        with open(self.path, encoding="utf-8") as handle:
            self.assertIn("sbc_xq sbc", handle.read())

    def test_no_temporary_file_is_left_behind(self):
        bt_codec.write(path=self.path, codecs=["sbc"])
        self.assertEqual(os.listdir(self.dir), ["20-rukebox-codecs.conf"])


if __name__ == "__main__":
    unittest.main()
