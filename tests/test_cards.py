"""RFID cards: the reader's keys become a number, the number finds its card,
and the card starts what it was given."""
import os
import shutil
import struct
import tempfile
import unittest
from unittest import mock

import _path  # noqa: F401
import card_reader
import cards
from speaker_buttons import EVENT_FORMAT
from test_empty_library import DaemonCase

DEVICES = """I: Bus=0003 Vendor=ffff Product=0035 Version=0110
N: Name="Sycreader RFID Technology Co., Ltd SYC ID&IC USB Reader"
H: Handlers=sysrq kbd leds event3

I: Bus=0005 Vendor=0000 Product=0000 Version=0000
N: Name="soundcore Select 4 Go (AVRCP)"
H: Handlers=kbd event2
"""


def key(code, value=1):
    return struct.pack(EVENT_FORMAT, 0, 0, 1, code, value)


class ReaderTest(unittest.TestCase):
    def test_the_reader_is_found_by_its_name(self):
        self.assertEqual(card_reader.find_reader(DEVICES), "/dev/input/event3")
        self.assertEqual(card_reader.find_reader(DEVICES, "soundcore"), "/dev/input/event2")
        self.assertIsNone(card_reader.find_reader(DEVICES.replace("RFID", "x").replace("ID&IC", "x").replace("Sycreader", "x").replace("Reader", "x")))

    def test_keys_make_a_number_and_enter_ends_it(self):
        decoder = card_reader.CardDecoder()
        typed = [decoder.feed(key(c)) for c in (11, 11, 2, 3, 4, 5, 28)]
        self.assertEqual(typed[-1], "001234")
        self.assertIsNone(decoder.feed(key(2, 0)), "a release is not a key")
        self.assertIsNone(decoder.feed(key(28)), "Enter alone is no card")


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "cards.json")

    def test_saved_replaced_and_deleted(self):
        cards.save(self.path, {"id": "0012345678", "name": "Rock", "action": "list", "target": ""})
        cards.save(self.path, {"id": "0012345678", "name": "Rock!", "action": "action", "target": "next"})
        self.assertEqual(cards.load(self.path)["0012345678"]["name"], "Rock!")
        cards.delete(self.path, "0012345678")
        self.assertEqual(cards.load(self.path), {})
        with self.assertRaises(KeyError):
            cards.delete(self.path, "0012345678")

    def test_bad_cards_are_refused(self):
        for bad, code in (({"id": "12", "name": "x", "action": "list"}, "card_bad_id"),
                          ({"id": "1234", "name": "", "action": "list"}, "card_name_required"),
                          ({"id": "1234", "name": "x", "action": "dance"}, "card_bad_action"),
                          ({"id": "1234", "name": "x", "action": "folder"}, "card_target_required")):
            with self.assertRaises(ValueError) as caught:
                cards.validate(bad)
            self.assertEqual(str(caught.exception), code)

    def test_an_unreadable_file_is_never_written_over(self):
        with open(self.path, "w") as f:
            f.write("{broken")
        with self.assertRaises(ValueError):
            cards.save(self.path, {"id": "1234", "name": "x", "action": "list"})
        with open(self.path) as f:
            self.assertEqual(f.read(), "{broken")


class DaemonCardTest(DaemonCase):
    def setUp(self):
        super().setUp()
        self.daemon.cfg["CARDS_FILE"] = os.path.join(self.dir, "cards.json")

    def add(self, **card):
        cards.save(self.daemon.cfg["CARDS_FILE"], dict({"id": "00001111", "name": "Card"}, **card))

    def test_an_unknown_card_is_remembered_for_the_page(self):
        self.assertEqual(self.daemon._card("99998888", "card"), "card_unknown")
        self.assertEqual(self.daemon._last_card_status(),
                         {"id": "99998888", "name": None, "known": False, "error": "card_unknown"})

    def test_a_list_card_plays_its_list(self):
        self.add(action="list", target="evening")
        with mock.patch.object(self.daemon, "_set_active_list", return_value={"ok": True}) as chosen:
            self.assertIsNone(self.daemon._card("00001111", "card"))
        chosen.assert_called_once_with("evening", "card", start=True)

    def test_a_folder_card_plays_that_folder_in_order(self):
        album = os.path.join(self.music, "Album")
        os.makedirs(album)
        for name in ("02 b.mp3", "01 a.mp3"):
            with open(os.path.join(album, name), "wb") as f:
                f.write(b"x")
        with open(os.path.join(self.music, "other.mp3"), "wb") as f:
            f.write(b"x")
        self.add(action="folder", target=album)
        self.daemon.mode = "idle"
        with mock.patch.object(self.daemon, "_start_or_restart_playback") as start:
            self.assertIsNone(self.daemon._card("00001111", "card"))
        start.assert_called_once()
        queue = [os.path.basename(p) for p in self.daemon.state.data["play_queue"]]
        self.assertEqual(queue, ["01 a.mp3", "02 b.mp3"])

    def test_a_folder_outside_the_music_is_refused(self):
        self.add(action="folder", target="/etc")
        self.assertEqual(self.daemon._card("00001111", "card"), "not_found")

    def test_a_button_card_acts_like_a_click(self):
        self.add(action="action", target="next")
        self.daemon.mode = "music"
        with mock.patch.object(self.daemon, "_perform_click_action") as click:
            self.daemon._card("00001111", "card")
        click.assert_called_once_with("card", "card", "next", "none")


if __name__ == "__main__":
    unittest.main()
