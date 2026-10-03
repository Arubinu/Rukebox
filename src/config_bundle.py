"""Exports and imports the whole configuration as one portable file."""

import json
import logging
import sys
import time

import announcements
import config_file
import config_schema
import hidden_tracks
import likes
import music_lists
import schedules
import track_order

log = logging.getLogger("config_bundle")

BUNDLE_FORMAT = "rukebox-config"
BUNDLE_VERSION = 1

EXCLUDED_SETTINGS = ("WEB_PASSWORD_HASH", "WEB_SESSION_SECRET")


def export_bundle(cfg, version=None):
    """Everything a fresh installation needs, as a plain dict."""
    settings = {
        key: value for key, value in config_file.read_values().items()
        if key not in EXCLUDED_SETTINGS
    }
    return {
        "format": BUNDLE_FORMAT,
        "version": BUNDLE_VERSION,
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "app_version": (version or {}).get("tree_hash_short", ""),
        "settings": settings,
        "announcements": announcements.load(cfg.get("ANNOUNCEMENTS_FILE", "")),
        "track_order": track_order.load(cfg.get("TRACK_ORDER_FILE", "")),
        "schedules": schedules.load(cfg.get("SCHEDULES_FILE", "")),
        "music_lists": music_lists.load(cfg.get("MUSIC_LISTS_FILE", "")),
        "likes": likes.load(cfg.get("LIKES_FILE", "")),
        "hidden": hidden_tracks.load(cfg.get("HIDDEN_FILE", "")),
    }


def _clean_announcements(items):
    """[(clean_item, None) | (None, reason)]."""
    cleaned = []
    for item in (items if isinstance(items, list) else []):
        if not isinstance(item, dict):
            cleaned.append((None, "not an object"))
            continue
        try:
            clean = announcements.validate(item)
        except ValueError as e:
            cleaned.append((None, str(e)))
            continue
        item_id = str(item.get("id", "")).strip()
        clean["id"] = item_id or announcements._slugify(clean["name"])
        try:
            clean["created_at"] = float(item.get("created_at", 0)) or time.time()
        except (TypeError, ValueError):
            clean["created_at"] = time.time()
        cleaned.append((clean, None))
    return cleaned


def _clean_records(items, validate, slugify):
    """Validated copies keeping their ids and dates; the bad ones are dropped."""
    kept = []
    for item in (items if isinstance(items, list) else []):
        if not isinstance(item, dict):
            continue
        try:
            clean = validate(item)
        except ValueError:
            continue
        clean["id"] = str(item.get("id", "")).strip() or slugify(clean["name"])
        try:
            clean["created_at"] = float(item.get("created_at", 0)) or time.time()
        except (TypeError, ValueError):
            clean["created_at"] = time.time()
        kept.append(clean)
    return _dedupe_ids(kept)


def _clean_tracks(items, fields, date_field):
    """Liked or set-aside tracks: a key each, one entry per key."""
    kept, seen = [], set()
    for item in (items if isinstance(items, list) else []):
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        clean = {"key": key}
        for field in fields:
            clean[field] = str(item.get(field) or "").strip()
        try:
            clean[date_field] = float(item.get(date_field) or 0) or time.time()
        except (TypeError, ValueError):
            clean[date_field] = time.time()
        kept.append(clean)
    kept.sort(key=lambda item: item[date_field])
    return kept


def _dedupe_ids(items):
    """Two records sharing an id would make one of them unreachable."""
    used = set()
    for item in items:
        base = item["id"] or "item"
        candidate = base
        suffix = 2
        while candidate in used:
            candidate = "%s-%d" % (base, suffix)
            suffix += 1
        item["id"] = candidate
        used.add(candidate)
    return items


def _clean_orders(orders):
    if not isinstance(orders, dict):
        return {}
    return {
        key: value for key, value in orders.items()
        if isinstance(key, str) and isinstance(value, list)
        and all(isinstance(name, str) for name in value)
    }


def import_bundle(data, cfg):
    """Applies a bundle and returns a summary; refusals raise ValueError(code)."""
    if not isinstance(data, dict) or data.get("format") != BUNDLE_FORMAT:
        raise ValueError("not_a_bundle")
    try:
        version = int(data.get("version", 0))
    except (TypeError, ValueError):
        version = 0
    if version < 1:
        raise ValueError("not_a_bundle")
    if version > BUNDLE_VERSION:
        raise ValueError("newer_bundle")

    raw_settings = data.get("settings")
    if not isinstance(raw_settings, dict):
        raise ValueError("no_settings")

    known = {setting.env for setting in config_schema.SETTINGS}
    updates = {}
    unknown = []
    for key, value in raw_settings.items():
        if key in EXCLUDED_SETTINGS:
            continue
        if key not in known:
            unknown.append(key)
            continue
        updates[key] = "" if value is None else str(value)

    before = config_file.read_values()
    changed = sum(1 for key, value in updates.items() if before.get(key) != value)

    if updates:
        config_file.write_values(updates)

    cleaned = _clean_announcements(data.get("announcements"))
    good = _dedupe_ids([item for item, _ in cleaned if item])
    rejected = [reason for item, reason in cleaned if item is None]
    applied_announcements = 0
    if isinstance(data.get("announcements"), list):
        announcements.save_all(cfg["ANNOUNCEMENTS_FILE"], good)
        applied_announcements = len(good)

    orders = _clean_orders(data.get("track_order"))
    if isinstance(data.get("track_order"), dict):
        track_order.save_all(cfg["TRACK_ORDER_FILE"], orders)

    summary = {
        "settings": len(updates),
        "settings_changed": changed,
        "announcements": applied_announcements,
        "announcements_rejected": rejected,
        "track_order": len(orders),
        "unknown_settings": unknown,
    }

    # Absent from a file exported before they existed: left as they are.
    if isinstance(data.get("schedules"), list):
        items = _clean_records(data["schedules"], schedules.validate, schedules._slugify)
        schedules.save_all(cfg["SCHEDULES_FILE"], items)
        summary["schedules"] = len(items)
    if isinstance(data.get("music_lists"), list):
        items = _clean_records(data["music_lists"], music_lists.validate, music_lists._slugify)
        music_lists.save_all(cfg["MUSIC_LISTS_FILE"], items)
        summary["music_lists"] = len(items)
    if isinstance(data.get("likes"), list):
        items = _clean_tracks(data["likes"], ("title", "artist"), "liked_at")
        likes.save_all(cfg["LIKES_FILE"], items)
        summary["likes"] = len(items)
    if isinstance(data.get("hidden"), list):
        items = _clean_tracks(data["hidden"], ("path", "title", "artist"), "hidden_at")
        hidden_tracks.save_all(cfg["HIDDEN_FILE"], items)
        summary["hidden"] = len(items)
    return summary


def load_bundle_file(path):
    """Reads a bundle from disk, for the firstrun path."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        log.exception("Could not read the configuration bundle %s", path)
        return None


REFUSAL_MESSAGES = {
    "not_a_bundle": "this is not a Rukebox configuration file",
    "newer_bundle": "this file comes from a newer version of Rukebox",
    "no_settings": "there are no settings in this file",
}


def _refusal_message(code):
    return REFUSAL_MESSAGES.get(code, code)


def _main(argv):
    """`config_bundle.py export <file>` / `import <file>`."""
    if len(argv) < 2 or argv[0] not in ("export", "import"):
        print("Usage: config_bundle.py export|import <file>", file=sys.stderr)
        return 2

    import config_and_scan

    action, path = argv[0], argv[1]
    cfg = config_and_scan.load_config()

    if action == "export":
        import version as version_module
        bundle = export_bundle(cfg, version_module.read_version_file(cfg["UPDATE_VERSION_FILE"]))
        with open(path, "w", encoding="utf-8") as f:
            json.dump(bundle, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print("Exported %d settings, %d announcements, %d track orders to %s" % (
            len(bundle["settings"]), len(bundle["announcements"]),
            len(bundle["track_order"]), path))
        return 0

    data = load_bundle_file(path)
    if data is None:
        print("Could not read %s" % path, file=sys.stderr)
        return 1
    try:
        summary = import_bundle(data, cfg)
    except ValueError as e:
        print("Refused: %s" % _refusal_message(str(e)), file=sys.stderr)
        return 1
    print("Imported %d settings, %d announcements, %d track orders" % (
        summary["settings"], summary["announcements"], summary["track_order"]))
    if summary["announcements_rejected"]:
        print("Skipped %d announcement(s): %s" % (
            len(summary["announcements_rejected"]),
            "; ".join(summary["announcements_rejected"])), file=sys.stderr)
    if summary["unknown_settings"]:
        print("Ignored unknown settings: %s" % ", ".join(sorted(summary["unknown_settings"])),
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
