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
        # random_albums spreads each artist (sub-folder): never two of the
        # same one in a row, whenever that is arithmetically possible.
        for _ in range(50):
            order = playlist.order_files(FILES, ROOT, "random_albums")
            keys = [playlist.group_key(p, ROOT) for p in order]
            self.assertFalse(any(a == b for a, b in zip(keys, keys[1:])), order)

    def test_unknown_mode(self):
        self.assertEqual(playlist.order_files(FILES, ROOT, "typo"), playlist.order_files(FILES, ROOT, "ordered"))


if __name__ == "__main__":
    unittest.main()
