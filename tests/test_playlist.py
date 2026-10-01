"""Play orders (src/playlist.py): every mode plays every file once; an
unknown mode degrades to "ordered" rather than silencing the radio."""
import unittest

import _path  # noqa: F401
import playlist

ROOT = "/music"
FILES = ["/music/B/2 b.mp3", "/music/A/10 a.mp3", "/music/A/2 a.mp3", "/music/C/1 c.mp3", "/music/x.mp3"]


class PlaylistTest(unittest.TestCase):
    def test_every_mode_is_a_permutation(self):
        for mode in ("random", "random_albums", "ordered"):
            self.assertEqual(sorted(playlist.order_files(FILES, ROOT, mode)), sorted(FILES), mode)

    def test_ordered_is_natural(self):
        order = playlist.order_files(FILES, ROOT, "ordered")
        self.assertLess(order.index("/music/A/2 a.mp3"), order.index("/music/A/10 a.mp3"))

    def test_random_albums_never_repeats_an_artist(self):
        # Spreads each artist (sub-folder) whenever that is arithmetically possible.
        for _ in range(50):
            order = playlist.order_files(FILES, ROOT, "random_albums")
            keys = [playlist.group_key(p, ROOT) for p in order]
            self.assertFalse(any(a == b for a, b in zip(keys, keys[1:])), order)

    def test_a_hand_made_order_is_kept(self):
        # "ordered" is the order the list was built in, not the file names.
        order = playlist.order_files(FILES, ROOT, "ordered", ["1 c.mp3", "2 b.mp3"])
        self.assertEqual(order[:2], ["/music/C/1 c.mp3", "/music/B/2 b.mp3"])
        self.assertEqual(sorted(order), sorted(FILES), "every file is still there")

    def test_unknown_mode(self):
        self.assertEqual(playlist.order_files(FILES, ROOT, "typo"), playlist.order_files(FILES, ROOT, "ordered"))


if __name__ == "__main__":
    unittest.main()
