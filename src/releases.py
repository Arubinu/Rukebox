"""Which published release a radio may move to, and what changed on the way there.

A radio follows its own major version. Only once it runs the newest release of
that major is the next one offered, with what it implies, and never one beyond."""

import json
import re
import sys
import urllib.error
import urllib.request

import version

LIST_URL = "https://api.github.com/repos/%s/releases?per_page=100"
HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "Rukebox"}
# Each release may carry its notes in every language of the interface.
NOTES_ASSET = "notes.json"
DEFAULT_REPO = "Arubinu/Rukebox"
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
# A release is named by its number alone: "v3.0.0-rc1" is a test, flagged or not.
_RELEASE_TAG_RE = re.compile(r"^[vV]?\d+\.\d+\.\d+$")
_TREE_HASH_RE = re.compile(r"(?:tree-hash:\s*|badge/[^\s)\"']*?)([0-9a-f]{8,64})", re.IGNORECASE)
# A long jump lists this many releases at most: the newest ones.
MAX_CHANGES = 12


class LookupFailed(Exception):
    """GitHub could not be asked or gave no usable answer; `code` says which."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def valid_repo(repo):
    repo = str(repo or "").strip()
    return bool(_REPO_RE.match(repo)) and not any(part.strip(".") == "" for part in repo.split("/"))


def fetch(repo, timeout=8):
    """The repository's releases, as GitHub lists them."""
    if not valid_repo(repo):
        raise LookupFailed("bad_github_repo")
    request = urllib.request.Request(LIST_URL % repo.strip(), headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            data = json.loads(reply.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise LookupFailed("release_none" if error.code == 404 else "github_error") from error
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise LookupFailed("no_internet") from error
    if not isinstance(data, list):
        raise LookupFailed("github_error")
    return data


def published(releases):
    """[(parts, release)] of the real releases, newest first: drafts, pre-releases
    and tags that name no version are left out."""
    found = []
    for release in releases or []:
        if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
            continue
        name = str(release.get("tag_name") or "")
        if _RELEASE_TAG_RE.match(name):
            found.append((version.tag_parts(name), release))
    found.sort(key=lambda item: item[0], reverse=True)
    return found


def newest_of(releases, major):
    """The newest release of one major version, or None."""
    return next((release for parts, release in published(releases) if parts[0] == major), None)


def declared_tree_hash(notes):
    """The tree hash a release declares in its notes, if it declares one."""
    match = _TREE_HASH_RE.search(notes or "")
    return match.group(1).lower() if match else None


def recognised(releases, installed, tree_hash):
    """The release this radio runs: the one whose notes declare its tree, else
    the one it was installed as."""
    tree = str(tree_hash or "").lower()
    if tree:
        for _parts, release in published(releases):
            declared = declared_tree_hash(release.get("body"))
            if declared and tree.startswith(declared):
                return release["tag_name"]
    return str(installed or "").strip()


def _here(installed):
    # A tree that was never given a release name is taken as the start of its own major.
    parts = version.tag_parts(installed)
    if parts is None:
        return (version.MAJOR, 0, 0, 0)
    # A development build (commits past its tag) whose code is a newer major than the tag is
    # ahead of that major's first release: never sent back to the major before.
    if parts[0] < version.MAJOR and parts[3] > 0:
        return (version.MAJOR, 0, 0, -1)
    return parts


def target(releases, installed):
    """(release, crosses): what to offer this radio, and whether it is the next
    major version; (None, False) when it already runs the newest it may have."""
    here = _here(installed)
    found = published(releases)
    own = next(((parts, release) for parts, release in found if parts[0] == here[0]), None)
    if own and own[0] > here:
        return own[1], False
    following = next((release for parts, release in found if parts[0] == here[0] + 1), None)
    return (following, True) if following else (None, False)


def refusal(releases, installed, tag, major=False):
    """Why `tag` may not be installed here, as a code, or None when it may."""
    parts = version.tag_parts(tag)
    if not parts:
        return "release_unknown"
    here = _here(installed)
    if parts[0] < here[0]:
        return "major_backwards"
    if parts[0] == here[0]:
        return None
    if parts[0] > here[0] + 1:
        return "major_skipped"
    own = newest_of(releases, here[0])
    if own and version.tag_parts(own["tag_name"]) > here:
        return "newest_first"
    return None if major else "major_needs_confirmation"


def changes(releases, installed, tag):
    """The releases after the installed one up to `tag`, newest first."""
    here, last = _here(installed), version.tag_parts(tag)
    if not last:
        return []
    return [release for parts, release in published(releases) if here < parts <= last][:MAX_CHANGES]


def notes_url(release):
    """Where the release keeps its notes in every language, or ""."""
    for asset in release.get("assets") or []:
        if isinstance(asset, dict) and asset.get("name") == NOTES_ASSET:
            return str(asset.get("browser_download_url") or "")
    return ""


def fetch_notes(release, timeout=8):
    """{"notes": {lang: text}, "upgrade": {lang: text}} of a release, or {}."""
    url = notes_url(release)
    if not url.startswith("https://"):
        return {}
    request = urllib.request.Request(url, headers={"User-Agent": "Rukebox"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            data = json.loads(reply.read(512 * 1024).decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def in_language(texts, lang, fallback=""):
    """The text in the reader's language, else in English, else the fallback."""
    if not isinstance(texts, dict):
        return fallback
    for key in (lang, "en"):
        text = texts.get(key)
        if isinstance(text, str) and text.strip():
            return text
    return fallback


def installed_release(version_file, releases):
    """The release a radio runs, from its version file and the published notes."""
    info = version.read_version_file(version_file)
    return recognised(releases, info.get("release"), info.get("tree_hash"))


def _main(argv):
    """Usage, for the updater (messages on stderr, a tag on stdout):
    releases.py target <repo> <version_file> [--major]   exit 3: nothing newer, 4: needs --major
    releases.py check  <repo> <version_file> <tag> [--major]   exit 5: refused
    """
    major = "--major" in argv
    args = [arg for arg in argv[1:] if arg != "--major"]
    if len(args) not in (3, 4) or args[0] not in ("target", "check"):
        print(_main.__doc__, file=sys.stderr)
        return 2
    try:
        listed = fetch(args[1])
    except LookupFailed as error:
        print("could not list the releases of github.com/%s (%s)" % (args[1], error.code), file=sys.stderr)
        return 2
    installed = installed_release(args[2], listed)
    if args[0] == "check":
        why = refusal(listed, installed, args[3], major)
        if why:
            print("%s cannot be installed over %s (%s)" % (args[3], installed or "this version", why),
                  file=sys.stderr)
            return 5
        print(args[3])
        return 0
    release, crosses = target(listed, installed)
    if release is None:
        print("already the newest release this radio may have (%s)" % (installed or "unnamed"),
              file=sys.stderr)
        return 3
    if crosses and not major:
        print("%s is a new major version: it is installed only once confirmed (--major)"
              % release["tag_name"], file=sys.stderr)
        return 4
    print(release["tag_name"])
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
