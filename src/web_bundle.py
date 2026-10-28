"""The page's script served as one: web/js/boot.js, then web/js/init/*.js in name order inside initApp()."""

import os

OPEN = "async function initApp() {\n"
CLOSE = "} // end of initApp()\n"


def parts(web_dir):
    """The files, in the order the script holds them."""
    folder = os.path.join(web_dir, "js")
    inner = os.path.join(folder, "init")
    names = sorted(name for name in os.listdir(inner) if name.endswith(".js"))
    return [os.path.join(folder, "boot.js")] + [os.path.join(inner, name) for name in names]


def stamp(web_dir):
    """What changes when any part does: each file's date and size."""
    out = []
    for path in parts(web_dir):
        info = os.stat(path)
        out.append((path, info.st_mtime_ns, info.st_size))
    return tuple(out)


def app_script(web_dir):
    """The whole script, as the page loads it."""
    texts = []
    for path in parts(web_dir):
        with open(path, encoding="utf-8", newline="") as f:
            texts.append(f.read())
    return texts[0] + OPEN + "".join(texts[1:]) + CLOSE
