"""Reads and writes rukebox.yaml, and generates rukebox.env from it."""

import logging
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config_schema import (  # noqa: E402
    BY_ENV, DEFAULTS, SETTINGS, sections_with_settings, to_raw,
)
import paths  # noqa: E402

log = logging.getLogger("config")

# The root every path setting is resolved against, so the YAML is looked for
# under RUKEBOX_CONFIG_DIR (/etc/rukebox by default). Recomputed at every
# entry point rather than captured once, and never as a default argument: the
# test suite moves the root after the import, and a value captured at import
# would ignore it.
YAML_FILE_CANDIDATES = []
ENV_FILE = ""
# The environment the two globals above are a snapshot of; None forces the
# first call to compute them.
_resolved_env = None


def _path_env():
    """The environment a root is resolved from."""
    return (paths.config_dir(), os.environ.get("RUKEBOX_ENV_FILE"),
            os.environ.get("RUKEBOX_YAML_FILE"))


def _refresh_paths(force=False):
    """Recomputes the two paths when the root moved.

    Only when it actually moved: the tests point the root at their own
    sandbox by rebinding the globals below, and recomputing on every call
    would throw that away."""
    global ENV_FILE, YAML_FILE_CANDIDATES, _resolved_env
    current = _path_env()
    if not force and current == _resolved_env:
        return
    _resolved_env = current
    YAML_FILE_CANDIDATES = paths.yaml_candidates()
    ENV_FILE = paths.env_file()


_refresh_paths()

HEADER = """\
# ============================================================
#  Rukebox Zero - configuration
#
#  THIS is the file to edit. /etc/rukebox/rukebox.env is generated
#  from it for systemd and the shell scripts - editing that one
#  has no lasting effect, it is overwritten on every sync.
#
#  After changing anything here:
#      sudo systemctl restart rukebox-config.service
#      sudo systemctl restart rukebox-daemon.service rukebox-web.service
#  (the web interface does both for you when it saves)
# ============================================================
"""


def _yaml_module():
    try:
        import yaml  # noqa: F401
        return yaml
    except ImportError:
        return None


def find_yaml_file(path=None):
    if path:
        return path
    _refresh_paths()
    for candidate in YAML_FILE_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    return YAML_FILE_CANDIDATES[0]


def _scalar(setting, raw):
    """YAML scalar for a stored string."""
    if setting.type == "bool":
        return "true" if str(raw).strip().lower() in ("1", "true", "yes", "on") else "false"
    if setting.type in ("int", "float"):
        text = str(raw).strip()
        if re.fullmatch(r"-?\d+(\.\d+)?", text):
            return text
        return '"%s"' % text
    text = "" if raw is None else str(raw)
    return '"%s"' % text.replace("\\", "\\\\").replace('"', '\\"')


def _render_setting(setting, values, blank_line=False):
    """The comment block and the `key."""
    out = []
    if setting.comment:
        for line in setting.comment.split("\n"):
            out.append("  # %s\n" % line if line else "  #\n")
    raw = values.get(setting.env, setting.default)
    out.append("  %s: %s\n" % (setting.key, _scalar(setting, raw)))
    if blank_line:
        out.append("\n")
    return out

def render_template(values=None):
    """The full, commented YAML document.

    With no `values`, the documented defaults are written - the Pi's own, which
    is what the checkout's config/rukebox.yaml holds. A first start passes
    DEFAULTS instead, so the file a machine writes for itself carries the paths
    that machine actually uses (RUKEBOX_CONFIG_DIR and the other three roots)."""
    values = values if values is not None else {}
    out = [HEADER]
    for name, description, settings in sections_with_settings():
        out.append("\n# ---------------------------------------------------------------\n")
        out.append("# %s\n" % description)
        out.append("# ---------------------------------------------------------------\n")
        out.append("%s:\n" % name)
        for setting in settings:
            out.extend(_render_setting(setting, values, blank_line=True))
        if out[-1] == "\n":
            out.pop()
    return "".join(out)


def _read_with_pyyaml(path, yaml):
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        return {}
    values = {}
    for setting in SETTINGS:
        section = data.get(setting.section)
        if isinstance(section, dict) and setting.key in section:
            value = section[setting.key]
            if value is None:
                value = ""
            elif isinstance(value, bool):
                value = "true" if value else "false"
            values[setting.env] = str(value)
    return values


def read_env_file(path=None):
    """The generated flat file."""
    _refresh_paths()
    path = path or ENV_FILE
    values = {}
    if not os.path.exists(path):
        return values
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                    value = value[1:-1]
                    if line.strip()[len(key) + 1] == '"':
                        value = value.replace('\\"', '"').replace("\\\\", "\\")
                values[key] = value
    except OSError:
        log.exception("Could not read %s", path)
    return values


def read_values(path=None, env_path=None):
    """Every configured setting, as {ENV_KEY."""
    env_path = env_path or ENV_FILE
    path = find_yaml_file(path)
    yaml = _yaml_module()

    if os.path.exists(path) and yaml is not None:
        try:
            return _read_with_pyyaml(path, yaml)
        except Exception:  # noqa: BLE001
            log.exception("%s is not valid YAML, falling back to %s", path, env_path)

    if os.path.exists(path) and yaml is None:
        log.warning(
            "PyYAML is not installed: reading %s instead of %s. Hand edits to "
            "the YAML file will apply at the next sync. Install it with "
            "'sudo apt-get install -y python3-yaml'.", env_path, path,
        )

    return read_env_file(env_path)


_KEY_LINE = re.compile(r"^(?P<indent>\s*)(?P<key>[A-Za-z0-9_]+)\s*:(?P<rest>.*)$")


def _strip_inline_comment(rest):
    """Splits `value # comment` keeping quotes intact."""
    in_single = in_double = False
    for i, ch in enumerate(rest):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            if i == 0 or rest[i - 1] in " \t":
                return rest[:i], rest[i:]
    return rest, ""


def write_values(updates, path=None, env_path=None):
    """Saves settings, preserving comments, ordering and every line that is not
    being changed."""
    unknown = [k for k in updates if k not in DEFAULTS]
    if unknown:
        raise ValueError("Unknown configuration key(s): %s" % sorted(unknown))

    env_path = env_path or ENV_FILE
    path = find_yaml_file(path)
    if not os.path.exists(path):
        current = read_values(path, env_path)
        current.update({k: to_raw(BY_ENV[k], v) for k, v in updates.items()})
        _atomic_write(path, render_template(current))
        write_env_file(path, env_path, values=current)
        return sorted(updates)

    current = read_values(path, env_path)
    current.update({k: to_raw(BY_ENV[k], v) for k, v in updates.items()})

    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    wanted = {}
    for env_key, value in updates.items():
        setting = BY_ENV[env_key]
        wanted.setdefault(setting.section, {})[setting.key] = (setting, to_raw(setting, value))

    changed = []
    section = None
    seen = set()
    for i, line in enumerate(lines):
        match = _KEY_LINE.match(line.rstrip("\n"))
        if not match:
            continue
        indent, key, rest = match.group("indent"), match.group("key"), match.group("rest")

        if not indent:
            section = key
            continue
        if section not in wanted or key not in wanted[section]:
            continue

        setting, raw = wanted[section][key]
        value_part, comment_part = _strip_inline_comment(rest)
        new_scalar = _scalar(setting, raw)
        if value_part.strip() == new_scalar:
            seen.add(setting.env)
            continue
        lines[i] = "%s%s: %s%s\n" % (indent, key, new_scalar, comment_part)
        changed.append(setting.env)
        seen.add(setting.env)

    missing = [BY_ENV[k] for k in updates if k not in seen]
    if missing:
        _insert_settings(lines, missing,
                         {k: to_raw(BY_ENV[k], v) for k, v in updates.items()})
        changed.extend(s.env for s in missing)

    _atomic_write(path, "".join(lines))
    write_env_file(path, env_path, values=current)
    return sorted(set(changed))


def _section_bounds(lines):
    """{section: (first line, line after its last setting)} for the top-level
    mappings already in the document."""
    bounds = {}
    current = None
    start = last_content = 0
    for i, line in enumerate(lines):
        stripped = line.rstrip("\n")
        if not stripped.strip() or stripped.strip().startswith("#"):
            continue
        match = _KEY_LINE.match(stripped)
        if match and not match.group("indent"):
            if current is not None:
                bounds[current] = (start, last_content + 1)
            current = match.group("key")
            start = last_content = i
        elif current is not None:
            last_content = i
    if current is not None:
        bounds[current] = (start, last_content + 1)
    return bounds


def _insert_settings(lines, settings, values):
    """Adds settings to an existing document, each inside its own section when
    that section is already present."""
    by_section = {}
    for setting in settings:
        by_section.setdefault(setting.section, []).append(setting)

    bounds = _section_bounds(lines)
    insertions = []
    tail = []

    for name, description, _all in sections_with_settings():
        if name not in by_section:
            continue
        block = []
        for setting in by_section[name]:
            block.extend(_render_setting(setting, values))
        if name in bounds:
            insertions.append((bounds[name][1], block))
        else:
            tail.append("\n")
            tail.append("# %s\n" % description)
            tail.append("%s:\n" % name)
            tail.extend(block)

    for index, block in sorted(insertions, key=lambda item: -item[0]):
        lines[index:index] = block
    lines.extend(tail)
    return lines


def merge_missing(path=None, env_path=None):
    """Adds settings a new version introduced, with their documentation,
    without modifying a single existing value."""
    env_path = env_path or ENV_FILE
    path = find_yaml_file(path)
    if not os.path.exists(path):
        return []
    present = read_values(path, env_path)
    missing = [s.env for s in SETTINGS if s.env not in present]
    if not missing:
        return []
    merged_values = dict(present)
    merged_values.update({k: DEFAULTS[k] for k in missing})

    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    # `merged_values`, not the documented defaults: a setting added to an
    # existing file is added to a machine, and that machine's own roots are in
    # DEFAULTS. The template stays the Pi's - this is not the template.
    _insert_settings(lines, [BY_ENV[k] for k in missing], merged_values)
    _atomic_write(path, "".join(lines))
    write_env_file(path, env_path, values=merged_values)
    return missing


def _atomic_write(path, text):
    """Writes through a temporary file and a rename, mode 600: the YAML holds
    the web password hash and the session secret, and rukebox.env repeats
    every setting."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    secure_file(tmp, directory or ".")
    os.replace(tmp, path)


def secure_file(path, owner_of="."):
    """Mode 600, and the owner of `owner_of` when running as root."""
    try:
        os.chmod(path, 0o600)
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            st = os.stat(owner_of)
            os.chown(path, st.st_uid, st.st_gid)
    except OSError:
        pass


def _shell_quote(value):
    """Single-quoted, which both systemd's EnvironmentFile parser and `source`
    in bash read the same way."""
    text = str(value).replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    return "'" + text.replace("'", "'\\''") + "'"


def write_env_file(yaml_path=None, env_path=None, values=None):
    """Regenerates the flat file systemd and the shell scripts read."""
    env_path = env_path or ENV_FILE
    merged = dict(DEFAULTS)
    merged.update(values if values is not None else read_values(yaml_path, env_path))
    values = merged

    lines = [
        "# GENERATED FILE - DO NOT EDIT.\n",
        "# Written from %s by src/config_file.py.\n" % find_yaml_file(yaml_path),
        "# Any change here is lost at the next sync; edit the YAML file.\n",
        "#\n",
        "# Read by systemd (EnvironmentFile=) and sourced by the shell scripts.\n",
        "\n",
    ]
    for setting in SETTINGS:
        lines.append("%s=%s\n" % (setting.env, _shell_quote(values.get(setting.env, setting.default))))
    _atomic_write(env_path, "".join(lines))
    return env_path


def _match_owner(path, reference):
    """Gives `path` the owner of `reference` when running as root."""
    try:
        if hasattr(os, "geteuid") and os.geteuid() == 0 and os.path.exists(path):
            st = os.stat(reference)
            os.chown(path, st.st_uid, st.st_gid)
    except OSError:
        pass


def _announcement_paths(yaml_path, env_path):
    values = dict(DEFAULTS)
    values.update(read_values(yaml_path, env_path))
    return values["ANNOUNCEMENTS_FILE"], values["TRACK_ORDER_FILE"], values


def seed_announcements(yaml_path=None, env_path=None):
    """A brand new radio is offered the morning and double-click announcement
    types (announcements.seed_defaults)."""
    import announcements  # noqa: E402

    yaml_path = find_yaml_file(yaml_path)
    env_path = env_path or ENV_FILE
    ann_path, _, _ = _announcement_paths(yaml_path, env_path)
    # The audio root this machine actually uses, not the template's: a container
    # keeps its tree elsewhere, and the folders seeded here are the ones the
    # interface must be allowed to write to.
    values = dict(DEFAULTS)
    values.update(read_values(yaml_path, env_path))
    audio_root = os.path.dirname(str(values.get("MEME_DIR") or "").strip()) \
        or announcements.DEFAULT_AUDIO_ROOT
    if announcements.seed_defaults(ann_path, audio_root):
        _match_owner(ann_path, os.path.dirname(yaml_path) or ".")
        return True
    return False


def retarget_announcements(old_root, new_root, yaml_path=None, env_path=None):
    """Moves the announcement folders seeded from `old_root` to `new_root`."""
    import announcements  # noqa: E402

    yaml_path = find_yaml_file(yaml_path)
    ann_path, _, _ = _announcement_paths(yaml_path, env_path or ENV_FILE)
    return announcements.retarget(ann_path, old_root, new_root)


def retarget_folders(old_root, new_root, yaml_path=None, env_path=None):
    """Moves the `folders` settings that name a path under `old_root` to
    `new_root`, and answers the keys that changed.

    A setting a version adds arrives with the template's path - the Pi's own.
    A container keeps its sounds elsewhere (and the interface only writes in
    the folders the settings name), so a folder left at the Pi's path points at
    nothing and lets the interface write outside the tree it belongs in."""
    import config_schema  # noqa: E402

    old_root = str(old_root or "").strip().rstrip("/")
    new_root = str(new_root or "").strip().rstrip("/")
    if not old_root or not new_root or old_root == new_root:
        return []
    yaml_path = find_yaml_file(yaml_path)
    env_path = env_path or ENV_FILE
    values = dict(DEFAULTS)
    values.update(read_values(yaml_path, env_path))
    updates = {}
    for setting in config_schema.SETTINGS:
        if setting.section != "folders":
            continue
        text = str(values.get(setting.env) or "").strip()
        if text == old_root or text.startswith(old_root + "/"):
            updates[setting.env] = new_root + text[len(old_root):]
    return write_values(updates, path=yaml_path, env_path=env_path) if updates else []


def ensure_file(yaml_path=None, env_path=None):
    """Creates the configuration if it does not exist yet, or adds the settings
    a new version introduced."""
    env_path = env_path or ENV_FILE
    yaml_path = find_yaml_file(yaml_path)
    if not os.path.exists(yaml_path):
        # DEFAULTS, not the bare Setting defaults: the file this machine writes
        # for itself names the paths this machine uses.
        _atomic_write(yaml_path, render_template(DEFAULTS))
        write_env_file(yaml_path, env_path, values=dict(DEFAULTS))
        seed_announcements(yaml_path, env_path)
        return "created", 0
    added = merge_missing(yaml_path, env_path)
    write_env_file(yaml_path, env_path)
    secure_file(yaml_path, os.path.dirname(yaml_path) or ".")
    # The setup page creates the YAML first: without this seeding its sounds belong to nothing.
    seed_announcements(yaml_path, env_path)
    return ("updated", len(added)) if added else ("unchanged", 0)


def _main(argv):
    """Usage:
    config_file.py sync     [yaml] [env]   regenerate the env file
    config_file.py ensure   [yaml] [env]   create or merge, then sync
    config_file.py show     [yaml]         print the effective values
    config_file.py set      KEY=value ...  save settings
    config_file.py retarget_audio OLD NEW  move the announcements' folders
    config_file.py template                print the documented template
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    _refresh_paths()
    command = argv[1] if len(argv) > 1 else ""

    if command == "set":
        pairs = argv[2:]
        if not pairs or any("=" not in p for p in pairs):
            print("Usage: config_file.py set KEY=value [KEY=value ...]", file=sys.stderr)
            return 2
        updates = dict(p.split("=", 1) for p in pairs)
        try:
            changed = write_values(updates)
        except ValueError as e:
            print("ERROR: %s" % e, file=sys.stderr)
            return 1
        print(" ".join(changed) if changed else "(no change)")
        return 0

    yaml_path = argv[2] if len(argv) > 2 else None
    env_path = argv[3] if len(argv) > 3 else None

    if command == "sync":
        print(write_env_file(yaml_path, env_path))
        return 0
    if command == "ensure":
        action, count = ensure_file(yaml_path, env_path)
        print("%s %s" % (action, count))
        return 0
    if command == "show":
        values = dict(DEFAULTS)
        values.update(read_values(yaml_path, env_path))
        for setting in SETTINGS:
            print("%-32s %s" % (setting.env, values[setting.env]))
        return 0
    if command == "retarget_audio":
        if len(argv) < 4:
            print("Usage: config_file.py retarget_audio OLD_ROOT NEW_ROOT", file=sys.stderr)
            return 2
        print("moved %d" % retarget_announcements(argv[2], argv[3]))
        return 0
    if command == "template":
        print(render_template(), end="")
        return 0
    print(_main.__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
