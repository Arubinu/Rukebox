#!/usr/bin/env python3
"""Builds dist/rukebox-setup.html, the self-contained card setup page, and dist/rukebox-server.html."""

import argparse
import base64
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
import version as project_version  # noqa: E402

# Must match what version.py hashes, or the release's declared hash is not what an installation records.
PAYLOAD_DIRS = project_version.VERSIONED_DIRS
SKIP_DIRS = {"__pycache__", "graphify-out", ".git", "node_modules"}


def release_stamp(explicit):
    """The tag this page is built from - what the installation records as its version."""
    if explicit:
        return explicit
    try:
        result = subprocess.run(
            ["git", "-C", ROOT, "describe", "--tags", "--always", "--dirty"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def project_archive(release=""):
    """The project as a gzip'd USTAR archive, members under "rukebox/"."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.USTAR_FORMAT, compresslevel=9) as tar:
        for top in PAYLOAD_DIRS:
            base = os.path.join(ROOT, top)
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
                for name in sorted(filenames):
                    if name.endswith((".pyc", ".tmp")):
                        continue
                    path = os.path.join(dirpath, name)
                    arc = "rukebox/" + os.path.relpath(path, ROOT).replace(os.sep, "/")
                    info = tar.gettarinfo(path, arc)
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mode = 0o755 if name.endswith((".sh", ".py")) else 0o644
                    with open(path, "rb") as f:
                        data = f.read()
                    if name.endswith((".sh", ".service", ".template")) or name == "sudoers-rukebox":
                        data = data.replace(b"\r\n", b"\n")
                    info.size = len(data)
                    tar.addfile(info, io.BytesIO(data))
        if release:
            data = (release + "\n").encode()
            info = tarfile.TarInfo("rukebox/RELEASE")
            info.size = len(data)
            info.mode = 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def version():
    sys.path.insert(0, os.path.join(ROOT, "src"))
    try:
        import version as v
        return v.describe(ROOT).get("tree_hash_short", "")
    except Exception:  # noqa: BLE001
        return ""


def installed_hash(archive, workdir):
    """The tree hash a Pi records after installing this page - what a release has
    to declare for the Pi to recognise which version it is running."""
    sys.path.insert(0, os.path.join(ROOT, "src"))
    import version as v
    tmp = os.path.join(workdir, ".rukebox-page-hash")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
            for member in tar.getmembers():
                parts = member.name.split("/", 1)
                if len(parts) != 2 or not parts[1]:
                    continue
                member.name = parts[1]
                try:
                    tar.extract(member, tmp, filter="data")
                except TypeError:  # Python < 3.11.4
                    tar.extract(member, tmp)
        return v.compute_tree_hash(tmp)[0]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def images():
    with open(os.path.join(ROOT, "web", "logo.webp"), "rb") as f:
        logo = "data:image/webp;base64," + base64.b64encode(f.read()).decode()
    with open(os.path.join(ROOT, "web", "title.svg"), "rb") as f:
        title = "data:image/svg+xml;base64," + base64.b64encode(f.read()).decode()
    return logo, title


def as_script(text):
    return text.replace("</", "<\\/")


def write_page(out, page):
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(page)


def build(out, release=""):
    page = read("bootstrap", "setup", "setup.html")
    style = read("web", "style.css") + "\n" + read("bootstrap", "setup", "setup.css")
    logo, title = images()
    archive = project_archive(release)
    payload = {
        "version": version(),
        "project": base64.b64encode(archive).decode(),
        "firstrun": read("bootstrap", "firstrun.sh.template").replace("\r\n", "\n"),
    }
    page = (page.replace("/*STYLE*/", style)
                .replace("{{LOGO}}", logo)
                .replace("{{TITLE}}", title)
                .replace("/*I18N*/", as_script(read("web", "i18n.js")))
                .replace("/*PAYLOAD*/", as_script(json.dumps(payload)))
                .replace("/*SETUP_JS*/", as_script(read("bootstrap", "setup", "setup.js"))))
    write_page(out, page)
    return len(page), len(archive), installed_hash(archive, os.path.dirname(os.path.abspath(out)))


def build_server(out, release=""):
    """The server page carries no project: it names the release the server fetches."""
    page = read("bootstrap", "server", "server.html")
    style = "\n".join((read("web", "style.css"), read("bootstrap", "setup", "setup.css"),
                       read("bootstrap", "server", "server.css")))
    logo, title = images()
    page = (page.replace("/*STYLE*/", style)
                .replace("{{LOGO}}", logo)
                .replace("{{TITLE}}", title)
                .replace("/*I18N*/", as_script(read("web", "i18n.js")))
                .replace("/*PAYLOAD*/", as_script(json.dumps({"release": release})))
                .replace("/*SERVER_JS*/", as_script(read("bootstrap", "server", "server.js"))))
    write_page(out, page)
    return len(page)


def main(argv=None):
    p = argparse.ArgumentParser(description="Build the self-contained card setup page and the server page.")
    p.add_argument("--out", default=os.path.join(ROOT, "dist", "rukebox-setup.html"))
    p.add_argument("--server-out", default=os.path.join(ROOT, "dist", "rukebox-server.html"))
    p.add_argument("--release", default="", help="tag the page is built from (default: git describe)")
    args = p.parse_args(argv)
    release = release_stamp(args.release)
    if release.endswith("-dirty"):
        print("warning: %s - the tree has uncommitted changes, the release will not match the tag."
              % release, file=sys.stderr)
    size, archive, installed = build(args.out, release)
    print("%s: %.1f MB (project archive %.1f MB, release %s)"
          % (args.out, size / 1048576, archive / 1048576, release or "unknown"))
    print("%s: %.1f MB" % (args.server_out, build_server(args.server_out, release) / 1048576))
    print("installed tree hash: %s" % installed)
    print("release badge: https://img.shields.io/badge/tree--hash-%s-blue" % installed[:12])


if __name__ == "__main__":
    main()
