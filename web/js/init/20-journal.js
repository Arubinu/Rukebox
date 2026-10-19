function formatTimeOnly(epochSeconds) {
  if (!epochSeconds) return "—";
  const d = new Date(Number(epochSeconds) * 1000);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function formatAgo(seconds) {
  const s = Math.max(0, Math.round(Number(seconds) || 0));
  if (s < 10) return t("clients.ago_now");
  if (s < 60) return t("clients.ago_seconds", { n: s });
  const minutes = Math.round(s / 60);
  if (minutes < 60) return t("clients.ago_minutes", { n: minutes });
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return t("clients.ago_hours", { n: hours });
  const days = Math.floor(hours / 24);
  if (days === 1) return t("clients.ago_yesterday", { time: formatTimeOnly(Date.now() / 1000 - s) });
  return t("clients.ago_days", { n: days });
}

function formatBytes(n) {
  const v = Number(n) || 0;
  if (v < 1024) return v + " B";
  if (v < 1024 * 1024) return (v / 1024).toFixed(1) + " KB";

  if (v < 1024 * 1024 * 1024) return (v / (1024 * 1024)).toFixed(1) + " MB";
  return (v / (1024 * 1024 * 1024)).toFixed(1) + " GB";
}

const EVENT_TYPE_KEYS = ["session_start", "session_end", "session_unclean", "shutdown",
  "clock_ready", "clock_unreliable", "clock_manual_set", "timezone_set", "click", "track_played",
  "meme_played", "announce_played", "playback_error", "playback_stalled",
  "speaker_disconnected", "speaker_reconnected", "speaker_silent", "speaker_battery", "speaker_battery_low", "speech_played", "skip_voted", "game_started", "game_over", "game_hall_reset", "game_asked_reset", "card_read", "card_saved", "folder_played", "ap_client_connected",
  "ap_client_disconnected", "web_session", "music_started", "music_list_stopped",
  "music_rescan", "track_order_changed", "track_order_reset",
  "cutoff_triggered", "volume_set", "settings_changed",
  "account_added", "account_changed", "account_removed", "volume_curve_changed",
  "daemon_restart", "ssh_toggle", "home_wifi_toggle", "bluetooth_connect",
  "bluetooth_disconnect", "stats_reset", "run_erased",

  "update_started", "update_applied", "update_rolled_back",
  "announcement_type_added", "announcement_type_updated",
  "announcement_type_removed", "custom_announce_triggered",
  "ap_config_changed", "web_login", "web_password_changed", "stream_listened",

  "track_skipped", "sound_skipped",
  "track_previous", "timed_pause", "sleep_timer", "loop_mode",
  "track_replayed", "device_banned", "device_unbanned", "output_override",

  "announce_file_added", "announce_file_removed", "announce_on_demand", "announce_skipped",
  "audio_restart", "bluetooth_pair", "config_exported", "config_imported", "config_reloaded",
  "counters_reset", "music_upload", "playback_pause", "portal_released", "stats_rows_deleted",
  "system_sound_off", "system_sound_reset", "system_sound_set", "track_queued", "device_free_credits",
  "device_renamed", "device_name_locked", "portal_reset", "device_forgotten",
  "devices_linked", "device_unlinked", "dedication_played", "reminder_said", "bluetooth_forget", "bluetooth_remote",
  "schedule_started", "schedule_ended", "schedule_stop",
  "schedule_added", "schedule_changed", "schedule_removed",
  "standby", "mute", "backup_exported", "backup_restored", "system_reboot",
  "flic_sdk_installed", "flic_enabled", "flic_disabled", "flic_button_removed", "home_wifi_autoconnect",
  "track_liked", "track_unliked", "track_hidden", "track_shown",
  "list_selected", "list_added", "list_changed", "list_removed",
  "announcement_volume_set", "prepared_file_added", "usb_music_used", "usb_music_released",
  "tags_changed", "cover_changed", "cover_removed", "covers_moved", "cast_started",
  "party_started", "party_ended", "station_added", "station_removed", "sync_run", "sync_saved",
  "sync_connection_saved", "sync_host_key_pinned", "shutdown_held"];
function eventLabel(type) {
  return EVENT_TYPE_KEYS.includes(type) ? t("event." + type) : type;
}

const CLOCK_SOURCE_KEYS = ["rtc", "bluetooth", "manual", "host", "wled", "none"];
function clockSourceLabel(source) {
  return CLOCK_SOURCE_KEYS.includes(source) ? t("clocksrc." + source) : t("clocksrc.unknown");
}

const END_REASON_KEYS = ["cutoff", "long_press", "service_stop", "unclean", "speaker_lost", "speaker_absent",
  "button", "interface"];
function endReasonLabel(reason) {
  return END_REASON_KEYS.includes(reason) ? t("endreason." + reason) : reason;
}

const CLICK_ACTION_KEYS = ["sound_then_next", "next_track_no_sound", "start_from_idle",
  "announcement", "shutdown", "ignored", "previous_track", "sound_then_previous",
  "sound_then_resume", "paused", "resumed", "loop_track", "loop_album", "loop_off",
  "volume_up", "volume_down", "sleep_on", "sleep_off", "standby", "muted", "unmuted",
  "time_then_resume"];
function clickActionLabel(action) {
  return CLICK_ACTION_KEYS.includes(action) ? t("clickaction." + action) : action;
}

let oldestEventId = null;

function kpiTile(icon, label, value, sub, opts) {
  const options = opts || {};
  const tile = document.createElement("div");
  tile.className = "kpi" + (options.hero ? " kpi-hero" : "");

  const i = document.createElement("div");
  i.className = "kpi-icon" + (options.warn ? " warn" : "");
  i.dataset.icon = icon;
  i.setAttribute("aria-hidden", "true");

  const body = document.createElement("div");
  body.className = "kpi-body";
  const v = document.createElement("div");
  v.className = "kpi-value";
  v.textContent = value;
  const l = document.createElement("div");
  l.className = "kpi-label";
  l.textContent = label;
  body.append(v, l);
  if (sub) {
    const s = document.createElement("div");
    s.className = "kpi-sub";
    s.textContent = sub;
    body.appendChild(s);
  }

  tile.append(i, body);
  if (options.keys) armKpiTile(tile, label, options.keys, options.page);
  return tile;
}

const KPI_HOLD_MS = 700;

/* A brief press opens the statistics page that goes with the counter, a long
   one resets it: the page stops being something the menu alone knows about. */
function armKpiTile(tile, label, keys, page) {
  tile.tabIndex = 0;
  tile.classList.add("kpi-resettable");
  if (page) tile.classList.add("kpi-link");
  tile.setAttribute("aria-description", t(page ? "stats.kpi_press_hint" : "stats.kpi_hold_hint"));
  let timer = null;
  let start = null;
  const cancel = () => {
    clearTimeout(timer);
    timer = null;
    tile.classList.remove("kpi-holding");
  };
  const open = () => {
    if (page) setActiveView("stats", page);
  };
  const ask = async () => {
    cancel();
    if (!(await showConfirm(t("stats.kpi_reset_confirm", { name: label })))) return;
    const r = await apiPost("/api/journal/reset_counters", { keys });
    if (!r.ok) {
      showError(r.error);
      return;
    }
    showToast(t("stats.kpi_reset_done", { name: label }));
    refreshStats(true);
  };
  tile.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    start = { x: e.clientX, y: e.clientY };
    tile.style.setProperty("--hold-ms", KPI_HOLD_MS + "ms");
    tile.classList.add("kpi-holding");
    timer = setTimeout(ask, KPI_HOLD_MS);
  });
  tile.addEventListener("pointermove", (e) => {
    if (timer && start && Math.hypot(e.clientX - start.x, e.clientY - start.y) > 10) cancel();
  });
  /* The hold clears its own timer, so a release after it opens nothing. */
  tile.addEventListener("pointerup", (e) => {
    const brief = !!timer && (!start || Math.hypot(e.clientX - start.x, e.clientY - start.y) <= 10);
    cancel();
    if (brief) open();
  });
  ["pointerleave", "pointercancel"].forEach((type) => tile.addEventListener(type, cancel));

  tile.addEventListener("contextmenu", (e) => e.preventDefault());
  tile.addEventListener("keydown", (e) => {
    if (e.key === "Delete" || e.key === "Backspace") {
      e.preventDefault();
      ask();
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      open();
    }
  });
}

function renderKpis(summary) {
  const placeholder = !summary;
  summary = summary || {};
  const keysIf = (keys) => (placeholder ? undefined : keys);
  const c = summary.counters || {};
  const grid = document.getElementById("kpiGrid");
  grid.innerHTML = "";

  const totalListening =
    (c.seconds_music || 0) + (c.seconds_meme || 0) + (c.seconds_announce || 0);
  const totalClicks =
    (c.clicks_single || 0) + (c.clicks_double || 0) + (c.clicks_long || 0) + (c.clicks_speaker || 0);

  const errorCount = Math.round(c.playback_errors || 0);
  const speakerLostCount = Math.round(c.speaker_drops || 0);

  grid.append(
    kpiTile("headphones", t("kpi.music_listened"), formatDuration(c.seconds_music),
      t("kpi.music_listened_sub", { total: formatDuration(totalListening) }), { hero: true, keys: keysIf(["seconds_music", "seconds_meme", "seconds_announce"]), page: "recap" }),
    kpiTile("music", t("kpi.tracks_played"), Math.round(c.tracks_played || 0),
      t("kpi.tracks_played_sub", { memes: Math.round(c.memes_played || 0), announcements: Math.round(c.announcements_played || 0) }), { keys: keysIf(["tracks_played", "memes_played", "announcements_played"]), page: "tops" }),
    kpiTile("pointer", t("kpi.button_presses"), Math.round(totalClicks),
      t("kpi.button_presses_sub", { single: Math.round(c.clicks_single || 0), double: Math.round(c.clicks_double || 0), long: Math.round(c.clicks_long || 0), speaker: Math.round(c.clicks_speaker || 0) }), { keys: keysIf(["clicks_single", "clicks_double", "clicks_long", "clicks_speaker"]) }),
    kpiTile("antenna", t("kpi.click_sources"),
      Math.round(c.clicks_flic || 0) + " / " + Math.round(c.clicks_gpio || 0) + " / " + Math.round(c.clicks_remote || 0) + " / " + Math.round(c.clicks_web || 0),
      t("kpi.click_sources_sub", { ignored: Math.round(c.clicks_ignored || 0) }), { keys: keysIf(["clicks_flic", "clicks_gpio", "clicks_remote", "clicks_web", "clicks_ignored"]) }),
    kpiTile("play", t("kpi.startups"), Math.round(c.sessions_started || 0),
      t("kpi.startups_sub", { used: Math.round(c.sessions_used || 0), unclean: Math.round(c.sessions_unclean || 0) }), { keys: keysIf(["sessions_started", "sessions_used", "sessions_unclean"]), page: "sessions" }),
    kpiTile("power", t("kpi.shutdowns"), Math.round((c.shutdowns_cutoff || 0) + (c.shutdowns_longpress || 0) + (c.shutdowns_speaker || 0)),
      t("kpi.shutdowns_sub", { scheduled: Math.round(c.shutdowns_cutoff || 0), longpress: Math.round(c.shutdowns_longpress || 0), speaker: Math.round(c.shutdowns_speaker || 0) }), { keys: keysIf(["shutdowns_cutoff", "shutdowns_longpress", "shutdowns_service", "shutdowns_speaker"]), page: "sessions" }),
    kpiTile("alert", t("kpi.playback_errors"), errorCount,
      t("kpi.playback_errors_sub", { n: Math.round(c.playback_stalls || 0) }), { warn: errorCount > 0, keys: keysIf(["playback_errors", "playback_stalls"]), page: "errors" }),
    kpiTile("speaker", t("kpi.speaker_lost"), speakerLostCount,
      t("kpi.speaker_lost_sub", { n: Math.round(c.speaker_recoveries || 0) }), { warn: speakerLostCount > 0, keys: keysIf(["speaker_drops", "speaker_recoveries"]) }),
    kpiTile("wifi", t("kpi.access_point"), Math.round(c.ap_client_connections || 0),
      t("kpi.access_point_sub", { n: Math.round(c.web_sessions || 0) }), { keys: keysIf(["ap_client_connections", "web_sessions"]) }),
    kpiTile("clock", t("kpi.time_established"),
      ["clock_rtc", "clock_bluetooth", "clock_manual", "clock_host", "clock_wled"]
        .reduce((total, key) => total + Math.round(c[key] || 0), 0),
      t("kpi.time_established_sub", { host: Math.round(c.clock_host || 0), bt: Math.round(c.clock_bluetooth || 0), wled: Math.round(c.clock_wled || 0), manual: Math.round(c.clock_manual || 0), failed: Math.round(c.clock_unreliable || 0) }), { keys: keysIf(["clock_rtc", "clock_bluetooth", "clock_manual", "clock_host", "clock_wled", "clock_unreliable"]) }),
  );

  if (placeholder) {
    grid.querySelectorAll(".kpi-value").forEach((v) => { v.textContent = "\u2013"; });
    grid.querySelectorAll(".kpi-sub").forEach((v) => { v.textContent = "\u00a0"; });
    return;
  }

  const period = document.getElementById("statsPeriod");
  const from = summary.reset_at || summary.created_at || summary.since;
  period.textContent = t("stats.since_prefix", {
    date: formatDateTime(from),
    count: summary.event_count || 0,
    days: Math.round(summary.retention_days || 0),
    size: formatBytes(summary.db_size_bytes),
  });
}

let chartDaySelected = null;

function renderChartDay(series) {
  const panel = document.getElementById("chartDay");
  const day = chartDaySelected && (series || []).find((d) => d.day === chartDaySelected);
  panel.hidden = !day;
  if (!day) {
    chartDaySelected = null;
    return;
  }
  const date = new Date(day.day + "T12:00:00");
  const title = document.createElement("h3");
  title.textContent = new Intl.DateTimeFormat(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric" }).format(date);
  const close = document.createElement("button");
  close.type = "button";
  close.className = "btn btn-icon btn-small";
  close.dataset.icon = "x";
  close.setAttribute("aria-label", t("common.close"));
  close.addEventListener("click", () => {
    chartDaySelected = null;
    renderDailyChart(series);
  });
  const head = document.createElement("div");
  head.className = "chart-day-head";
  head.append(title, close);
  const rows = [
    ["stats.r_music", formatDuration(day.seconds_music)],
    ["stats.r_sounds", formatDuration(day.seconds_meme)],
    ["stats.r_announces", formatDuration(day.seconds_announce)],
    ["stats.r_tracks", Math.round(day.tracks_played || 0)],
    ["stats.r_clicks", Math.round(day.clicks || 0)],
    ["stats.r_errors", Math.round(day.playback_errors || 0)],
    ["stats.r_startups", Math.round(day.sessions_started || 0)],
    ["stats.r_web", Math.round(day.web_sessions || 0)],
  ];
  const dl = document.createElement("dl");
  rows.forEach(([key, value]) => {
    const wrap = document.createElement("div");
    const dt = document.createElement("dt");
    dt.textContent = t(key);
    const dd = document.createElement("dd");
    dd.textContent = value;
    wrap.append(dt, dd);
    dl.append(wrap);
  });
  panel.replaceChildren(head, dl);
}

function renderDailyChart(series) {
  const box = document.getElementById("dailyChart");
  box.innerHTML = "";
  if (!series || !series.length) {
    box.textContent = t("stats.no_data");
    renderChartDay(null);
    return;
  }
  const max = Math.max(...series.map((d) => d.seconds_music), 1);

  const step = Math.max(1, Math.ceil(series.length / 7));

  series.forEach((day, i) => {
    const col = document.createElement("div");
    col.className = "chart-col";
    col.title = t("chart.tooltip", { date: day.day, duration: formatDuration(day.seconds_music), tracks: Math.round(day.tracks_played) }) +
      (day.playback_errors ? t("chart.tooltip_errors", { n: Math.round(day.playback_errors) }) : "");

    col.setAttribute("role", "button");
    col.tabIndex = 0;
    col.setAttribute("aria-label", col.title);
    col.dataset.day = day.day;
    const selected = chartDaySelected === day.day;
    col.classList.toggle("is-selected", selected);
    col.setAttribute("aria-pressed", selected ? "true" : "false");
    const pick = () => {
      chartDaySelected = chartDaySelected === day.day ? null : day.day;
      renderDailyChart(series);
    };
    col.addEventListener("click", pick);
    col.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        pick();
      }
    });

    const bar = document.createElement("div");
    bar.className = "chart-bar";

    const pct = day.seconds_music > 0 ? Math.max(4, (day.seconds_music / max) * 100) : 0;
    bar.style.height = pct + "%";
    if (day.playback_errors > 0) bar.classList.add("has-errors");

    const label = document.createElement("div");
    label.className = "chart-label" + ((series.length - 1 - i) % step ? " minor" : "");
    label.textContent = day.day.slice(8) + "/" + day.day.slice(5, 7);
    label.setAttribute("aria-hidden", "true");

    col.append(bar, label);
    box.appendChild(col);
  });
  renderChartDay(series);
}

function renderTopList(container, title, items, opts) {
  const options = opts || {};
  const block = document.createElement("div");
  block.className = "top-block";

  const h = document.createElement("div");
  h.className = "top-title";
  h.textContent = title;
  block.appendChild(h);

  if (!items || !items.length) {
    const empty = document.createElement("div");
    empty.className = "top-empty";
    empty.textContent = t("stats.nothing_recorded");
    block.appendChild(empty);
    container.appendChild(block);
    return;
  }

  const list = document.createElement("ul");
  list.className = "top-list";
  items.forEach((item) => {
    const li = document.createElement("li");

    const kind = options.kind === "custom_announce"
      ? "custom:" + item.kind
      : options.kind;
    li.append(selectionBox("items", JSON.stringify([kind, item.name])));
    let name;
    if (item.in_library) {
      name = libraryLink(item.name, { q: item.name }, "top-name");
    } else {
      name = document.createElement("span");
      name.className = "top-name";
      name.textContent = item.name;
    }
    const value = document.createElement("span");
    value.className = "top-value";
    value.textContent = options.showSeconds
      ? Math.round(item.count) + "x · " + formatDuration(item.seconds)
      : Math.round(item.count) + "x";
    li.append(name, value);
    list.appendChild(li);
  });
  block.appendChild(list);
  container.appendChild(block);
}

function listInUse(id) {
  const box = document.getElementById(id);
  return !!box && (box.dataset.extended === "1" || !!box.querySelector(".row-select:checked"));
}

function renderTops(summary, force) {
  const top = summary.top || {};
  const box = document.getElementById("topsBox");
  if (force || !listInUse("topsBox")) renderTopsInto(box, top);
  const errorsBox = document.getElementById("errorsBox");
  if (force || !listInUse("errorsBox")) {
    errorsBox.innerHTML = "";
    renderTopList(errorsBox, t("top.errors"), top.error, { kind: "error" });
  }
}

function renderTopsInto(box, top) {
  box.innerHTML = "";
  renderTopList(box, t("top.music"), top.music, { showSeconds: true, kind: "music" });
  renderTopList(box, t("top.cutoff"), top.cutoff_announce, { kind: "cutoff_announce" });
  renderTopList(box, t("top.meme"), top.meme, { kind: "meme" });

  renderTopList(box, t("top.custom"), top.custom_announce, { kind: "custom_announce" });
  // Only there while the statistics keep who asks: the server leaves it out otherwise.
  if (top.person) renderTopList(box, t("top.person"), top.person, { kind: "person" });
}

const SESSIONS_PAGE = 50;
let sessionsOldestId = null;

function renderSessions(summary, force) {
  const box = document.getElementById("sessionList");
  if (!force && listInUse("sessionList")) return;
  box.innerHTML = "";
  box.dataset.extended = "";
  const sessions = (summary.sessions && summary.sessions.recent) || [];
  sessionsOldestId = sessions.length ? sessions[sessions.length - 1].id : null;
  if (!sessions.length) {
    box.textContent = t("stats.no_startup");
    updateMoreSessions();
    return;
  }
  sessions.forEach((s) => box.appendChild(sessionRow(s)));
  updateMoreSessions();
}

function updateMoreSessions() {
  const loaded = document.querySelectorAll("#sessionList .row-select").length;
  document.getElementById("moreSessionsRow").hidden =
    !(sessionsOldestId !== null && remoteTotals.sessions > loaded);
}

document.getElementById("btnMoreSessions").addEventListener("click", async () => {
  const btn = document.getElementById("btnMoreSessions");
  btn.disabled = true;
  const result = await apiGet("/api/journal/sessions?limit=" + SESSIONS_PAGE +
                              (sessionsOldestId !== null ? "&before_id=" + sessionsOldestId : ""));
  btn.disabled = false;
  if (!result.ok || !result.data) return;
  const box = document.getElementById("sessionList");
  const rows = result.data.sessions || [];
  rows.forEach((s) => box.appendChild(sessionRow(s)));
  if (rows.length) {
    sessionsOldestId = rows[rows.length - 1].id;
    box.dataset.extended = "1";
  }
  if (rows.length < SESSIONS_PAGE) sessionsOldestId = null;
  updateMoreSessions();
  updateDeleteButtons();
});

function sessionRow(s) {
  const row = document.createElement("div");
  row.className = "session-row";

  const when = document.createElement("div");
  when.className = "session-when";
  when.textContent = formatDateTime(s.started_at);
  if (!s.clock_ok) when.classList.add("unreliable");

  const detail = document.createElement("div");
  detail.className = "session-detail";
  const parts = [];
  if (s.ended_at) {
    parts.push(t("session.until", { time: formatTimeOnly(s.ended_at) }));
    const duration = Number(s.ended_at) - Number(s.started_at);
    if (duration > 0) parts.push(formatDuration(duration));
  } else {
    parts.push(t("session.in_progress"));
  }
  parts.push(s.end_reason ? endReasonLabel(s.end_reason) : "—");
  parts.push(t("session.clock_prefix", { source: clockSourceLabel(s.clock_source) }));
  if (!s.used) parts.push(t("session.never_used"));
  detail.textContent = parts.join(" · ");

  row.append(s.ended_at ? selectionBox("sessions", s.id) : selectionSpacer(),
             when, detail);
  if (s.end_reason === "unclean") row.classList.add("session-unclean");
  return row;
}

function describeEvent(event) {
  const d = event.detail || {};
  switch (event.type) {
    case "click": {
      const source = d.source === "flic" ? t("evt.source_flic")
        : d.source === "gpio" ? t("evt.source_gpio")
        : d.source === "remote" ? t("evt.source_remote")
        : d.source === "web" ? t("evt.source_web")
        : d.source === "speaker" ? t("evt.source_speaker")
        : d.source === "usb" ? t("evt.source_usb") : t("evt.source_unknown");
      const speakerGesture = /^speaker_(playpause|next|previous|volumeup|volumedown)$/.exec(event.label || "");
      const kind = event.label === "single" ? t("evt.click_single")
        : event.label === "double" ? t("evt.click_double")
        : event.label === "long" ? t("evt.click_long")
        : speakerGesture ? t("speakerbtn." + speakerGesture[1]) : event.label;
      let text = t("evt.click_desc", { kind, source, action: clickActionLabel(d.action) || d.action || "—" });
      if (d.target) text += t("evt.click_target", { target: d.target });
      return text;
    }
    case "track_played":
    case "meme_played":
    case "announce_played":
      return t("evt.played_desc", { label: eventLabel(event.type), name: event.label || "—", duration: formatDuration(d.seconds) });
    case "session_start": {
      const pending = !event.label || event.label === "pending" || event.label === "clock pending";
      return t("evt.session_start_desc", { source: pending ? t("clocksrc.pending") : clockSourceLabel(event.label) });
    }
    case "stats_rows_deleted": {
      const lists = { events: "stats.eventlog_section", sessions: "stats.sessions_section",
        played: "stats.most_played_section", errors: "stats.errors_section", items: "evt.rows_items" };
      const list = lists[event.label] ? t(lists[event.label]) : (event.label || "—");
      return t("evt.rows_deleted_desc", { n: Math.round(d.count || 0), list })
        + (d.all ? " (" + t("evt.rows_all") + ")" : "");
    }
    case "playback_pause":
      return clickActionLabel(event.label === "paused" ? "paused" : "resumed");
    case "playback_error":
      return t("evt.playback_error_desc", { name: event.label || "—", error: d.error || t("common.unknown_error") });
    case "clock_ready":
      return t("evt.clock_ready_desc", { source: clockSourceLabel(event.label) }) +
        (d.offset_sec ? t("evt.clock_ready_offset", { n: Math.round(d.offset_sec) }) : "");
    case "clock_unreliable":
      return t("evt.clock_unreliable_desc");
    case "update_applied":
      return t("evt.update_applied_desc", {
        from: d.from || "?", to: d.to || "?", source: event.label || "?",
      });
    case "settings_changed":
    case "config_reloaded": {
      const keys = d.keys || d.applied || String(event.label || "").split(", ").filter(Boolean);
      const names = keys.map(settingName).join(", ");
      const label = event.who ? event.who + (names ? " (" + names + ")" : "") : names;
      return label ? t("evt.default_with_label", { base: eventLabel(event.type), label }) : eventLabel(event.type);
    }
    case "update_rolled_back":
      return t("evt.update_rolled_back_desc", { backup: event.label || "?" });
    case "session_end":
    case "shutdown":
      return t("evt.session_end_desc", { label: eventLabel(event.type), reason: event.label ? endReasonLabel(event.label) : "—" });
    default: {
      const base = eventLabel(event.type);

      const label = event.who ? event.who + (event.label ? " (" + event.label + ")" : "") : event.label;
      return label ? t("evt.default_with_label", { base, label }) : base;
    }
  }
}

function renderEvents(events, append) {
  const list = document.getElementById("eventList");
  if (!append) list.innerHTML = "";

  if (!events.length && !append) {
    const li = document.createElement("li");
    li.className = "event-empty";
    const filtered = document.getElementById("eventSearch").value.trim()
      || document.getElementById("eventTypeFilter").value;
    li.textContent = t(filtered ? "stats.no_match" : "stats.no_event");
    list.appendChild(li);
    return;
  }

  events.forEach((event) => {
    const li = document.createElement("li");
    li.className = "event-row";
    if (event.type === "playback_error" || event.type === "playback_stalled" ||
        event.type === "speaker_disconnected" || event.type === "session_unclean" ||
        event.type === "clock_unreliable") {
      li.classList.add("event-warn");
    }

    const time = document.createElement("span");
    time.className = "event-time";
    time.textContent = formatDateTime(event.ts);

    if (!event.clock_ok) {
      time.classList.add("unreliable");
      time.title = t("stats.recorded_before_clock_title");
      time.textContent = "~ " + time.textContent;
    }

    const text = document.createElement("span");
    text.className = "event-text";
    text.textContent = describeEvent(event);

    li.append(selectionBox("events", event.id), time, text);
    list.appendChild(li);
    oldestEventId = event.id;
  });
}

async function loadEvents(append) {
  const type = document.getElementById("eventTypeFilter").value;
  const query = document.getElementById("eventSearch").value.trim();
  let url = "/api/journal/entries?limit=60";
  if (type) url += "&type=" + encodeURIComponent(type);
  if (query) {
    url += "&q=" + encodeURIComponent(query);
    // The database only knows type names: the translated labels that match are
    // sent along.
    const needle = query.toLowerCase();
    const labels = EVENT_TYPE_KEYS.filter((key) => eventLabel(key).toLowerCase().includes(needle));
    if (labels.length) url += "&labels=" + encodeURIComponent(labels.join(","));
  }
  if (append && oldestEventId !== null) url += "&before_id=" + oldestEventId;

  const result = await apiGet(url);
  if (!result.ok) return;

  const filter = document.getElementById("eventTypeFilter");
  const current = filter.value;
  if (filter.options.length - 1 !== result.data.types.length) {
    filter.innerHTML = "";
    const allOpt = document.createElement("option");
    allOpt.value = "";
    allOpt.textContent = t("stats.all_events");
    filter.appendChild(allOpt);
    result.data.types.forEach((eventType) => {
      const opt = document.createElement("option");
      opt.value = eventType;
      opt.textContent = eventLabel(eventType);
      filter.appendChild(opt);
    });
    filter.value = current;
  }

  remoteTotals.events = Number(result.data.total) || 0;
  if (!append) remoteSelected.eventList = false;
  renderEvents(result.data.events, append);
  document.getElementById("btnMoreEvents").hidden = result.data.events.length < 60;
  updateDeleteButtons();
}

async function refreshStats(force) {
  force = !!force;
  const result = await apiGet("/api/journal/summary");
  const hint = document.getElementById("statsHint");
  if (!result.ok || !result.data || !result.data.enabled) {
    hint.textContent = t("stats.disabled");
    return;
  }
  hint.textContent = "";
  const totals = result.data.totals || {};
  remoteTotals.played = Number(totals.played) || 0;
  remoteTotals.errors = Number(totals.errors) || 0;
  remoteTotals.sessions = Number(totals.sessions) || 0;
  renderKpis(result.data);
  renderTops(result.data, force);
  renderSessions(result.data, force);
  updateDeleteButtons();

  const days = document.getElementById("statsDays").value;
  const daily = await apiGet("/api/journal/daily?days=" + days);
  if (daily.ok) renderDailyChart(daily.data);
  const week = days === "7" ? daily : await apiGet("/api/journal/daily?days=7");
  if (week.ok && Array.isArray(week.data)) renderRecentStats(week.data);
}

const SETUP_TARGETS = {
  storage: ["system", "healthTitle", "chip"],
  speaker: ["audio", "btTitle", "bluetooth"],
  speaker_pair: ["audio", "btTitle", "bluetooth"],
  flic_pair: ["audio", "btTitle", "bluetooth"],
  clock: ["system", "clockTitle", "clock"],
  timezone: ["system", "clockTitle", "globe"],
  music: ["home", "musicTitle", "music"],
  password: ["system", "securityTitle", "key"],
  ap_open: ["network", "apTitle", "wifi"],
  covers_left: ["system", "healthTitle", "image"],
  sync_secret: ["home", "syncTitle", "key"],
};
let setupHidden = [];

