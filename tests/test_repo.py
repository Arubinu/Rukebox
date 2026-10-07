"""Rules about the repository itself, each learnt the hard way (CLAUDE.md):
translations complete in the six languages, every recorded event has a
label, API errors are codes and not sentences, every icon asked for has a
drawing, the sudoers file is LF, the two push scripts exclude the same
things, and no endpoint is named like analytics (ad blockers drop those)."""
import os
import re
import unittest
from urllib.parse import unquote
from xml.etree import ElementTree

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


def i18n_texts():
    """{language: [(key, text), ...]}, duplicates included."""
    text = _path.read("web", "i18n.js")
    starts = [(m.start(), m.group(1)) for m in re.finditer(r"\n  (\w\w): \{", text)]
    found = {}
    for i, (pos, lang) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(text)
        found[lang] = re.findall(r'\n    "([^"]+)":\s*"((?:[^"\\]|\\.)*)"', text[pos:end])
    return found


class TranslationsTest(unittest.TestCase):
    def test_no_key_is_written_twice(self):
        for lang, pairs in i18n_texts().items():
            keys = [key for key, _text in pairs]
            twice = sorted({key for key in keys if keys.count(key) > 1})
            self.assertEqual(twice, [], "%s: the second one silently wins" % lang)

    def test_placeholders_are_the_same_in_every_language(self):
        texts = {lang: dict(pairs) for lang, pairs in i18n_texts().items()}
        wrong = []
        for key, english in texts["en"].items():
            wanted = sorted(set(re.findall(r"\{(\w+)\}", english)))
            for lang in LANGS[1:]:
                got = sorted(set(re.findall(r"\{(\w+)\}", texts[lang].get(key, english))))
                if got != wanted:
                    wrong.append((lang, key, got, wanted))
        self.assertEqual(wrong, [])

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

    def test_every_listed_service_has_a_name(self):
        server = _path.read("src", "web_server.py")
        block = server[server.index("SYSTEM_SERVICES = ("):server.index("_SERVICE_PROPS")]
        keys = i18n_blocks()["en"]
        self.assertEqual({name for name in re.findall(r'\("([\w-]+)"', block) if "svc." + name not in keys}, set())


class ServerRulesTest(unittest.TestCase):
    def test_errors_are_codes(self):
        """In every module: what the daemon and its client answer reaches the page too."""
        sentences = []
        for name in sorted(os.listdir(_path.SRC)):
            if name.endswith(".py"):
                with open(os.path.join(_path.SRC, name), encoding="utf-8") as f:
                    sentences += [(name, found) for found in
                                  re.findall(r'"error":\s*f?"[^"]*\s[^"]*"', f.read())]
        self.assertEqual(sentences, [])

    def test_no_analytics_like_routes(self):
        with open(os.path.join(_path.SRC, "web_server.py"), encoding="utf-8") as f:
            routes = re.findall(r'@app\.route\("([^"]+)"', f.read())
        bad = [r for r in routes if re.search(r"/(stats|analytics|tracking|track|events|collect|beacon|pixel)(/|$)", r)]
        self.assertEqual(bad, [])


class IconsTest(unittest.TestCase):
    """An icon is `data-icon="name"`, drawn by a `[data-icon="name"]` rule that
    points at a `--i-name` drawing. A name with no rule draws nothing at all,
    silently - the whole element is a mask with no image."""
    def test_the_rule_that_draws_every_icon_is_whole(self):
        css = _path.read("web", "style.css")
        rule = re.search(r"\[data-icon\]::before\s*\{([^}]*)\}", css)
        self.assertIsNotNone(rule, "the one rule that draws every icon")
        self.assertIn("mask: var(--icon)", rule.group(1))
        self.assertIsNone(re.search(r",[ \t]*\n[ \t]*\n", css),
                          "a selector list cut off from its rule (a comma, then a blank line)")

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

    def test_every_drawing_is_a_readable_svg(self):
        """A drawing is a data URI written by hand: one unescaped character in
        it and the icon is silently blank, which no other check would see."""
        css = _path.read("web", "style.css")
        drawings = re.findall(r"--i-([\w-]+): url\(\"data:image/svg\+xml,([^\"]+)\"\)", css)
        self.assertTrue(drawings)
        for name, payload in drawings:
            root = ElementTree.fromstring(unquote(payload))
            self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg", name)
            self.assertEqual(root.get("viewBox"), "0 0 24 24", name)

    def test_two_pages_of_one_area_do_not_share_an_icon(self):
        """The pages of an area sit side by side in its menu, so one drawing
        for two of them says nothing about either. Reported on 2026-09-30:
        "Now Playing" and "Library" were both a music note."""
        tab = None
        seen = {}
        clashes = []
        for line in _path.read("web", "index.html").splitlines():
            section = re.search(r'<section[^>]*\bdata-tab="([^"]+)"', line)
            if section:
                tab = section.group(1)
            icon = re.search(r'<h2 data-icon="([^"]+)"', line)
            if not icon or not tab:
                continue
            name = re.search(r'id="([^"]+)"', line)
            name = name.group(1) if name else line.strip()
            clash = seen.get((tab, icon.group(1)))
            if clash:
                clashes.append("%s and %s both draw \"%s\" in the %s area"
                               % (clash, name, icon.group(1), tab))
            seen[(tab, icon.group(1))] = name
        self.assertEqual(clashes, [])


class FilesTest(unittest.TestCase):
    def test_sudoers_is_lf(self):
        self.assertNotIn("\r", _path.read("config", "sudoers-rukebox"))

    def test_the_container_files_are_lf_too(self):
        """A shell script or a Dockerfile with CRLF does not run: the image
        builds on Linux, from a checkout made anywhere."""
        for parts in (("docker", "entrypoint.sh"), ("docker", "supervisor.py"),
                      ("docker", "pipewire-container.conf"), ("Dockerfile",),
                      ("docker", "compose.stream.yml")):
            self.assertNotIn("\r", _path.read(*parts), "/".join(parts))

    def test_every_root_the_container_uses_is_a_documented_one(self):
        """The image moves the four roots by name; a typo there would silently
        leave the container writing to /etc/rukebox."""
        dockerfile = _path.read("Dockerfile")
        for name in ("RUKEBOX_CONFIG_DIR", "RUKEBOX_STATE_DIR", "RUKEBOX_MUSIC_DIR",
                     "RUKEBOX_INSTALL_DIR", "RUKEBOX_PLATFORM"):
            self.assertIn(name, dockerfile, name)

    def test_the_compose_variants_all_use_the_published_image(self):
        import glob as globmod

        variants = sorted(os.path.basename(p) for p in
                          globmod.glob(_path.repo_file("docker", "compose.*.yml")))
        self.assertEqual(len(variants), 4, variants)
        for name in variants:
            text = _path.read("docker", name)
            self.assertIn("ghcr.io/arubinu/rukebox", text, name)
            self.assertIn("/config:/config", text, name)
            self.assertIn("/data:/data", text, name)

    def test_the_community_scripts_are_present_and_well_formed(self):
        """Three files, written to community-scripts' rules: the day this is
        proposed upstream they are copied, not rewritten. They cannot be run
        from here - there is no Proxmox host in the test suite - so what is
        checked is that they are there, complete and JSON-valid."""
        import json as jsonmod

        for parts in (("community-scripts", "ct", "rukebox.sh"),
                      ("community-scripts", "install", "rukebox-install.sh"),
                      ("community-scripts", "json", "rukebox.json"),
                      ("community-scripts", "README.md")):
            self.assertTrue(os.path.exists(_path.repo_file(*parts)), "/".join(parts))

        ct = _path.read("community-scripts", "ct", "rukebox.sh")
        for needle in ('APP="Rukebox"', "header_info", "var_cpu=", "var_ram=",
                       "var_disk=", "start", "build_container", "description",
                       "function update_script"):
            self.assertIn(needle, ct, needle)
        install = _path.read("community-scripts", "install", "rukebox-install.sh")
        for needle in ("setting_up_container", "network_check", "update_os",
                       "motd_ssh", "customize", "cleanup_lxc", "RUKEBOX_PROFILE=lxc"):
            self.assertIn(needle, install, needle)

        fiche = jsonmod.loads(_path.read("community-scripts", "json", "rukebox.json"))
        self.assertEqual(fiche["name"], "Rukebox")
        self.assertEqual(fiche["slug"], "rukebox")
        self.assertEqual(fiche["type"], "ct")
        self.assertIn("install_methods", fiche)
        # The JSON points at the script the ct/ file installs.
        self.assertIn("rukebox-install", _path.read("community-scripts", "ct", "rukebox.sh"))

    def test_every_readme_says_a_container_is_possible(self):
        for lang in ("", ".fr", ".de", ".es", ".it", ".nl"):
            text = _path.read("README%s.md" % lang)
            self.assertIn("docker compose", text, lang)
            self.assertIn("docs/guide.md#running-in-a-container-docker", text, lang)

    def test_push_scripts_exclude_the_same(self):
        """Four lists, one archive: an update and a first install pack the
        same tree, from a shell or from PowerShell."""
        if not os.path.exists(_path.repo_file("bootstrap", "push_update.sh")):
            self.skipTest("bootstrap/ is not installed on a Pi")
        wanted = re.findall(r"--exclude='([^']+)'", _path.read("bootstrap", "push_update.sh"))
        self.assertTrue(wanted)
        self.assertIn("./.venv", wanted, "a bare name excludes nothing: the members are ./...")
        for name in ("push_update.ps1", "push_install.sh", "push_install.ps1"):
            pattern = r'"--exclude=([^"]+)"' if name.endswith(".ps1") else r"--exclude='([^']+)'"
            self.assertEqual(re.findall(pattern, _path.read("bootstrap", name)), wanted, name)


class PlatformCardsTest(unittest.TestCase):
    """A card hidden by a capability needs its CSS rule.

    The stylesheet cannot compare two attributes, so one rule per capability is
    what hides a card the machine cannot honour; the menu entry goes through
    pageIsAvailable() instead. A card whose capability has no rule is a page
    that is offered and then refuses - what the Clock card did on a container.
    """

    def test_every_capability_a_card_asks_for_has_its_rule(self):
        html = _path.read("web", "index.html")
        css = _path.read("web", "style.css")
        needs = set()
        for tag in re.findall(r"<[^>]*>", html):
            if 'class="card' not in tag:
                continue
            found = re.search(r'data-needs="([^"]+)"', tag)
            if found:
                needs.update(found.group(1).split())
        self.assertTrue(needs, "the cards ask for capabilities")
        for name in sorted(needs):
            rule = 'body:not([data-caps~="%s"]) .card[data-needs~="%s"]' % (name, name)
            self.assertIn(rule, css, "%s needs a rule to hide its card" % name)


class GuestPathsTest(unittest.TestCase):
    def test_the_two_guest_lists_agree(self):
        # A path the server allows and app.js forgets is a button that does nothing.
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
