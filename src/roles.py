"""Who may reach what: four roles, from a visitor to the owner.

The ladder is deliberately small and each step is a *place*, not a privilege:

    guest     someone on the access point who did not log in: the player, the
              queue, the suggestions - what `guest_mode` allows, and no more.
    member    someone who lives here: their music, their likes, their lists,
              the household messages - never a setting, never the machine.
    operator  someone who keeps the radio: every setting and everything it
              stores (announcements, lists, schedules, cards, the devices on
              the network) - but not the machine itself.
    owner     the machine and its door: power, updates, network, backups,
              the accounts, the names the interface answers under.

A path that is listed nowhere needs the owner: a route added tomorrow is
closed until someone writes down who may call it. The order inside a list is
the file order of `src/web_server.py`, so it reads like the pages it serves.
"""

# The four levels, weakest first: a role may call what its own level and every
# level below it lists.
ROLES = ("guest", "member", "operator", "owner")
RANK = {role: index for index, role in enumerate(ROLES)}


# A guest's surface: what the interface offers without anyone logging in, when
# guest access is on. web/app.js holds the same list (a test compares them).
GUEST_PATHS = frozenset({
    "/api/dedications/voice",
    "/api/suggestions/name_voice",
    "/api/status",
    "/api/status/wait",
    "/api/vote/skip",
    "/api/game",
    "/api/game/answer",
    "/api/game/join",
    "/api/volume",
    "/api/action/single_click",
    "/api/action/double_click",
    "/api/action/start_music",
    "/api/action/next_track",
    "/api/action/previous_track",
    "/api/action/toggle_pause",
    "/api/action/skip_sound",
    "/api/action/announce",
    "/api/announcements",
    "/api/portal/status",
    "/api/portal/release",
    "/api/now/cover",
    "/api/now/lyrics",
    "/api/recent",
    "/api/queue",
    "/api/today",
    "/api/library",
    "/api/library/facets",
    "/api/library/queue",
    "/api/library/play",
    "/api/audio/fallback",
    "/api/device",
    "/api/suggestions",
    "/api/suggestions/vote",
    "/api/suggestions/delete",
    "/api/suggestions/name",
    "/api/devices/link_code",
    "/api/devices/link_join",
    "/api/devices/link_leave",
})


# One who lives here: the player's own switches, and what the radio remembers
# about their music.
MEMBER_PATHS = frozenset({
    "/api/likes",
    "/api/likes/toggle",
    "/api/excluded",
    "/api/excluded/filter",
    "/api/excluded/restore",
    "/api/library/hide",
    "/api/library/duplicates",
    "/api/library/duplicates/ignore",
    "/api/lists",
    "/api/lists/<list_id>",
    "/api/lists/<list_id>/m3u",
    "/api/lists/<list_id>/tracks",
    "/api/lists/active",
    "/api/lists/from_genres",
    "/api/lists/import",
    "/api/stations",
    "/api/stations/<station_id>",
    "/api/stations/<station_id>/play",
    "/api/loop",
    "/api/mute",
    "/api/memories",
    "/api/reminders",
    "/api/reminders/<rid>",
})


# One who keeps the radio: everything it plays, stores and shows - the content
# cards, the settings, and the people on the network.
OPERATOR_PATHS = frozenset({
    "/api/action/sleep_timer",
    "/api/action/test_click",
    "/api/action/timed_pause",
    "/api/announce_files/<path:source_id>",
    "/api/announcement_volumes",
    "/api/announcement_volumes/<path:key>",
    "/api/announcements/<item_id>",
    "/api/announcements/<item_id>/play",
    "/api/audio/outputs",
    "/api/audio/test",
    "/api/bluetooth/connect",
    "/api/bluetooth/controllers",
    "/api/bluetooth/disconnect",
    "/api/bluetooth/forget",
    "/api/bluetooth/pair",
    "/api/bluetooth/remote",
    "/api/bluetooth/remotes",
    "/api/bluetooth/scan/start",
    "/api/bluetooth/scan/status",
    "/api/bluetooth/scan/stop",
    "/api/browse",
    "/api/cards",
    "/api/cards/<card_id>",
    "/api/cards/<card_id>/play",
    "/api/clock/test_bt",
    "/api/config/export",
    "/api/config/import",
    "/api/dedications/delete",
    "/api/dedications/listen",
    "/api/devices/ban",
    "/api/devices/banned",
    "/api/devices/forget",
    "/api/devices/free_credits",
    "/api/devices/link",
    "/api/devices/name",
    "/api/devices/name_locked",
    "/api/devices/name_voice",
    "/api/devices/portal",
    "/api/devices/portal_release",
    "/api/devices/seen",
    "/api/devices/unlink",
    "/api/dj_announcements",
    "/api/flic/buttons/delete",
    "/api/flic/enable",
    "/api/flic/pair/cancel",
    "/api/flic/pair/start",
    "/api/flic/pair/status",
    "/api/flic/status",
    "/api/game/asked",
    "/api/game/cut",
    "/api/game/go",
    "/api/game/hall",
    "/api/game/mark",
    "/api/game/next",
    "/api/game/start",
    "/api/game/stop",
    "/api/music/manifest",
    "/api/music/upload",
    "/api/mqtt/status",
    "/api/prepared/<kind>",
    "/api/prepared/<kind>/file",
    "/api/prepared/<kind>/folder",
    "/api/rescan_music",
    "/api/schedule_order",
    "/api/schedules",
    "/api/schedules/<schedule_id>",
    "/api/schedules/<schedule_id>/voice",
    "/api/party",
    "/api/settings",
    "/api/snapcast/client",
    "/api/snapcast/clients",
    "/api/speech/test",
    "/api/stream",
    "/api/suggestions/names",
    "/api/suggestions/release_name",
    "/api/suggestions/status",
    "/api/system_sounds",
    "/api/system_sounds/<key>",
    "/api/system_sounds/<key>/off",
    "/api/system_sounds/<key>/test",
    "/api/tags",
    "/api/tags/identify",
    "/api/tags/picture",
    "/api/track_order/<path:source_id>",
    "/api/volume_curve",
    "/api/wled",
    "/api/wled/discover",
    "/api/wled/refresh",
    "/api/wled/test",
})


def needed_role(path, rule=None):
    """The role a request needs: the lowest level that lists it, the owner
    otherwise."""
    for role, paths in (("guest", GUEST_PATHS), ("member", MEMBER_PATHS),
                        ("operator", OPERATOR_PATHS)):
        if path in paths or (rule and rule in paths):
            return role
    return "owner"


def allows(role, needed):
    """Whether a role may do what another one needs."""
    return RANK.get(role, -1) >= RANK.get(needed, len(ROLES))


# A guest page and what it asks for: hiding the page refuses those too.
GUEST_PAGE_PATHS = {
    "upnext": {"/api/queue"},
    "recent": {"/api/recent"},
    "today": {"/api/today"},
    "library": {"/api/library", "/api/library/facets"},
    "game": {"/api/game", "/api/game/answer", "/api/game/join"},
    "suggest": {"/api/suggestions", "/api/suggestions/vote", "/api/suggestions/delete",
                "/api/suggestions/name"},
}


def allowed_paths(role, guest_access=True, pages_off=()):
    """Every path a role may call, for the interface to know before it asks.

    The page holds no list of its own: it is told this one at boot, so a path
    added here is one the page may use without being told twice.
    """
    rank = RANK.get(role, -1)
    hidden = set()
    for page in pages_off or ():
        hidden |= GUEST_PAGE_PATHS.get(page, set())
    paths = set()
    if rank >= RANK["member"]:
        paths |= GUEST_PATHS | MEMBER_PATHS
    elif rank >= RANK["guest"] and guest_access:
        paths |= {path for path in GUEST_PATHS if path not in hidden}
    if rank >= RANK["operator"]:
        paths |= OPERATOR_PATHS
    return sorted(paths)
