"""Every setting, with its type, default and documentation."""


class Setting:
    __slots__ = ("env", "section", "key", "type", "default", "comment", "label")

    def __init__(self, env, section, key, type_, default, comment="", label=None):
        self.env = env
        self.section = section
        self.key = key
        self.type = type_
        self.default = default
        self.comment = comment
        self.label = label or env

    @property
    def path(self):
        return "%s.%s" % (self.section, self.key)

    def __repr__(self):
        return "<Setting %s (%s)>" % (self.env, self.path)

SECTIONS = [
    ("audio", "Audio output: Bluetooth speaker, headphone jack, USB or HDMI"),
    ("bluetooth", "Bluetooth: speaker, and which controller does what"),
    ("folders", "Audio folders (subfolders are scanned recursively)"),
    ("playback", "Playback"),
    ("lights", "WLED light strips: scenes that follow the radio, its beat and the clock"),
    ("buttons", "Flic and/or GPIO button: what single and double click do"),
    ("schedule", "Evening cutoff (the morning announcement is a custom one)"),
    ("fades", "Fade durations - every stop is progressive, never abrupt"),
    ("playback_errors", "What to do when a file cannot be read"),
    ("clock", "Clock: this Pi has no NTP, see docs/guide.md"),
    ("network", "Web interface and Wi-Fi"),
    ("security", "Web interface password (optional) and its GPIO reset"),
    ("suggestions", "Suggestion box: music and announcement ideas, votes, names"),
    ("hardware", "The Pi itself"),
    ("statistics", "Usage statistics"),
    ("updates", "Updates"),
    ("paths", "Internal paths - only change if you know what you are doing"),
]

SETTINGS = [
    Setting(
        "AUDIO_OUTPUT", "audio", "output", "str", "bluetooth",
        "Where the sound goes:\n"
        "  bluetooth -> the Bluetooth speaker below (the default)\n"
        "  jack      -> the 3.5 mm headphone jack (Raspberry Pi 3/4, not\n"
        "               the Zero)\n"
        "  usb       -> a USB sound card, whichever is plugged in\n"
        "  hdmi      -> the HDMI screen or receiver\n"
        "  docker    -> a virtual output, heard over the network: this is the\n"
        "               container's own output, where there is no sound card\n"
        "               at all, and it only makes a sound once the network\n"
        "               audio stream is on and something is listening to it\n"
        "A wired output is found again whenever it comes and goes; while it\n"
        "is missing, the sound goes to the default output instead. With a\n"
        "wired output, losing the Bluetooth speaker no longer pauses the\n"
        "music nor powers the Pi off.",
        "Audio output",
    ),
    Setting(
        "AUDIO_FALLBACK_OUTPUT", "audio", "fallback_output", "str", "",
        "Where the sound goes while the Bluetooth speaker is away: empty for\n"
        "nowhere (the music pauses, see pause_on_speaker_loss), or jack / usb /\n"
        "hdmi to keep playing there. The speaker takes the sound back when it\n"
        "returns. Only used when output is bluetooth.",
        "Fallback output",
    ),
    Setting(
        "STREAM_ENABLED", "audio", "stream_enabled", "bool", "false",
        "Serve what the radio plays over HTTP, so any device on the network can\n"
        "listen to it - the \"Listen here\" button on the player, VLC, or a\n"
        "network speaker. This is the only way to hear a Rukebox in a container,\n"
        "which has no sound card of its own; see the Docker section of\n"
        "docs/guide.md. Off by default: it costs an ffmpeg process, which a Pi\n"
        "Zero would rather not run. The library has to be playable by whoever\n"
        "can reach this page, so think about the password first.",
        "Network audio stream",
    ),
    Setting(
        "STREAM_ENCODER", "audio", "stream_encoder", "str", "",
        "The codec the stream is encoded with: opus (default), mp3, aac or\n"
        "vorbis. Empty picks the best one this ffmpeg knows, which is what the\n"
        "image installs. Opus is what every browser plays and what the little\n"
        "bandwidth of a Pi Zero carries comfortably.",
        "Stream codec",
    ),
    Setting(
        "STREAM_SOURCE", "audio", "stream_source", "str", "",
        "The PipeWire monitor to encode, as `pactl list short sources` names it.\n"
        "Empty encodes the output the radio plays to - the wired output chosen in\n"
        "the card above, or the default one - which is what makes the stream carry\n"
        "what you hear. Only set this to follow something else entirely.",
        "Stream source",
    ),
    Setting(
        "STREAM_VOLUME", "audio", "stream_volume", "int", "100",
        "A volume of its own for the network stream, in percent: 100 follows the\n"
        "volume the radio is playing at, lower is quieter and higher is louder\n"
        "(0 to 200). It multiplies what the radio plays, so a radio that is turned\n"
        "down cannot be made loud again here.",
        "Stream volume (%)",
    ),
    Setting(
        "UPNP_NAME", "audio", "upnp_name", "str", "",
        "What this radio is called by a player that finds it on the network\n"
        "(VLC's \"Local network\", a phone, a network speaker): the whole name,\n"
        "number included, and the number is drawn once when the radio first\n"
        "announces itself (\"Rukebox 4821\") so that two radios on one network are\n"
        "told apart. Rename it as you like - the radio stays the same device to a\n"
        "player that already knows it (see UPNP_UID) - and it is also the title\n"
        "the stream itself carries.",
        "Name on the network",
    ),
    Setting(
        "UPNP_UID", "audio", "upnp_uid", "str", "",
        "Drawn once, never shown: the name a player remembers this radio by, so\n"
        "that renaming it relabels the device instead of making a second one, and\n"
        "two radios that were given the same name are still two devices.",
        "Identity",
    ),
    Setting(
        "SPEAKER_MAC", "bluetooth", "speaker_mac", "str", "",
        "MAC address of the \"master\" speaker (the other speaker of a stereo\n"
        "pair is handled by the speakers themselves, not our concern here).\n"
        "Fill this in, or pair a speaker from the web interface, which writes\n"
        "it here for you. Example: AA:BB:CC:DD:EE:FF",
        "Speaker MAC address",
    ),
    Setting(
        "SPEAKER_BT_ADAPTER", "bluetooth", "adapter", "str", "",
        "If the Pi has MULTIPLE Bluetooth interfaces (e.g. internal + USB\n"
        "dongle), MAC address of the CONTROLLER (not the speaker) to use for\n"
        "the speaker, the Bluetooth clock fallback and scanning - get it with\n"
        "`bluetoothctl list`. Empty = default controller.",
        "Bluetooth controller",
    ),
    Setting(
        "FLIC_HCI_DEVICE", "bluetooth", "flic_hci_device", "str", "hci0",
        "Bluetooth controller dedicated to flicd (Flic button): its address\n"
        "(stable across reboots) or an hciN name. It must not be the speaker's\n"
        "controller - flicd takes it for itself. Set from Bluetooth > Controllers.",
        "Flic controller",
    ),
    Setting(
        "SPEAKER_LOSS_PAUSE", "bluetooth", "pause_on_speaker_loss", "bool", "true",
        "Pause the music when the speaker disconnects, and resume it when it\n"
        "comes back - rather than playing on into the void. Seen by the speaker\n"
        "check (speaker_watch_sec), so within that many seconds.",
        "Pause when the speaker disconnects",
    ),
    Setting(
        "SPEAKER_QUICK_STEP", "bluetooth", "quick_volume_step", "int", "20",
        "Right after the music starts (or the speaker comes back), a press on\n"
        "the speaker's volume buttons moves the volume by this many points -\n"
        "the music is often far too loud then. Back to the speaker's own small\n"
        "steps 10 seconds after the last press. 0 = always the speaker's steps.",
        "Big volume steps at the start (%)",
    ),
    Setting(
        "SPEAKER_BATTERY_LOW", "bluetooth", "battery_low_percent", "int", "15",
        "Warn once (a sound and a line on the player) when the speaker says its\n"
        "battery is at or below this percentage. 0 = no warning. Only speakers\n"
        "that report their battery to BlueZ can be followed.",
        "Low battery warning (%)",
    ),
    Setting(
        "SPEAKER_RESUME_FADE_SEC", "bluetooth", "resume_fade_sec", "float", "3",
        "Fade-in when the music resumes after the speaker came back.\n"
        "0 = resume at full volume straight away.",
        "Fade-in on resume (sec)",
    ),
    Setting(
        "BT_AUDIO_CODECS", "bluetooth", "bt_audio_codecs", "str", "sbc_xq,sbc",
        "The Bluetooth codecs (A2DP) the Pi OFFERS the speaker; the speaker\n"
        "picks among them. Known names: ldac, aptx_hd, aptx, aac, sbc_xq, sbc,\n"
        "faststream, opus. Write only what the speaker can do: offering one it\n"
        "cannot handle is not harmless - measured here, with SBC-XQ offered\n"
        "alone, a soundcore Select 4 Go answered on the headset profile\n"
        "instead (mSBC, telephone quality) rather than falling back to SBC.\n"
        "The Pi's own codecs, and what the link ended up using, are in\n"
        "System health > Audio diagnostic.",
        "Bluetooth codecs offered",
    ),
    Setting(
        "SPEAKER_LOSS_SHUTDOWN_MIN", "bluetooth", "shutdown_after_speaker_loss_min", "int", "0",
        "Power the Pi off when the speaker has been disconnected for this many\n"
        "minutes. 0 = never.",
        "Power off after the speaker is lost (min)",
    ),
    Setting(
        "SPEAKER_ABSENT_SHUTDOWN_MIN", "bluetooth", "shutdown_if_no_speaker_min", "int", "0",
        "Power the Pi off when no speaker has connected this many minutes\n"
        "after the radio started. 0 = never.",
        "Power off if no speaker since startup (min)",
    ),

    Setting("MUSIC_DIR", "folders", "music", "str", "/home/pi/audio/music",
            "", "Music folder"),
    Setting(
        "DJ_ANNOUNCE_DIR", "folders", "dj_announcements", "str",
        "/home/pi/audio/dj_announcements",
        "Introductions to play instead of the spoken one (see dj_announce_mode),\n"
        "mirroring the music folder: a file named after the song, or one named\n"
        "\"_any\" beside it - which then covers its album, its artist, or the\n"
        "whole library. Any format mpv reads; the name is what counts.",
        "Prepared introductions",
    ),
    Setting("MEME_DIR", "folders", "memes", "str", "/home/pi/audio/memes",
            "Sounds played by a single click on the Flic button.", "Button sounds"),
    Setting(
        "COVER_DIR", "folders", "covers", "str", "/home/pi/audio/covers",
        "Cover art you prepared, mirroring the music folder the way the\n"
        "prepared introductions do: a picture named after the song, or one\n"
        "named \"_any\" beside it - which then covers its album, its artist,\n"
        "or the whole library. The names the readers usually look for\n"
        "(cover, folder, front, albumart, thumb, poster...), and the\n"
        "folder's own name, work there too. This folder is the \"files\"\n"
        "side of cover_priority; anything found in the music folders\n"
        "themselves counts as the \"id3\" side. Empty disables it: the\n"
        "pictures beside the music are then the only ones looked at.",
        "Prepared covers",
    ),
    Setting("CUTOFF_ANNOUNCE_DIR", "folders", "cutoff_announcements", "str",
            "/home/pi/audio/cutoff_announcements", "", "Cutoff announcements"),
    Setting(
        "KEEPALIVE_SOUND", "folders", "keepalive_sound", "str",
        "/home/pi/audio/system/keepalive.wav",
        "Looped very quietly when autostart is off, while waiting for the first\n"
        "click - stops the Bluetooth speaker powering itself off on silence.",
        "Keep-alive sound",
    ),
    Setting("CLOCK_OK_SOUND", "folders", "clock_ok_sound", "str",
            "/home/pi/audio/system/clock_ok.wav",
            "Played once the clock status is known (only when there is no RTC).",
            "Clock OK sound"),
    Setting("CLOCK_FALLBACK_SOUND", "folders", "clock_fallback_sound", "str",
            "/home/pi/audio/system/clock_fallback.wav", "", "Clock fallback sound"),
    Setting(
        "RESTART_SOUND", "folders", "restart_sound", "str",
        "/home/pi/audio/system/restart.wav",
        "Played before a restart planned for the end of the song (System\n"
        "card). Leave empty to restart without a sound.",
        "Restart sound",
    ),
    Setting(
        "AP_CONNECT_SOUND", "folders", "ap_connect_sound", "str",
        "/home/pi/audio/system/ap_connect.wav",
        "Played once when a new device joins the admin Wi-Fi access point.\n"
        "Leave empty to disable. Uses the same disposable-mpv mechanism as the\n"
        "clock sounds above, so it never interrupts music (see _play_cue_sound()).",
        "Wi-Fi connection sound",
    ),
    Setting(
        "BATTERY_LOW_SOUND", "folders", "battery_low_sound", "str",
        "/home/pi/audio/system/battery_low.wav",
        "Played once when the speaker's battery falls to battery_low_percent.\n"
        "Leave empty to warn on the page only.",
        "Low battery sound",
    ),

    Setting(
        "MUSIC_ORDER_MODE", "playback", "order_mode", "str", "random",
        "How the music list is played:\n"
        "  random         -> fully shuffled (the original SHUFFLE=true)\n"
        "  random_albums  -> shuffled too, but never two tracks from the\n"
        "                    same artist back to back (grouped by each\n"
        "                    track's immediate subfolder under music: -\n"
        "                    organise as music/Artist/Album/track.mp3 for\n"
        "                    this to group sensibly)\n"
        "  ordered        -> natural filename order (\"2\" before \"10\"),\n"
        "                    i.e. the original SHUFFLE=false - name files\n"
        "                    \"01 - ...\", \"02 - ...\" to control it",
        "Music order",
    ),
    Setting(
        "MUSIC_LOOP", "playback", "loop", "bool", "true",
        "true  = the list loops forever, like before\n"
        "false = stop after playing every track once. The Pi and the\n"
        "        speaker stay fully on while stopped (so a scheduled\n"
        "        announcement can still play) - only the music itself\n"
        "        stops. A single click (or \"Start music\" in the web\n"
        "        interface) restarts the list.",
        "Loop the music list",
    ),
    Setting(
        "COVER_PRIORITY", "playback", "cover_priority", "str", "files",
        "Which cover the page shows when both sides have one:\n"
        "  files -> cover_dir first (a picture named after the song or\n"
        "           \"_any\" beside it, or one of the usual names), then\n"
        "           what the music folders and the tags carry.\n"
        "  id3   -> what the music folders and the tags carry first: the\n"
        "           picture named after the track (or <track>.cover.jpg),\n"
        "           the one embedded in the file itself, then the folders'\n"
        "           own pictures (cover, folder, front, albumart,\n"
        "           albumartsmall, thumb, poster, default, jacket, artist,\n"
        "           or the folder's own name), from the album folder up to\n"
        "           the artist folder and the library.\n"
        "Either way a picture that cannot be read is skipped and the search\n"
        "goes on; when nothing is found anywhere, the page shows the grey\n"
        "music note. A wide backdrop, a banner, a logo or a disc picture is\n"
        "never taken for the cover.",
        "Which cover wins",
    ),
    Setting(
        "UPCOMING_TRACKS_COUNT", "playback", "upcoming_tracks", "int", "10",
        "How many of the next tracks the Home section lists (the \"Up next\"\n"
        "card, guests included). 0 hides it.",
        "Tracks coming up",
    ),
    Setting(
        "RECENT_TRACKS_COUNT", "playback", "recent_tracks", "int", "20",
        "How many of the last tracks played the Home section lists (the\n"
        "\"Recently played\" card, guests included). 0 hides it.",
        "Tracks in the history",
    ),
    Setting(
        "MUSIC_KEEP_PROGRESS", "playback", "keep_progress", "bool", "true",
        "true  = remember where the list was and continue there after a\n"
        "        restart (this Pi shuts down and restarts daily - this\n"
        "        is what makes tomorrow continue instead of restarting\n"
        "        the whole list)\n"
        "false = start over at every restart. For random/random_albums\n"
        "        this means a fresh shuffle every time.",
        "Keep progress across restarts",
    ),
    Setting(
        "MUSIC_RESUME_MODE", "playback", "resume_mode", "str", "next_track",
        "Only relevant when keep_progress is true: which track plays\n"
        "first after a restart, if the Pi was shut down mid-track.\n"
        "  next_track    -> move on, as if that track had already finished\n"
        "  same_track    -> play that same track again from the start\n"
        "  same_position -> play it again from where it stopped (a few\n"
        "                   seconds earlier). Known when the radio stops it\n"
        "                   itself - cutoff, standby, power off, a restart -\n"
        "                   not after a power cut.",
        "On restart, resume with",
    ),
    Setting("MORNING_RISE_TRACKS", "playback", "rising_start_tracks", "int", "0",
            "At each music start, the first N songs of the queue play from the\n"
            "quietest to the loudest - the music wakes up with you. The loudness\n"
            "of each file is measured once, in the background (EBU R128).\n"
            "0 = off, 30 at most.",
            "Rising start (songs)"),
    Setting("ANNOUNCE_ORDER_MODE", "playback", "announce_order_mode", "str", "random_albums",
            "Applied to every announcement folder (morning, cutoff,\n"
            "double-click, and any custom announcement type) that holds\n"
            "more than one file. Independent from the music setting.\n"
            "  random         -> fully shuffled\n"
            "  random_albums  -> shuffled, but never two files from the\n"
            "                    same SUBFOLDER back to back. The grouping\n"
            "                    is by subfolder, exactly as for music -\n"
            "                    announcements have no albums, so read it\n"
            "                    as \"never two of the same kind in a row\"\n"
            "                    when a folder is split into subfolders.\n"
            "                    With no subfolders at all there is nothing\n"
            "                    to group, so it behaves as plain random -\n"
            "                    which is why it is the default here.\n"
            "  ordered        -> natural filename order (\"2\" before\n"
            "                    \"10\"). In this mode you can also pick a\n"
            "                    specific sequence yourself, per folder,\n"
            "                    from the Custom Announcements card in the\n"
            "                    web interface - natural filename order is\n"
            "                    only the starting point.",
            "Announcement order"),
    Setting("BASE_VOLUME", "playback", "base_volume", "float", "70",
            "Volume (0-100) reset at every daemon start, whatever the previous state.",
            "Base volume"),
    Setting(
        "AUDIO_COMPRESSION", "playback", "audio_compression", "str", "off",
        "Loudness filter mpv applies to everything it plays, before the\n"
        "volume control - see \"Getting more volume\" in docs/guide.md:\n"
        "  off    -> exactly what the files contain (the default)\n"
        "  soft   -> the quiet passages come up, the peaks are tamed, and\n"
        "            the result is held just below full scale. The volume\n"
        "            slider keeps working exactly as before.\n"
        "  strong -> the same, more so: for a very quiet recording or a\n"
        "            small speaker, at the cost of some of the dynamics\n"
        "            (loud tracks end up slightly quieter, quiet ones up\n"
        "            to 9 dB louder).\n"
        "Nothing is added to the files; an unknown value behaves as off.\n"
        "Applied without a restart.",
        "Volume boost (compression)",
    ),
    Setting(
        "AUDIO_EQUALIZER", "playback", "audio_equalizer", "str", "off",
        "A sound profile, applied before the volume boost:\n"
        "  off    -> the files as they are (the default)\n"
        "  bass   -> more low end, for a small speaker\n"
        "  voice  -> clearer voices, less rumble (talk, podcasts)\n"
        "  bright -> more treble\n"
        "  night  -> soft bass and tamed peaks, for listening quietly\n"
        "Applied without a restart.",
        "Sound profile",
    ),
    Setting("SKIP_TRAILING_SILENCE", "playback", "skip_trailing_silence", "bool", "true",
            "Move on as soon as a song's last seconds are only silence, rather\n"
            "than playing them out. The silence is found once per file, in the\n"
            "background, from its last 30 seconds.",
            "Skip the silence at the end"),
    Setting(
        "VOLUME_CHANGE", "playback", "volume_change", "str", "instant",
        "How a volume change from the web interface (or a button) is heard:\n"
        "  instant -> at once\n"
        "  fade    -> gliding to the new level over volume_fade_sec",
        "Volume change",
    ),
    Setting(
        "VOLUME_FADE_SEC", "playback", "volume_fade_sec", "float", "1.5",
        "With volume_change: fade, how long the glide lasts (seconds).",
        "Volume fade (sec)",
    ),
    Setting(
        "VOLUME_MODE", "playback", "volume_mode", "str", "session",
        "What happens to the volume set from the web interface's slider\n"
        "when the track changes (or when an announcement's fade ends).\n"
        "  session -> the volume you set is kept for as long as the\n"
        "             daemon runs; only a restart goes back to\n"
        "             base_volume above. This is what the slider implies:\n"
        "             you set it once and it stays where you put it.\n"
        "  base    -> the volume snaps back to base_volume on every track\n"
        "             change. Useful when base_volume is the level that\n"
        "             is right for the room and a change is only meant to\n"
        "             last for the track being listened to.\n"
        "An unknown value behaves as 'base'.",
        "Volume after a track change",
    ),
    Setting(
        "VOLUME_STEP", "playback", "volume_step", "int", "10",
        "How much a button set to \"Volume +\" or \"Volume -\" changes the\n"
        "volume (1-50).",
        "Volume step",
    ),
    Setting(
        "SPEAKER_VOLUME_LINK", "playback", "speaker_volume_link", "bool", "false",
        "One volume instead of two: the slider sets the speaker's own\n"
        "(hardware) volume and the radio sends it everything it has, so a\n"
        "press on the speaker's volume buttons moves the slider, and the two\n"
        "can never drift apart. Left off, the slider is the radio's software\n"
        "volume and the speaker's is a second one on top of it - which is\n"
        "what you want when the radio feeds something without volume keys.",
        "The speaker's volume is the volume",
    ),
    Setting(
        "SPEAKER_VOLUME_LOCK", "playback", "speaker_volume_lock", "bool", "false",
        "The speaker's volume buttons cannot change the volume: whatever they\n"
        "do is put back within two seconds. Linked (speaker_volume_link), the\n"
        "interface's volume is the one kept; otherwise the level the speaker\n"
        "had when it connected. A speaker that keeps its volume to itself\n"
        "(no absolute volume over Bluetooth) cannot be held.",
        "The speaker's volume buttons are locked",
    ),
    Setting(
        "PAUSE_DURATIONS", "playback", "pause_durations", "str", "5,15,30,60",
        "The durations (minutes, comma-separated) the web interface's\n"
        "\"Timer\" offers as a timed pause: the music pauses, then starts\n"
        "again by itself.",
        "Timed pause durations (min)",
    ),
    Setting(
        "SLEEP_DURATIONS", "playback", "sleep_durations", "str", "30,60,90,120",
        "The durations (minutes, comma-separated) the web interface's\n"
        "\"Timer\" offers as a sleep timer: the music pauses (with a fade)\n"
        "that many minutes later. The shortest is also the one a button set\n"
        "to \"Sleep timer\" uses.",
        "Sleep timer durations (min)",
    ),
    Setting(
        "MUSIC_START_MODE", "playback", "start_mode", "str", "boot",
        "When the music starts.\n"
        "  boot      -> as soon as the daemon is up, the way a radio\n"
        "               switched on at the wall behaves\n"
        "  action    -> the Pi initialises fully but stays silent until\n"
        "               the first click (button, or the web interface's\n"
        "               own Start button)\n"
        "  scheduled -> silent until start_hour:start_minute below, then\n"
        "               starts on its own. A click before that time still\n"
        "               starts it early, exactly as in 'action' mode -\n"
        "               the schedule is a floor, not a lock.\n"
        "  bluetooth -> silent until the Bluetooth speaker is connected\n"
        "               (the speaker is then the on switch), exactly as\n"
        "               silent at startup as 'action'. Checked every\n"
        "               speaker_watch_sec below, so it also fires when the\n"
        "               Pi boots with the speaker already connected. Once\n"
        "               a day, like 'scheduled': a later reconnection while\n"
        "               the music is already playing changes nothing, and\n"
        "               one after the list finished (loop=false) does NOT\n"
        "               restart it - a flaky speaker must not be able to\n"
        "               start music on its own in the middle of the night.\n"
        "               A click always starts the music early.",
        "Music start",
    ),
    Setting(
        "MUSIC_START_HOUR", "playback", "start_hour", "int", "7",
        "Only used when start_mode is 'scheduled'. Ignored otherwise.\n"
        "Needs a trustworthy clock, like every other scheduled event\n"
        "here - see the RTC note in docs/guide.md.",
        "Music start hour",
    ),
    Setting(
        "MUSIC_START_MINUTE", "playback", "start_minute", "int", "0",
        "Only used when start_mode is 'scheduled'.",
        "Music start minute",
    ),
    Setting("REPLAYGAIN_MODE", "playback", "replaygain_mode", "str", "track",
            "ReplayGain mode read by mpv from the tags already in the files:\n"
            "track | album | no", "ReplayGain mode"),
    Setting("REPLAYGAIN_TARGET_LUFS", "playback", "replaygain_target_lufs", "str", "-14",
            "Target used if you re-tag one day with scripts/tag_replaygain.sh.",
            "ReplayGain target (LUFS)"),

    Setting(
        "SINGLE_CLICK_ACTION", "buttons", "single_click_action", "str", "next",
        "What a single click on the button does once music is playing (also\n"
        "the equivalent button in the web interface):\n"
        "  next        -> the next track, after the sound below if any\n"
        "  previous    -> the previous track (the same one from the top\n"
        "                 after its first seconds), after the sound\n"
        "  sound       -> plays the sound below, then the same song goes on\n"
        "                 where it was\n"
        "  playpause | pause | play\n"
        "  loop_track  -> loops the song (again: back to normal)\n"
        "  loop_album  -> loops its album, i.e. its folder (again: normal)\n"
        "  loop_off    -> normal playback\n"
        "  volume_up | volume_down -> by volume_step\n"
        "  sleep       -> pauses the music in the shortest of\n"
        "                 sleep_durations (again: cancel)\n"
        "  off         -> nothing\n"
        "The sound below only goes with next, previous and sound. From idle\n"
        "(music not started yet), a single click always starts the music.",
        "Single click action",
    ),
    Setting(
        "SINGLE_CLICK_SOURCE", "buttons", "single_click_source", "str", "meme",
        "Sound played first (actions next, previous and sound): \"none\", or\n"
        "one sound of a list -\n"
        "meme (button sounds), cutoff, or \"custom:<id>\" for an announcement\n"
        "of the Custom Announcements card. One sound per click, taken in the\n"
        "announcement order (announce_order_mode, or the saved order): a\n"
        "fixed order plays 1, 2, 3... click after click, a shuffled one plays\n"
        "every sound once before any comes back.",
        "Single click sound",
    ),
    Setting(
        "DOUBLE_CLICK_ACTION", "buttons", "double_click_action", "str", "next",
        "What a double click does: same choices as the single click. Ignored\n"
        "while music has not started, except play and playpause, which start\n"
        "it.",
        "Double click action",
    ),
    Setting(
        "DOUBLE_CLICK_SOURCE", "buttons", "double_click_source", "str", "custom:doubleclick",
        "Sound played before the next track on a double click: same choices\n"
        "as single_click_source.",
        "Double click sound",
    ),
    Setting(
        "SPEAKER_PLAYPAUSE_ACTION", "buttons", "speaker_playpause_action", "str", "next",
        "The Bluetooth speaker's own buttons (src/speaker_buttons.py), as\n"
        "they reach the Pi over AVRCP - which ones a speaker sends depends on\n"
        "the speaker - and the media keys of a USB sound card, which are the\n"
        "buttons of the headphones plugged into it. Same choices as the\n"
        "clicks (next, previous, sound, playpause... see single_click_action),\n"
        "with a sound first (the _source below). Its play/pause button starts\n"
        "the music from idle, like a single click.",
        "Speaker play/pause action",
    ),
    Setting("SPEAKER_PLAYPAUSE_SOURCE", "buttons", "speaker_playpause_source", "str", "meme",
            "", "Speaker play/pause sound"),
    Setting("SPEAKER_NEXT_ACTION", "buttons", "speaker_next_action", "str", "next",
            "", "Speaker next action"),
    Setting("SPEAKER_NEXT_SOURCE", "buttons", "speaker_next_source", "str", "none",
            "", "Speaker next sound"),
    Setting("SPEAKER_PREVIOUS_ACTION", "buttons", "speaker_previous_action", "str", "off",
            "", "Speaker previous action"),
    Setting("SPEAKER_PREVIOUS_SOURCE", "buttons", "speaker_previous_source", "str", "none",
            "", "Speaker previous sound"),
    Setting(
        "SPEAKER_VOLUMEUP_ACTION", "buttons", "speaker_volumeup_action", "str", "volume_up",
        "A USB sound card often changes its own volume on that key as well, so\n"
        "both may move at once - set it to \"off\" to let the card alone.",
        "Speaker volume + action",
    ),
    Setting("SPEAKER_VOLUMEUP_SOURCE", "buttons", "speaker_volumeup_source", "str", "none",
            "", "Speaker volume + sound"),
    Setting(
        "SPEAKER_VOLUMEDOWN_ACTION", "buttons", "speaker_volumedown_action", "str", "volume_down",
        "", "Speaker volume - action",
    ),
    Setting("SPEAKER_VOLUMEDOWN_SOURCE", "buttons", "speaker_volumedown_source", "str", "none",
            "", "Speaker volume - sound"),
    Setting(
        "GPIO_BUTTON_PIN", "buttons", "gpio_button_pin", "int", "20",
        "BCM pin number for an optional physical button wired directly to a\n"
        "GPIO pin, in ADDITION to (or instead of) the Flic button - see\n"
        "systemd/rukebox-gpio-button.service, not enabled by default. Same\n"
        "single/double/long-press vocabulary as Flic, sent to the same\n"
        "control socket - use either input, or both at once. An arbitrary\n"
        "default distinct from GPIO_RESET_PIN (21), not a special pin.",
        "GPIO button pin",
    ),
    Setting(
        "GPIO_BUTTON_DEBOUNCE_SEC", "buttons", "gpio_button_debounce_sec", "float", "0.03",
        "Mechanical bounce shorter than this after a transition is ignored.",
        "GPIO button debounce (sec)",
    ),
    Setting(
        "GPIO_BUTTON_DOUBLE_CLICK_WINDOW_SEC", "buttons", "gpio_button_double_click_window_sec",
        "float", "0.4",
        "How long to wait, after the button is released once, to see whether\n"
        "a second press is a double click rather than a single one.",
        "GPIO button double-click window (sec)",
    ),
    Setting(
        "GPIO_BUTTON_LONG_PRESS_SEC", "buttons", "gpio_button_long_press_sec", "float", "1.5",
        "How long the button must be held down to count as a long press\n"
        "rather than a click.",
        "GPIO button long-press duration (sec)",
    ),

    Setting("CUTOFF_ENABLED", "schedule", "cutoff_enabled", "bool", "true",
            "Whether the day ends with a cutoff at all. Off, nothing stops the\n"
            "music by itself but a schedule's own stop.",
            "Daily cutoff"),
    Setting("CUTOFF_HOUR", "schedule", "cutoff_hour", "int", "7", "", "Cutoff hour"),
    Setting("CUTOFF_MINUTE", "schedule", "cutoff_minute", "int", "0", "", "Cutoff minute"),
    Setting(
        "CUTOFF_MODE", "schedule", "cutoff_mode", "str", "end_of_track",
        "exact        -> progressive fade right at the chosen time\n"
        "end_of_track -> wait for the natural end of the current track\n"
        "                (may run a few minutes past the chosen time)",
        "Cutoff mode",
    ),
    Setting("SHUTDOWN_AFTER_CUTOFF", "schedule", "shutdown_after_cutoff", "bool", "true",
            "What the cutoff ends with.\n"
            "true  -> the Pi powers itself off\n"
            "false -> standby: the Pi stays on and waits as it does at startup,\n"
            "         so the next start (a time, the speaker, a click) works",
            "Shut down after cutoff"),
    Setting("CUTOFF_WARNING_MIN", "schedule", "cutoff_warning_min", "int", "0",
            "Say \"the radio stops in N minutes\" that many minutes before the\n"
            "cutoff, while the music plays. 0 = say nothing.",
            "Spoken cutoff warning (min)"),
    Setting("SPEECH_LANGUAGE", "schedule", "speech_language", "str", "en",
            "Language of what the radio says out loud (the time, the date, the\n"
            "cutoff warning): en, fr, de, es, it or nl. Needs pico2wave, piper or\n"
            "espeak-ng (installed with the radio).",
            "Spoken language"),
    Setting("PIPER_VOICE", "schedule", "piper_voice", "str", "",
            "The natural-sounding voice to say things with, when its model is\n"
            "installed: a Piper voice name (scripts/install_piper.sh fetches the\n"
            "program and one). Empty uses pico2wave or espeak-ng instead.",
            "Spoken voice (Piper)"),

    Setting("DJ_ANNOUNCE_EVERY", "schedule", "dj_announce_every", "int", "0",
            "Like a radio host: every N songs, the next one is introduced out\n"
            "loud (\"Up next: title, by artist\") between the two. 0 = never.",
            "Introduce the songs (every N)"),
    Setting(
        "DJ_ANNOUNCE_MODE", "schedule", "dj_announce_mode", "str", "spoken",
        "Who says that introduction:\n"
        "  spoken      -> the radio says it, as before (dj_announce_dir ignored)\n"
        "  files       -> only the files of dj_announce_dir: a song with none is\n"
        "                 played without an introduction\n"
        "  files_first -> the file when there is one, the radio's voice otherwise",
        "Who introduces the songs",
    ),
    Setting("ANNOUNCE_MUSIC_UNDER", "fades", "music_under_announcements", "int", "0",
            "Keep the music playing under an announcement (the spoken time\n"
            "included), at this percentage of its volume. 0 = the music pauses\n"
            "for the announcement, as before.",
            "Music under announcements (%)"),
    Setting("FADE_DURATION_SEC", "fades", "announcement_sec", "float", "15",
            "Fade before a scheduled announcement.", "Announcement fade (sec)"),
    Setting("INTERACTIVE_FADE_DURATION_SEC", "fades", "interactive_sec", "float", "1.5",
            "Actions: next / previous track, a sound, an announcement - from a\n"
            "button or the web interface. Kept short so they feel responsive.\n"
            "Set to 0 for an instant cut instead of a fade (still goes through the\n"
            "same volume-ramp code, just with a zero-length ramp - no separate\n"
            "'hard cut' mode needed).",
            "Action fade (sec)"),
    Setting(
        "LONG_PRESS_ACTION", "buttons", "long_press_action", "str", "poweroff",
        "What a long press does: poweroff (fade out, then the Pi switches\n"
        "off) or standby (fade out, the Pi stays on, waiting like at\n"
        "startup - a click or the start mode brings the music back).",
        "Long press",
    ),
    Setting("LONGPRESS_FADE_DURATION_SEC", "fades", "long_press_sec", "float", "4",
            "Before stopping the music and switching the Pi off (long press, the\n"
            "interface's Stop & shutdown, the speaker gone for too long).",
            "Fade before switching off (sec)"),
    Setting("CROSSFADE_SEC", "fades", "crossfade_sec", "float", "0",
            "The end of a song fades out while the next one fades in, over this\n"
            "many seconds. 0 = one after the other, as before. 10 at most.\n"
            "Not before a spoken introduction, the cutoff or a planned restart.",
            "Crossfade (sec)"),
    Setting("START_FADE_SEC", "fades", "start_sec", "float", "0",
            "When the music starts (at boot, at the set time, when the speaker\n"
            "connects, or on a press), the first song rises from silence to the\n"
            "volume over this many seconds - gentle for a wake-up. 0 = at once.\n"
            "At most 120 seconds.", "Start fade-in (sec)"),
    Setting("PAUSE_FADE_SEC", "fades", "pause_sec", "float", "1",
            "Pausing a song from the web interface: the music fades out, then\n"
            "pauses - and fades back in on resume, over the same time. 0 = cut\n"
            "and resume at once. At most 5 seconds.", "Pause fade (sec)"),

    Setting(
        "PLAYBACK_ERROR_MAX_RETRIES", "playback_errors", "pause_after_failures", "int", "5",
        "An unreadable file never stops the radio: it is logged and playback\n"
        "moves to the next track. After this many CONSECUTIVE failures, the\n"
        "next attempt is postponed instead of racing through a broken folder.\n"
        "A single file that plays resets the count.\n"
        "0 = disable entirely (failures still recorded, never grouped).",
        "Pause after N failed tracks",
    ),
    Setting(
        "PLAYBACK_ERROR_BACKOFF_SEC", "playback_errors", "retry_delay_sec", "float", "10",
        "How long to wait before retrying once the threshold above is reached.\n"
        "0 = no waiting: the run of failures is still RECORDED, but playback\n"
        "continues immediately.",
        "Retry delay (sec)",
    ),

    Setting(
        "BT_CLOCK_ENABLED", "clock", "bluetooth_fallback", "bool", "false",
        "Fallback #1 = hardware RTC (/dev/rtc0), detected automatically,\n"
        "nothing to set here.\n"
        "Fallback #2 = time read over Bluetooth (Current Time Service) from a\n"
        "device paired beforehand. Only used if the RTC is absent, and not\n"
        "reliable on every device (see docs/guide.md).",
        "Bluetooth clock fallback",
    ),
    Setting("BT_CLOCK_MAC", "clock", "bluetooth_mac", "str", "XX:XX:XX:XX:XX:XX",
            "", "Clock device MAC"),
    Setting(
        "CLOCK_SYNC_GRACE_SEC", "clock", "sync_grace_sec", "float", "30",
        "Time given to the fallbacks above before the scheduler starts\n"
        "evaluating time-based triggers anyway, with whatever time is known.",
        "Clock sync grace (sec)",
    ),
    Setting("SPEAKER_READY_TIMEOUT_SEC", "clock", "speaker_ready_timeout_sec", "float", "20",
            "How long to wait for the speaker before playing the clock\n"
            "confirmation sound (beyond that, the sound is skipped).",
            "Speaker ready timeout (sec)"),

    Setting(
        "WEB_PORT", "network", "web_port", "int", "80",
        "80 on purpose, so the interface is just http://<the Pi's address>\n"
        "with nothing to remember or type after it - which is also what makes\n"
        "the captive portal below able to hand a phone the real page rather\n"
        "than a redirect. Binding it as the \"pi\" user works thanks to the\n"
        "capability granted in systemd/rukebox-web.service. An existing\n"
        "installation keeps whatever it already had (8080 was the old\n"
        "default): updates never rewrite a value already in this file.",
        "Web interface port",
    ),
    Setting(
        "WEB_EXTRA_HOSTS", "network", "web_extra_hosts", "str", "",        "Other names the interface may be opened under (comma-separated).\n"
        "Its address and its own name on a local network (rukebox,\n"
        "rukebox.local, .lan, .home, .internal...) are always accepted; any\n"
        "other name pointing at the Pi has to be listed here, or the interface\n"
        "refuses it - that refusal is what stops another site from reading\n"
        "the interface.",
        "Other accepted host names",
    ),
    Setting(
        "SETUP_HIDDEN", "network", "setup_hidden", "str", "",
        "Items of the web interface's \"To finish\" card that were set aside\n"
        "(comma-separated: speaker, clock, timezone, music, password, ap_open).",
        "Hidden setup items",
    ),
    Setting(
        "HOME_WIFI_CONN_NAME", "network", "home_wifi_connection", "str", "",
        "NetworkManager profile created by scripts/setup_home_wifi.sh.\n"
        "Empty = feature not configured (its web interface card stays hidden).",
        "Personal Wi-Fi profile",
    ),
    Setting(
        "HOME_WIFI_ENABLED", "network", "home_wifi_enabled", "bool", "true",
        "Whether that connection is wanted UP. The web interface's switch\n"
        "writes it; scripts/home-wifi-connect.sh reads it back on every turn\n"
        "and takes the connection back when NetworkManager gave up on it -\n"
        "which is what left the Pi off its own network for twenty minutes\n"
        "once. It stays off when this is false, so switching it off by hand\n"
        "is never undone behind your back.",
        "Personal Wi-Fi wanted",
    ),
    Setting(
        "CAPTIVE_PORTAL_MODE", "network", "captive_portal_mode", "str", "release",
        "What happens once someone has opened the portal page.\n"
        "  release -> stop intercepting THAT device's connectivity\n"
        "             checks, so its phone marks the network as a normal\n"
        "             one and stays connected. This is what a hotel or a\n"
        "             train portal does, and without it the phone never\n"
        "             gets the answer it is waiting for: it keeps the\n"
        "             network in its \"captive\" state and drops it after\n"
        "             a few minutes, closing the portal window with it.\n"
        "  new_only -> like release, and a device that has already opened\n"
        "             the page once is never intercepted again\n"
        "  hold    -> keep intercepting forever, so the portal page\n"
        "             reappears on every connectivity check. The\n"
        "             original behaviour; useful if you WANT the page\n"
        "             pushed at every reconnection, at the cost of the\n"
        "             phone never settling on the network.\n"
        "Releases are remembered per device (by IP) and forgotten when\n"
        "the web server restarts.",
        "Captive portal mode",
    ),
    Setting(
        "SKIP_VOTE_ENABLED", "security", "skip_vote", "bool", "true",
        "Everyone with the page open may vote to skip the song (free, one\n"
        "vote per person and per song). Offered from two people present.",
        "Vote to skip",
    ),
    Setting(
        "SKIP_VOTE_SHARE", "security", "skip_vote_share", "int", "50",
        "The song changes once MORE than this share (%) of the people with the\n"
        "page open voted, two votes at least.",
        "Votes needed (%)",
    ),
    Setting(
        "ACTION_REPEAT_SEC", "security", "action_repeat_sec", "float", "1",
        "For this long after an action ends, the same kind of action from\n"
        "someone else is not run (they get the first answer and pay nothing):\n"
        "several people pressing Next together skip one song. 0 = off.",
        "Same action from someone else ignored for (sec)",
    ),
    Setting(
        "GUEST_QUOTA_ENABLED", "security", "guest_quota", "bool", "true",
        "Guests (guest_mode, below) spend credits on their actions, so the same\n"
        "song again and again, or skip-skip-skip, runs dry fast while an\n"
        "occasional press never does. Nothing for whoever is past the password.",
        "Guest credits",
    ),
    Setting("GUEST_QUOTA_MAX", "security", "guest_quota_max", "int", "10",
            "Credits a guest holds at most (and starts with).", "Credits at most"),
    Setting("GUEST_QUOTA_REFILL_SEC", "security", "guest_quota_refill_sec", "int", "120",
            "One credit comes back every this many seconds.", "One credit back every (sec)"),
    Setting("GUEST_QUOTA_REPEAT_MIN", "security", "guest_quota_repeat_min", "int", "10",
            "An action's price doubles for every time the same guest did it within\n"
            "this many minutes.", "Repeat window (min)"),
    Setting("GUEST_COST_NEXT", "security", "guest_cost_next", "int", "1",
            "Credits each guest action costs, before doubling when repeated\n(guest_quota_repeat_min). 0 = free.", "Price: Next track"),
    Setting("GUEST_COST_PREVIOUS", "security", "guest_cost_previous", "int", "1",
            "", "Price: Previous track"),
    Setting("GUEST_COST_START", "security", "guest_cost_start", "int", "1",
            "", "Price: Start the music"),
    Setting("GUEST_COST_PAUSE", "security", "guest_cost_pause", "int", "1",
            "The middle button does both: pause or resume a song, and skip a\n"
            "sound that is playing.", "Price: Pause, resume, skip a sound"),
    Setting("GUEST_COST_SOUND", "security", "guest_cost_sound", "int", "2",
            "", "Price: Sound + next"),
    Setting("GUEST_COST_ANNOUNCE", "security", "guest_cost_announce", "int", "3",
            "", "Price: Announcement"),
    Setting("GUEST_COST_VOLUME", "security", "guest_cost_volume", "int", "1",
            "", "Price: Volume (one charge per 10 sec of slider)"),
    Setting("GUEST_COST_QUEUE", "security", "guest_cost_queue", "int", "1",
            "", "Price: Put a song up next (library, recently played, suggestion)"),
    Setting("GUEST_COST_PLAY_NOW", "security", "guest_cost_play_now", "int", "1",
            "", "Price: Play a song of Up next now"),
    Setting("GUEST_COST_OUTPUT", "security", "guest_cost_output", "int", "1",
            "", "Price: Another audio output (planned one missing)"),
    Setting("DEDICATIONS_ENABLED", "security", "dedications", "bool", "false",
            "Someone putting a song up next may add a short message, said out\n"
            "loud just before the song (\"For Marie, from Blue fox: ...\").\n"
            "The owner can remove a message from Up next before it is said.",
            "Dedications"),
    Setting("QUEUE_FAIR", "security", "queue_fair", "bool", "true",
            "Songs asked for take turns between people - one each, then the\n"
            "next round - instead of first come, first served, so one person\n"
            "cannot fill the whole queue.",
            "Take turns between people"),
    Setting(
        "GUEST_PAGES_OFF", "security", "guest_pages_off", "str", "",
        "Home pages a guest does not get, comma-separated: upnext, recent,\n"
        "today, library, game, suggest. Their requests are refused too.\n"
        "Empty = every page a guest may see.",
        "Pages hidden from guests",
    ),
    Setting(
        "GUEST_LOCKED", "security", "guest_locked", "str", "",
        "Guest commands locked whatever the credits, comma-separated: next,\n"
        "previous, start, pause, sound, announce, volume, queue, play_now,\n"
        "output (the names of the prices above). Empty = none.",
        "Locked for guests",
    ),
    Setting(
        "GUEST_MODE_ENABLED", "security", "guest_mode", "bool", "false",
        "Lets anyone on the access point use the BUTTON actions - sound,\n"
        "announcement, start the music, volume - without knowing the web\n"
        "interface password. Shutting the Pi down, and every setting, stay\n"
        "behind the password.\n"
        "Only does anything when web_password_hash is set: with no\n"
        "password the whole interface is already open to anyone on the\n"
        "access point (see docs/guide.md, Web interface section).\n"
        "Off by default because turning it on is a deliberate widening of\n"
        "what an unauthenticated visitor can do - decide it yourself\n"
        "rather than have an update decide it for you.",
        "Guest actions without password",
    ),
    Setting(
        "SUGGESTIONS_ENABLED", "suggestions", "enabled", "bool", "true",
        "The suggestion box on the Home section: anyone who can open the\n"
        "interface - guests included, when guest access is on - proposes\n"
        "music or announcements to add, under a name of their choice that\n"
        "no other device can take, and votes the others up or down (one\n"
        "vote per suggestion per device). The owner sees every name change.",
        "Suggestion box",
    ),
    Setting(
        "SUGGESTIONS_RENAME_INTERVAL_MIN", "suggestions", "rename_interval_min", "int", "60",
        "Minimum time between two changes of name for one device, in\n"
        "minutes (0 = no limit). Taking a first name is never delayed.",
        "Delay between two name changes (min)",
    ),
    Setting(
        "LIBRARY_DB_FILE", "paths", "library_db", "str",
        "/var/lib/rukebox/library.db",
        "The music library's catalogue (titles, artists, albums, genres), read\n"
        "once per file by the web server - searching the library and telling\n"
        "whether a suggested song is already there (SQLite).",
        "Library catalogue",
    ),
    Setting(
        "SUGGESTIONS_DB_FILE", "suggestions", "database", "str",
        "/var/lib/rukebox/suggestions.db",
        "Where the suggestions, votes and names are kept (SQLite).",
        "Suggestions database",
    ),
    Setting(
        "CAPTIVE_PORTAL_ENABLED", "network", "captive_portal", "bool", "true",
        "Pop the web interface up automatically on a phone that joins the\n"
        "admin access point, the way hotel Wi-Fi does, so nobody has to know\n"
        "or type an address. On by default: on a screenless appliance,\n"
        "\"connect to the Wi-Fi and the page appears\" is the difference\n"
        "between usable and not. It works by answering the handful of \"am I\n"
        "behind a portal?\" URLs every OS checks (see src/captive_portal.py)\n"
        "- only those hostnames, so devices keep normal internet access.\n"
        "Needs the dnsmasq drop-in that scripts/setup_ap.sh installs.",
        "Captive portal",
    ),
    Setting(
        "AP_INTERFACE", "network", "ap_interface", "str", "uap0",
        "Admin access point interface, created by scripts/create_uap0.sh.\n"
        "NOTE: if you change this, update the matching 'iw' entry in\n"
        "/etc/sudoers.d/rukebox-poweroff, otherwise access point usage cannot\n"
        "be recorded in the statistics.",
        "Access point interface",
    ),

    Setting(
        "WEB_PASSWORD_HASH", "security", "password_hash", "str", "",
        "Password required to open the web interface, stored as a salted\n"
        "hash (never plain text) - set, changed or cleared from the web\n"
        "interface's own Security card, not meant to be hand-edited. Empty\n"
        "(default) = no password required, this project's original design.",
        "Web interface password (hash)",
    ),
    Setting(
        "WEB_SESSION_SECRET", "security", "session_secret", "str", "",
        "Random key signing the browser's login session cookie, generated\n"
        "automatically the first time a password is set. Never set this by\n"
        "hand; changing it logs every browser out at once.",
        "Session secret",
    ),
    Setting(
        "GPIO_RESET_ENABLED", "security", "gpio_reset_enabled", "bool", "true",
        "Whether grounding GPIO_RESET_PIN at boot clears the web password\n"
        "(see docs/guide.md, 'Web interface password'). Harmlessly inactive on\n"
        "anything that isn't a Raspberry Pi, or without 'pinctrl' installed.",
        "GPIO password reset",
    ),
    Setting(
        "GPIO_RESET_PIN", "security", "gpio_reset_pin", "int", "21",
        "BCM GPIO number checked once at boot, configured with its internal\n"
        "pull-up - so grounding it (a jumper wire to any GND pin) needs no\n"
        "external resistor. Default 21 = physical header pin 40. Change this\n"
        "if that pin is already used for something else on your Pi.",
        "GPIO reset pin (BCM)",
    ),

    Setting(
        "STATS_ENABLED", "statistics", "enabled", "bool", "true",
        "Records what the radio actually does: button presses and what they\n"
        "triggered, startups/shutdowns, announcements, hours listened,\n"
        "playback errors, speaker drops, access point usage. Consultable and\n"
        "resettable from the web interface. Nothing ever leaves the Pi.",
        "Statistics enabled",
    ),
    Setting("STATS_DB_FILE", "statistics", "database", "str",
            "/var/lib/rukebox/stats.db",
            "", "Statistics database"),
    Setting(
        "STATS_RETENTION_DAYS", "statistics", "retention_days", "float", "90",
        "How long the detailed event log is kept. Cumulative totals (hours\n"
        "listened, number of startups...) are NEVER pruned.",
        "Event retention (days)",
    ),
    Setting("STATS_MAX_EVENTS", "statistics", "max_events", "int", "20000",
            "Hard ceiling on stored events, whatever the retention above.",
            "Maximum events kept"),
    Setting("SPEAKER_WATCH_INTERVAL_SEC", "statistics", "speaker_watch_sec", "float", "10",
            "How often to check the speaker is still connected. 0 = disabled,\n"
            "except when something depends on that check (start_mode\n"
            "'bluetooth', pausing on speaker loss, the power-off delays): it\n"
            "then falls back to 10s instead of silently disabling them. One\n"
            "check costs ~40ms (bluetoothctl info), so 10s is cheap, and it is\n"
            "how long a pause on speaker loss can lag behind.",
            "Speaker check interval (sec)"),
    Setting(
        "RFID_READER", "hardware", "rfid_reader", "str", "",
        "A USB RFID reader or barcode scanner (one that types like a keyboard):\n"
        "part of the name it announces in /proc/bus/input/devices. Empty =\n"
        "the first device whose name says RFID or card reader.",
        "Card or barcode reader",
    ),
    Setting(
        "ACT_LED", "hardware", "act_led", "str", "default",
        "The Pi's green activity LED:\n"
        "  default -> its original behaviour (SD card activity)\n"
        "  off     -> always off (a bedroom at night)\n"
        "Applied at every boot by rukebox-act-led.service, and at once when\n"
        "changed from the web interface.",
        "Activity LED",
    ),
    Setting(
        "TRANSFER_LIMIT_MODE", "hardware", "transfer_limit", "str", "auto",
        "Slows file uploads down (music sync, sounds, backups) so they do not\n"
        "crowd out the Bluetooth sound on the radio it shares with Wi-Fi:\n"
        "  auto   -> while a sound plays to a connected speaker of the built-in\n"
        "            chip: music or an announcement, never the keep-alive chime\n"
        "            and never in pause\n"
        "  always -> always\n"
        "  off    -> never",
        "Limit transfers",
    ),
    Setting("TRANSFER_LIMIT_KBPS", "hardware", "transfer_limit_kbps", "int", "200",
            "Upload speed while limited, in kilobytes per second.",
            "Limited speed (KB/s)"),
    Setting(
        "TRANSFER_LIMIT_USB", "hardware", "transfer_limit_usb", "bool", "false",
        "In auto, limit too while the speaker is on a USB dongle. A dongle is a\n"
        "radio of its own, but a few centimetres from the board's antenna it\n"
        "can still disturb the Wi-Fi.",
        "Also with a speaker on a USB dongle",
    ),
    Setting(
        "USB_PORT_MODE", "hardware", "usb_port_mode", "str", "gadget",
        "The Pi Zero's USB data port:\n"
        "  gadget -> network over the USB cable to a computer (SSH, the web\n"
        "            interface at 169.254.7.7, Internet through the computer)\n"
        "  host   -> plug devices in instead (Bluetooth dongle, sound card,\n"
        "            USB key) through an OTG adapter; the cable access is lost\n"
        "Applied by rukebox-usb-gadget.service; takes full effect after a reboot.",
        "USB port",
    ),
    Setting(
        "USB_MUSIC_SWITCH", "hardware", "usb_music_switch", "str", "after",
        "What happens to the song playing when the music starts coming from a\n"
        "storage device (a key or a disk the radio reads its music from):\n"
        "  after -> it finishes, and the device takes over on the next one\n"
        "  now   -> it is replaced straight away by one of the device's own\n"
        "The card's \"Play now\" does the same thing once, whatever this says.",
        "Changing to a storage device",
    ),
    Setting("AP_WATCH_INTERVAL_SEC", "statistics", "ap_watch_sec", "float", "60",
            "How often to look at who is connected to the access point.\n"
            "0 = disabled.", "Access point check interval (sec)"),

    Setting(
        "UPDATE_GIT_URL", "updates", "git_url", "str", "",
        "Updating over the USB cable (bootstrap/push_update.sh or .cmd) needs\n"
        "nothing here. This is only for updating from a Git repository, which\n"
        "requires the Pi to have temporary network access. Empty = disabled.",
        "Git repository URL",
    ),
    Setting("UPDATE_GIT_BRANCH", "updates", "git_branch", "str", "main", "", "Git branch"),
    Setting(
        "UPDATE_GITHUB_REPO", "updates", "github_repo", "str", "Arubinu/Rukebox",
        "The GitHub repository (owner/name) whose releases the Update card\n"
        "offers. Checking needs the Pi to have Internet for a moment (home\n"
        "Wi-Fi, or the USB cable's second card); installing from the web\n"
        "interface also needs allow_from_web below. Empty = disabled.",
        "GitHub repository",
    ),
    Setting(
        "UPDATE_ALLOW_WEB", "updates", "allow_from_web", "bool", "false",
        "Allow the \"Update from Git\" and \"Install\" (GitHub release) buttons\n"
        "in the web interface.\n"
        "READ THIS BEFORE ENABLING: the web interface has NO authentication,\n"
        "so anyone on the Pi's access point could trigger a fetch-and-run as\n"
        "root. Off by default; USB and SSH updates need nothing of the sort.",
        "Allow updates from the web",
    ),
    Setting("UPDATE_VERSION_FILE", "updates", "version_file", "str",
            "/var/lib/rukebox/version.json",
            "", "Version file"),
    Setting(
        "UPDATE_DOCKER_IMAGE", "updates", "docker_image", "str", "ghcr.io/arubinu/rukebox",
        "The image a container updates FROM: not an update mechanism, but the\n"
        "one line the Update card shows there, since pulling a new image and\n"
        "recreating the container is the only way a container gets a new\n"
        "version. Ignored on a Pi, where update.sh replaces the tree.",
        "Container image",
    ),
    Setting("UPDATE_BACKUP_KEEP", "updates", "backup_keep", "int", "3",
            "How many pre-update backups of /opt/rukebox to keep for rollback.",
            "Backups kept"),

    Setting(
        "STATE_DIR", "paths", "state_dir", "str",
        "/var/lib/rukebox",
        "Everything that is not configuration: the statistics database, the\n"
        "library catalogue, the queue, the likes. Defaults to /var/lib/rukebox\n"
        "on a Pi, and to whatever RUKEBOX_STATE_DIR names elsewhere - the\n"
        "container image points it at its own /data volume.",
        "State directory"),
    Setting("MPV_SOCKET", "paths", "mpv_socket", "str", "/tmp/mpvsocket", "", "mpv IPC socket"),
    Setting("CONTROL_SOCKET", "paths", "control_socket", "str", "/tmp/rukebox_control.sock",
            "", "Control socket"),
    Setting("MUSIC_CACHE_FILE", "paths", "music_cache", "str",
            "/var/lib/rukebox/music_cache.json",
            "", "Music scan cache"),
    Setting(
        "ANNOUNCEMENTS_FILE", "paths", "announcements_file", "str",
        "/etc/rukebox/announcements.json",
        "Custom announcement types added from the web interface (name,\n"
        "folder, time of day) - see src/announcements.py. Plain JSON, not\n"
        "YAML: it is a growable list managed entirely through the web UI's\n"
        "own form, not meant for hand-editing like the settings above.",
        "Custom announcements file",
    ),
    Setting(
        "TRACK_ORDER_FILE", "paths", "track_order_file", "str",
        "/etc/rukebox/track_order.json",
        "Custom play order saved per announcement folder when\n"
        "announce_order_mode is \"ordered\" and you have picked a specific\n"
        "sequence from the web interface - see src/track_order.py. Same\n"
        "reasoning as announcements_file above: a plain JSON file, not part\n"
        "of the YAML settings.",
        "Track order file",
    ),
    Setting(
        "MUSIC_LISTS_FILE", "paths", "music_lists_file", "str",
        "/etc/rukebox/music_lists.json",
        "The music lists built from the web interface - see\n"
        "src/music_lists.py. Same reasoning as announcements_file above: a\n"
        "growable list managed entirely by its own form, so JSON rather\n"
        "than a hand-edited setting. A list is either manual (tracks picked\n"
        "one by one) or by genre (kept up to date from the library's tags);\n"
        "the radio plays one list, or everything when none is active.",
        "Music lists file",
    ),
    Setting(
        "SCHEDULES_FILE", "paths", "schedules_file", "str",
        "/etc/rukebox/schedules.json",
        "The schedules built from the web interface - see src/schedules.py:\n"
        "start the music, stop it, or both, on chosen days or one date, with\n"
        "settings that only hold while the schedule runs. Read by the daemon\n"
        "at every scheduler tick, so a change needs no restart.",
        "Schedules file",
    ),
    Setting(
        "CARDS_FILE", "paths", "cards_file", "str",
        "/etc/rukebox/cards.json",
        "The RFID cards and what each one starts - see src/cards.py. Read at\n"
        "every card, so a change needs no restart.",
        "Cards file",
    ),
    Setting(
        "LIKES_FILE", "paths", "likes_file", "str",
        "/var/lib/rukebox/likes.json",
        "The tracks liked from the player - see src/likes.py. A song is\n"
        "remembered by its library key, so the like survives a rescan; each\n"
        "entry carries the date it was liked, which is what the list shows.",
        "Liked tracks file",
    ),
    Setting(
        "HIDDEN_FILE", "paths", "hidden_file", "str",
        "/var/lib/rukebox/hidden.json",
        "The tracks a duplicate check kept aside - see src/hidden_tracks.py:\n"
        "the radio stops choosing them on its own. Nothing is deleted, and the\n"
        "file is still there to be played by hand.",
        "Hidden tracks file",
    ),
    Setting(
        "WLED_ENABLED", "lights", "enabled", "bool", "false",
        "Drive WLED light strips: each moment of the radio (music, pause,\n"
        "an announcement, the cutoff...) picks one of the presets made in WLED.",
        "WLED light strips",
    ),
    Setting(
        "WLED_HOSTS", "lights", "hosts", "str", "",
        "The address of each WLED, separated by commas (192.168.4.20 or\n"
        "wled-salon.local). A preset number means the same thing on each of them.",
        "WLED addresses",
    ),
    Setting(
        "WLED_SCENE_START", "lights", "scene_start", "int", "0",
        "The preset while the music starts and fades in.\n"
        "0 leaves the light as it is, -1 switches it off.",
        "When the music starts",
    ),
    Setting(
        "WLED_SCENE_PLAY", "lights", "scene_play", "int", "0",
        "The preset while a song plays.\n"
        "0 leaves the light as it is, -1 switches it off.",
        "While the music plays",
    ),
    Setting(
        "WLED_SCENE_PAUSE", "lights", "scene_pause", "int", "0",
        "The preset while the music is paused.\n"
        "0 leaves the light as it is, -1 switches it off.",
        "While paused",
    ),
    Setting(
        "WLED_SCENE_IDLE", "lights", "scene_idle", "int", "0",
        "The preset while the radio waits: before the start, in standby, after the list.\n"
        "0 leaves the light as it is, -1 switches it off.",
        "While waiting",
    ),
    Setting(
        "WLED_SCENE_ANNOUNCE", "lights", "scene_announce", "int", "0",
        "The preset while an announcement, a button sound or a sentence plays.\n"
        "0 leaves the light as it is, -1 switches it off.",
        "During an announcement",
    ),
    Setting(
        "WLED_SCENE_CUTOFF", "lights", "scene_cutoff", "int", "0",
        "The preset of the cutoff: its warning, then its announcement.\n"
        "0 leaves the light as it is, -1 switches it off.",
        "At the cutoff",
    ),
    Setting(
        "WLED_SCENE_GAME", "lights", "scene_game", "int", "0",
        "The preset while a blind test is on.\n"
        "0 leaves the light as it is, -1 switches it off.",
        "During the blind test",
    ),
    Setting(
        "WLED_SCENE_BUTTON", "lights", "scene_button", "int", "0",
        "A preset shown for three seconds on each press of a button.\n"
        "0 leaves the light as it is, -1 switches it off.",
        "On a button press",
    ),
    Setting(
        "WLED_SCENE_ALERT", "lights", "scene_alert", "int", "0",
        "A preset shown for three seconds when the speaker is lost or its battery is low.\n"
        "0 leaves the light as it is, -1 switches it off.",
        "On an alert",
    ),
    Setting(
        "WLED_OFF_AT_POWEROFF", "lights", "off_at_poweroff", "bool", "true",
        "Switch the light strips off when the radio switches itself off.",
        "Off with the radio",
    ),
    Setting(
        "WLED_AUDIO_SYNC", "lights", "audio_sync", "bool", "false",
        "Send the beat of the music to WLED's audio reactive effects, the way its\n"
        "UDP sound sync does: in WLED, set the sound settings' sync to Receive.\n"
        "It costs an ffmpeg process and a little of the processor while music plays.",
        "Light to the beat",
    ),
    Setting(
        "WLED_AUDIO_DELAY_MS", "lights", "audio_delay_ms", "int", "200",
        "How long the beat waits before it is sent, in milliseconds, so the light\n"
        "keeps time with what is heard: a Bluetooth speaker plays 150 to 250 ms\n"
        "late, a wired output barely at all (0 to 2000).",
        "Beat delay (ms)",
    ),
    Setting(
        "WLED_CLOCK", "lights", "clock", "bool", "true",
        "Share the time with WLED: the more reliable of the two gives it to the\n"
        "other. A radio with no clock module gets the time back from a WLED that\n"
        "stayed powered, once it has given that WLED the time at least once.",
        "Share the time",
    ),
]

BY_ENV = {s.env: s for s in SETTINGS}
BY_PATH = {s.path: s for s in SETTINGS}


def _environment_paths():
    """{ENV_KEY: path} for the roots this machine actually runs with.

    The defaults written in SETTINGS above are the Raspberry Pi's own, so the
    checkout's rukebox.yaml and the documentation around it read as the Pi's
    layout. This is what moves them for a machine that set RUKEBOX_CONFIG_DIR,
    RUKEBOX_STATE_DIR, RUKEBOX_MUSIC_DIR or RUKEBOX_INSTALL_DIR - the
    container image puts them under /config, /data and /music - without
    rewriting a single line of YAML.

    `paths` is imported here, not at the top: it imports nothing of ours, but
    keeping it out of the module's own imports says that nothing above this
    point depends on a root."""
    import paths

    return {
        "ANNOUNCEMENTS_FILE": paths.resolve("ANNOUNCEMENTS_FILE", paths.config("announcements.json")),
        "AP_CONNECT_SOUND": paths.resolve("AP_CONNECT_SOUND", paths.music("system", "ap_connect.wav")),
        "BATTERY_LOW_SOUND": paths.resolve("BATTERY_LOW_SOUND", paths.music("system", "battery_low.wav")),
        "CARDS_FILE": paths.resolve("CARDS_FILE", paths.config("cards.json")),
        "CLOCK_FALLBACK_SOUND": paths.resolve(
            "CLOCK_FALLBACK_SOUND", paths.music("system", "clock_fallback.wav")),
        "CLOCK_OK_SOUND": paths.resolve("CLOCK_OK_SOUND", paths.music("system", "clock_ok.wav")),
        "COVER_DIR": paths.resolve("COVER_DIR", paths.music("covers")),
        "CUTOFF_ANNOUNCE_DIR": paths.resolve(
            "CUTOFF_ANNOUNCE_DIR", paths.music("cutoff_announcements")),
        "DJ_ANNOUNCE_DIR": paths.resolve("DJ_ANNOUNCE_DIR", paths.music("dj_announcements")),
        "HIDDEN_FILE": paths.resolve("HIDDEN_FILE", paths.state("hidden.json")),
        "KEEPALIVE_SOUND": paths.resolve("KEEPALIVE_SOUND", paths.music("system", "keepalive.wav")),
        "LIBRARY_DB_FILE": paths.resolve("LIBRARY_DB_FILE", paths.state("library.db")),
        "LIKES_FILE": paths.resolve("LIKES_FILE", paths.state("likes.json")),
        "MEME_DIR": paths.resolve("MEME_DIR", paths.music("memes")),
        "MUSIC_CACHE_FILE": paths.resolve("MUSIC_CACHE_FILE", paths.state("music_cache.json")),
        "MUSIC_DIR": paths.resolve("MUSIC_DIR", paths.music("music")),
        "MUSIC_LISTS_FILE": paths.resolve("MUSIC_LISTS_FILE", paths.config("music_lists.json")),
        "RESTART_SOUND": paths.resolve("RESTART_SOUND", paths.music("system", "restart.wav")),
        "SCHEDULES_FILE": paths.resolve("SCHEDULES_FILE", paths.config("schedules.json")),
        "STATE_DIR": paths.resolve("STATE_DIR", paths.state_dir()),
        "STATS_DB_FILE": paths.resolve("STATS_DB_FILE", paths.state("stats.db")),
        "SUGGESTIONS_DB_FILE": paths.resolve("SUGGESTIONS_DB_FILE", paths.state("suggestions.db")),
        "TRACK_ORDER_FILE": paths.resolve("TRACK_ORDER_FILE", paths.config("track_order.json")),
        "UPDATE_VERSION_FILE": paths.resolve("UPDATE_VERSION_FILE", paths.state("version.json")),
    }


DEFAULTS = {s.env: s.default for s in SETTINGS}
DEFAULTS.update(_environment_paths())

BOOL_TRUE = ("1", "true", "yes", "on")


def coerce(setting, raw):
    """Turns the stored string into the type the code expects."""
    if setting.type == "bool":
        return str(raw).strip().lower() in BOOL_TRUE
    if setting.type == "int":
        try:
            return int(float(str(raw).strip()))
        except (TypeError, ValueError):
            return int(float(setting.default))
    if setting.type == "float":
        try:
            return float(str(raw).strip())
        except (TypeError, ValueError):
            return float(setting.default)
    return str(raw)


def to_raw(setting, value):
    """The inverse: the string form stored in the files."""
    if setting.type == "bool":
        if isinstance(value, bool):
            return "true" if value else "false"
        return "true" if str(value).strip().lower() in BOOL_TRUE else "false"
    return str(value).strip()


def sections_with_settings():
    """[(section, description, [Setting, ...]), ...] in file order."""
    known = {name for name, _ in SECTIONS}
    unknown = sorted({s.section for s in SETTINGS} - known)
    if unknown:
        raise ValueError(
            "config_schema: setting(s) in unknown section(s) %s - add the "
            "section to SECTIONS or fix the Setting. Known sections: %s"
            % (", ".join(unknown), ", ".join(sorted(known)))
        )
    grouped = []
    for name, description in SECTIONS:
        items = [s for s in SETTINGS if s.section == name]
        if items:
            grouped.append((name, description, items))
    return grouped


RESTART_REQUIRED = frozenset({
    "STATS_ENABLED", "STATS_DB_FILE", "STATS_RETENTION_DAYS", "STATS_MAX_EVENTS",
    "AP_WATCH_INTERVAL_SEC", "AP_INTERFACE",
    "STATE_DIR", "MPV_SOCKET", "CONTROL_SOCKET", "MUSIC_CACHE_FILE",
    "ANNOUNCEMENTS_FILE", "TRACK_ORDER_FILE", "SPEAKER_BT_ADAPTER",
    "FLIC_HCI_DEVICE", "WEB_PORT",
})

# A save rebuilds the encoder instead of asking for a restart: it is built on
# the next status read, which is a second away. The stream's own volume is not
# here: it is handed to the encoder that is running, which keeps its listeners.
STREAM_SETTINGS = frozenset({"STREAM_ENABLED", "STREAM_ENCODER", "STREAM_SOURCE"})

# What a player sees. There is no switch of its own: the announcement exists
# exactly while the stream does (see web_server._upnp_follow_stream), so a save
# only ever has a name to hand over. The identity is not here either - it is
# drawn once and never changes, so a save never has to re-announce the device.
UPNP_SETTINGS = frozenset({"UPNP_NAME"})

GPIO_BUTTON_SETTINGS = frozenset({
    "GPIO_BUTTON_PIN", "GPIO_BUTTON_DEBOUNCE_SEC",
    "GPIO_BUTTON_DOUBLE_CLICK_WINDOW_SEC", "GPIO_BUTTON_LONG_PRESS_SEC",
})

SYSTEM_SOUNDS = ("CLOCK_OK_SOUND", "CLOCK_FALLBACK_SOUND", "AP_CONNECT_SOUND",
                 "BATTERY_LOW_SOUND", "RESTART_SOUND", "KEEPALIVE_SOUND")
