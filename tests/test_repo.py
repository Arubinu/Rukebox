"""Rules about the repository itself, each learnt the hard way (CLAUDE.md):
translations complete in the six languages, every recorded event has a
label, API errors are codes and not sentences, every icon asked for has a
drawing, the sudoers file is LF, the two push scripts exclude the same
things, and no endpoint is named like analytics (ad blockers drop those)."""
import os
import re
import unittest

import _path

LANGS = ("en", "fr", "de", "es", "it", "nl")


def i18n_blocks():
    text = _path.read("web", "i18n.js")
    starts = [(m.start(), m.group(1)) for m in re.finditer(r"\n  (\w\w): \{", text)]
    blocks = {}
    for i, (pos, lang) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(text)
        blocks[lang] = set(re.findall(r'\n    "([^"]+)":', text[pos:end]))
    return blocks


class TranslationsTest(unittest.TestCase):
    def test_same_keys_everywhere(self):
        blocks = i18n_blocks()
        self.assertEqual(set(LANGS), set(blocks))
        for lang in LANGS[1:]:
            self.assertEqual(blocks[lang] ^ blocks["en"], set(), lang)

    def test_page_keys_exist(self):
        keys = i18n_blocks()["en"]
        html = _path.read("web", "index.html")
        used = set(re.findall(r'data-i18n(?:-html|-placeholder|-title|-aria-label)?="([^"]+)"', html))
        self.assertEqual(used - keys, set())

    def test_every_event_has_a_label(self):
        app = _path.read("web", "app.js")
        listed = set(re.findall(r'"(\w+)"', app[app.index("const EVENT_TYPE_KEYS"):app.index("function eventLabel")]))
        keys = i18n_blocks()["en"]
        self.assertEqual({k for k in listed if "event." + k not in keys}, set())
        recorded = set()
        for name in os.listdir(_path.SRC):
            if name.endswith(".py"):
                with open(os.path.join(_path.SRC, name), encoding="utf-8") as f:
                    recorded |= set(re.findall(r'\.record\(\s*"(\w+)"', f.read()))
        self.assertEqual(recorded - listed, set(), "add them to EVENT_TYPE_KEYS and event.* in i18n.js")


class ServerRulesTest(unittest.TestCase):
    def test_errors_are_codes(self):
        with open(os.path.join(_path.SRC, "web_server.py"), encoding="utf-8") as f:
            source = f.read()
        self.assertEqual(re.findall(r'"error":\s*"[^"]*\s[^"]*"', source), [])

    def test_no_analytics_like_routes(self):
        with open(os.path.join(_path.SRC, "web_server.py"), encoding="utf-8") as f:
            routes = re.findall(r'@app\.route\("([^"]+)"', f.read())
        bad = [r for r in routes if re.search(r"/(stats|analytics|tracking|track|events|collect|beacon|pixel)(/|$)", r)]
        self.assertEqual(bad, [])


class IconsTest(unittest.TestCase):
    """An icon is `data-icon="name"`, drawn by a `[data-icon="name"]` rule that
    points at a `--i-name` drawing. A name with no rule draws nothing at all,
    silently - the whole element is a mask with no image."""
    def test_every_icon_used_is_defined(self):
        used = set(re.findall(r'data-icon="([^"]+)"', _path.read("web", "index.html")))
        used |= set(re.findall(r'dataset\.icon = "([^"]+)"', _path.read("web", "app.js")))
        css = _path.read("web", "style.css")
        defined = set(re.findall(r'\[data-icon="([^"]+)"\]\s*\{', css))
        self.assertEqual(used - defined, set())

    def test_every_drawing_exists(self):
        css = _path.read("web", "style.css")
        drawings = set(re.findall(r"--i-([\w-]+): url\(", css))
        used = set(re.findall(r"var\(--i-([\w-]+)\)", css))
        self.assertEqual(used - drawings, set())
        self.assertEqual(drawings - used, set())


class FilesTest(unittest.TestCase):
    def test_sudoers_is_lf(self):
        self.assertNotIn("\r", _path.read("config", "sudoers-rukebox"))

    def test_push_scripts_exclude_the_same(self):
        if not os.path.exists(_path.repo_file("bootstrap", "push_update.sh")):
            self.skipTest("bootstrap/ is not installed on a Pi")
        sh =re.findall(r"--exclude='([^']+)'", _path.read("bootstrap", "push_update.sh"))
        ps1 = re.findall(r'"--exclude=([^"]+)"', _path.read("bootstrap", "push_update.ps1"))
        self.assertTrue(sh)
        self.assertEqual(sh, ps1)


class GuestPathsTest(unittest.TestCase):
    def test_the_two_guest_lists_agree(self):
        # app.js refuses a guest call before sending it, from its own copy of
        # the list. A path the server allows and that copy forgets is a button
        # that shows its price and does nothing - which is what happened to
        # pause and skip a sound, added to the server and not to the page.
        source = _path.read("src", "web_server.py")
        start = source.index("_GUEST_PATHS = frozenset({")
        server = set(re.findall(r'"(/api/[^"]*)"', source[start:source.index("})", start)]))

        app = _path.read("web", "app.js")
        start = app.index("GUEST_API_PATHS = [")
        client = set(re.findall(r'"(/api/[^"]*)"', app[start:app.index("];", start)]))
        client = {path for path in client if not path.endswith("/")}

        self.assertTrue(server and client)
        self.assertEqual(server ^ client, set())


if __name__ == "__main__":
    unittest.main()
