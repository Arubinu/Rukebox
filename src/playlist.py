"""Play orders: random, random by album, or ordered."""

import os
import random
import re

_NUMERIC_RUN = re.compile(r"(\d+)")


def natural_sort_key(path):
    """Splits a path into text/number chunks so "track2" sorts before
    "track10"."""
    name = os.path.basename(path).lower()
    return [int(chunk) if chunk.isdigit() else chunk for chunk in _NUMERIC_RUN.split(name)]


def group_key(path, root):
    """The immediate subfolder of `path` under `root`."""
    try:
        rel = os.path.relpath(path, root)
    except ValueError:
        return os.path.basename(path)
    parts = rel.replace("\\", "/").split("/")
    return parts[0] if len(parts) > 1 else os.path.basename(path)


def build_random(files):
    order = list(files)
    random.shuffle(order)
    return order


def build_random_albums(files, root):
    """Shuffles each group's contents, then interleaves the groups so that two
    files from the same group are never adjacent whenever that is
    arithmetically possible."""
    groups = {}
    for f in files:
        groups.setdefault(group_key(f, root), []).append(f)
    for name in groups:
        random.shuffle(groups[name])

    names = list(groups.keys())
    random.shuffle(names)

    order = []
    previous = None
    while True:
        available = [n for n in names if groups[n] and n != previous]
        if not available:
            break
        pick = max(available, key=lambda n: len(groups[n]))
        order.append(groups[pick].pop())
        previous = pick

    for name in names:
        order.extend(groups[name])
    return order


def build_ordered(files, custom_order=None):
    """Natural filename sort, or the given explicit order when one is supplied."""
    if not custom_order:
        return sorted(files, key=natural_sort_key)

    by_basename = {}
    for f in files:
        by_basename.setdefault(os.path.basename(f), []).append(f)

    order = []
    for name in custom_order:
        matches = by_basename.pop(name, None)
        if matches:
            order.extend(matches)

    leftovers = sorted(
        (f for remaining in by_basename.values() for f in remaining),
        key=natural_sort_key,
    )
    order.extend(leftovers)
    return order


def order_files(files, root, mode, custom_order=None):
    """Single entry point: dispatches to the right algorithm."""
    if not files:
        return []
    if mode == "random":
        return build_random(files)
    if mode == "random_albums":
        return build_random_albums(files, root)
    if mode == "ordered":
        return build_ordered(files, custom_order)
    return build_ordered(files, custom_order)
