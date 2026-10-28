const ON_SHOW_REFRESHES = [];
function refreshEvery(callback, ms, pages) {
  ON_SHOW_REFRESHES.push({ callback, pages });
  return setInterval(() => {
    if (!document.hidden && pageOnScreen(pages)) callback();
  }, ms);
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  ON_SHOW_REFRESHES.forEach(({ callback, pages }) => { if (pageOnScreen(pages)) callback(); });
});
// What was not refreshed while away is brought up to date the moment its page opens.
document.addEventListener("page-shown", () => {
  ON_SHOW_REFRESHES.forEach(({ callback, pages }) => { if (pages && pageOnScreen(pages)) callback(); });
});

refreshEvery(renderBrandClock, 1000);
setInterval(() => {
  document.querySelectorAll(".brand-clock").forEach((box) => {
    box.dataset.show = box.dataset.show === "time" ? "date" : "time";
  });
}, BRAND_CLOCK_SWAP_MS);
window.LANG_CHANGE_LISTENERS.push(renderBrandClock);

let statusRequestSeq = 0;
let statusPaintedSeq = 0;

let nowPlayingShown = null;
function setNowPlayingText(title, artist) {
  const signature = title + "\u0000" + artist;
  if (signature === nowPlayingShown) return;
  const first = nowPlayingShown === null;
  nowPlayingShown = signature;
  const box = document.getElementById("npText");
  document.getElementById("npTrack").textContent = title;

  document.getElementById("npArtist").textContent = artist || "\u00a0";
  if (first) return;
  box.classList.remove("np-enter");
  void box.offsetWidth;
  box.classList.add("np-enter");
}

const SOUND_LINE_MIN_MS = 5000;
let soundClearTimer = null;
function soundText(file, announcement, meme) {
  if (announcement) return t("now.sound_announcement", { name: announcement, file });
  return t(meme ? "now.sound_meme" : "now.sound_other", { file });
}
function applySoundLine(d) {
  const line = document.getElementById("npSound");
  const last = d.last_sound;
  const playing = !!d.sound;
  const recent = last && typeof last.age === "number" && last.age * 1000 < SOUND_LINE_MIN_MS;
  clearTimeout(soundClearTimer);
  if (!playing && !recent) {
    line.classList.add("is-empty");
    return;
  }
  line.textContent = playing
    ? soundText(d.sound, d.sound_announcement, d.mode === "meme")
    : soundText(last.file, last.announcement, last.meme);
  line.classList.remove("is-empty");
  if (!playing) {
    soundClearTimer = setTimeout(() => line.classList.add("is-empty"),
                                 SOUND_LINE_MIN_MS - last.age * 1000);
  }
}

function paintPlayerSmallRow() {
  // One row for the two small buttons, and it exists only while one of them
  // does: an empty one would leave a gap under the buttons above it.
  const row = document.getElementById("playerSmallRow");
  const shown = ["btnListen", "btnSkipVote"].some((id) => {
    const button = document.getElementById(id);
    return button && !button.hidden;
  });
  row.hidden = !shown;
}

function paintSkipVote(vote) {
  const btn = document.getElementById("btnSkipVote");
  btn.hidden = !vote;
  paintPlayerSmallRow();
  if (!vote) return;
  btn.textContent = t(vote.mine ? "vote.voted" : "vote.skip", { votes: vote.votes, needed: vote.needed });
  btn.disabled = !!vote.mine;
  btn.setAttribute("aria-pressed", vote.mine ? "true" : "false");
}

async function refreshStatus() {
  const seq = ++statusRequestSeq;

  const sentAt = Date.now();
  const result = await apiGet("/api/status");
  const sampledAt = (sentAt + Date.now()) / 2;
  if (!result.ok) {
    setConnDot(false);
    return;
  }
  setConnDot(true);
  if (seq < statusPaintedSeq) return;
  statusPaintedSeq = seq;
  const d = result.data;
  d.sampledAt = sampledAt;
  setGoingDown(d.going_down || (d.updating ? "update" : null), sentAt);

  document.getElementById("npMode").textContent = d.mode === "cast" && d.cast && d.cast.origin === "station"
    ? t("mode.station") : modeLabel(d.mode);

  const fileTitle = (d.current_track || "").replace(/\.[a-z0-9]{2,5}$/i, "");
  setNowPlayingText(d.track_title || fileTitle || "\u2014", d.track_artist || "");
  applySoundLine(d);
  applyTrackProgress(d);
  applyTrackMedia(d);
  paintNavNow(d, d.track_title || fileTitle);

  const playingKey = d.mode === "music" ? (d.track_key || null) : null;
  if (playingKey !== recentPlayingKey) {
    recentPlayingKey = playingKey;
    refreshRecent();
    refreshUpnext();
    paintLikeButton();
  }

  const nextSignature = JSON.stringify(d.next_track || null);
  if (nextSignature !== upnextSignature) {
    upnextSignature = nextSignature;
    refreshUpnext();
  }

  if (volumePending && (Math.abs(Number(d.volume) - volumePending.value) <= 1 ||
                        Date.now() > volumePending.until)) {
    volumePending = null;
  }
  if (!userIsDraggingVolume && !volumePending) {
    const slider = document.getElementById("volumeSlider");
    slider.value = Math.round(d.volume);
    setVolumeFill(slider);
    document.getElementById("volumeValue").textContent = Math.round(d.volume);
    refreshAnnounceVolumeNotes();
  }

  // A guest has one button in that slot either way.
  const guest = document.body.dataset.access === "guest";
  document.getElementById("btnStart").hidden = d.mode !== "idle" && d.mode !== "stopped";
  document.getElementById("btnPause").hidden = guest && (d.mode === "idle" || d.mode === "stopped");
  document.getElementById("btnSingle").disabled = !["music", "idle", "stopped"].includes(d.mode);
  document.getElementById("btnDouble").disabled = d.mode !== "music";
  // The radio refuses them while a blind test plays.
  document.getElementById("btnPause").disabled = d.mode === "game";
  document.getElementById("btnNext").disabled = d.mode === "game";
  const inGame = d.mode === "game";
  const frame = document.querySelector(".card-player .now-playing");
  if (frame) {
    frame.classList.toggle("is-game", inGame);
    [...frame.children].forEach((child) => { if (child.id !== "gameCover") child.inert = inGame; });
  }
  document.getElementById("gameCover").hidden = !inGame;

  const clockBox = document.getElementById("clockStatus");

  if (d.has_rtc) {
    clockBox.textContent = t("clock.rtc_ok");
    clockBox.dataset.state = "ok";
  } else if (d.clock_ready) {
    clockBox.textContent = t("clock.rtc_fallback");
    clockBox.dataset.state = "warn";
  } else {
    clockBox.textContent = t("clock.rtc_waiting");
    clockBox.dataset.state = "pending";
  }

  syncPiClock(d);

  const badge = document.getElementById("speakerBadge");
  badge.textContent = d.speaker_connected ? t("speaker.connected")
    : d.speaker_mac && d.speaker_paired === false ? t("speaker.not_paired") : t("speaker.not_connected");
  badge.classList.toggle("connected", d.speaker_connected);
  document.getElementById("speakerMacDisplay").textContent = d.speaker_mac || "—";
  paintSkipVote(d.skip_vote);
  paintLastCard(d.last_card);
  const level = d.speaker_connected && typeof d.speaker_battery === "number" ? d.speaker_battery : null;
  ["speakerBattery", "npBattery"].forEach((id) => paintBattery(document.getElementById(id), level,
    d.speaker_battery_low));

  // Connected, the button takes the link back: that is what cures a silent speaker.
  const connectBtn = document.getElementById("btnSpeakerConnect");
  const connectKey = d.speaker_connected === true ? "speaker.reconnect" : "speaker.connect";
  connectBtn.dataset.i18n = connectKey;
  connectBtn.textContent = t(connectKey);

  lastKnownSpeakerMac = d.speaker_mac || "";

  const audioLine = document.getElementById("audioOutputLine");
  const output = d.audio_output || {};

  const broken = output.server === false || (!output.sink && !output.missing);
  if (output.missing) {
    audioLine.textContent = t("audioout.missing_" + output.output);
    audioLine.classList.add("warning");
  } else if (output.sink) {
    audioLine.textContent = t("audio.output_name", { name: output.sink });
    audioLine.classList.remove("warning");
  } else if (output.server === false) {
    audioLine.textContent = t("audio.no_server");
    audioLine.classList.add("warning");
  } else {
    audioLine.textContent = t("audio.no_sink");
    audioLine.classList.add("warning");
  }

  document.getElementById("audioRepairRow").hidden = !broken;

  document.getElementById("sshToggle").checked = !!d.ssh_active;
  if (d.restart_target && d.restart_target !== restartTarget) {
    restartTarget = d.restart_target;
    paintRestartAfterSong();
  }
  if (restartPending !== !!d.restart_pending || restartDirect !== !!d.restart_direct) {
    restartPending = !!d.restart_pending;
    restartDirect = !!d.restart_direct;
    paintRestartAfterSong();
  }
  // Struck through, not removed: the setting keeps its value.
  const inert = document.querySelector('#musicStartMode option[value="bluetooth"]');
  if (inert) inert.classList.toggle("option-inert", !!d.music_start_mode_inert);
  // A click's sentence cannot be prepared ahead: said once, so a slow synthesis
  // does not look like a dead button.
  const preparing = !!d.speech_preparing;
  if (preparing !== speechPreparing) {
    speechPreparing = preparing;
    if (preparing) toast(t("speech.preparing"), "info");
  }

  const flicBadge = document.getElementById("flicBadge");
  flicBadge.textContent = d.flic_active ? t("system.flic_active") : t("system.flic_not_used");
  flicBadge.classList.toggle("connected", d.flic_active);
}

async function watchStatusChanges() {
  const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  let since = -1;
  for (;;) {
    const askedAt = Date.now();
    let answer = null;
    try {
      const res = await fetch("/api/status/wait?since=" + since, { cache: "no-store" });
      if (res.ok) answer = await res.json();
    } catch (e) {  }
    if (answer && answer.ok && answer.data && answer.data.going_down) {
      setGoingDown(answer.data.going_down);
      await pause(5000);
      continue;
    }
    const version = answer && answer.ok && answer.data ? answer.data.version : undefined;

    if (typeof version !== "number") {
      await pause(5000);
      continue;
    }
    if (version !== since) {
      if (since !== -1) refreshStatus();
      since = version;
    } else if (Date.now() - askedAt < 250) {
      await pause(2000);
    }
  }
}
watchStatusChanges();

let trackClock = null;

function formatClock(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = h > 0 ? String(m).padStart(2, "0") : String(m);
  return (h > 0 ? h + ":" : "") + mm + ":" + String(s).padStart(2, "0");
}

function applyTrackProgress(d) {
  trackClock = {
    mode: d.mode,
    position: Number(d.position) || 0,
    duration: Number(d.duration) || 0,
    paused: !!d.paused,
    at: d.sampledAt || Date.now(),
  };
  paintTrackProgress();

  const btn = document.getElementById("btnPause");
  const skippable = d.mode === "meme" || /^(custom:|button_announce:)/.test(d.mode || "");
  const listening = d.mode === "music" || d.mode === "cast";
  const playing = listening && !d.paused;
  btn.dataset.action = skippable ? "skip" : "pause";
  btn.dataset.icon = skippable ? "skip" : (playing ? "pause" : "play");
  btn.dataset.i18n = skippable ? "home.skip"
    : (playing ? "home.pause" : (listening ? "home.resume" : "home.play"));
  btn.textContent = t(btn.dataset.i18n);
  applyLoopAndTimers(d);
  applyNowMeta(d);
  applyNotices(d);
  applyGuestCredits(d);
  applyCapabilities(d);
  applyListen(d);
  applyMute(d);
  applyFallback(d);
  applyUsbMusic(d);
}

/* The daemon mounts the key and switches the library: each action is one command. */
let usbMusicPainted = "";

function applyUsbMusic(d) {
  const state = d.usb_music || null;
  const signature = JSON.stringify(state);
  if (signature === usbMusicPainted) return;
  usbMusicPainted = signature;
  paintUsbMusic(state);
}

function usbDeviceLabel(entry) {
  // The volume label is the name Windows writes when you rename a disk; a
  // device that has none is called by its model rather than by /dev/sdb1.
  return entry.label || entry.model || entry.device || "";
}

function paintUsbMusic(state) {
  const list = document.getElementById("usbList");
  const now = document.getElementById("usbNow");
  const hint = document.getElementById("usbHint");
  if (!list || !now) return;
  list.replaceChildren();
  now.textContent = "";
  hint.textContent = "";
  if (!state) return;

  now.textContent = state.active
    ? t("usb.playing_from", { label: usbDeviceLabel(state), tracks: state.tracks || 0 })
    : t("usb.internal", { dir: state.internal || "" });
  if (state.error) hint.textContent = errorLabel(state.error);
  else if (state.port_mode && state.port_mode !== "host") {
    hint.textContent = t("usb.port_mode", { mode: t("system.usb_" + state.port_mode) });
  } else if (state.playing_from_other) hint.textContent = t("usb.other_playing");

  paintUsbSpace(state);
  (state.devices || []).forEach((entry) => list.append(usbRow(state, entry)));
  const remembered = state.remembered;
  const plugged = (state.devices || []).some((one) => one.remembered);
  if (remembered && !plugged) {
    list.append(usbRow(state, { key: remembered.key, label: remembered.label,
                                model: remembered.model, device: "",
                                size: "", fstype: "", unplugged: true }));
  }
  if (!list.children.length) hint.textContent = t("usb.no_devices");
}

const USB_LOW_SPACE = 500e6;

function paintUsbSpace(state) {
  const box = document.getElementById("usbSpace");
  const bar = document.getElementById("usbSpaceBar");
  const fill = document.getElementById("usbSpaceFill");
  const text = document.getElementById("usbSpaceText");
  if (!box || !bar || !fill || !text) return;
  const space = state.space || null;
  if (!space || !space.total) {
    box.hidden = true;
    return;
  }
  const percent = Math.min(100, Math.max(0, Math.round(Number(space.percent) || 0)));
  box.hidden = false;
  fill.style.width = percent + "%";
  // The health card's own "nearly full" rule, so the two never disagree.
  bar.classList.toggle("warn", space.free < Math.max(USB_LOW_SPACE, space.total * 0.05));
  bar.setAttribute("aria-label", t("usb.space_aria", { percent: percent,
                                                       free: formatBytes(space.free),
                                                       total: formatBytes(space.total) }));
  text.textContent = t("usb.space_line", { used: formatBytes(space.used),
                                           total: formatBytes(space.total),
                                           free: formatBytes(space.free) });
}

function usbRow(state, entry) {
  const row = document.createElement("li");
  row.className = "usb-row";
  const name = document.createElement("span");
  name.className = "usb-name";
  name.textContent = entry.unplugged
    ? t("usb.unplugged", { label: usbDeviceLabel(entry) })
    : usbDeviceLabel(entry);
  const meta = document.createElement("span");
  meta.className = "usb-meta";
  meta.textContent = [entry.fstype, entry.size,
                      entry.active ? t("usb.in_use")
                        : (entry.remembered ? t("usb.remembered") : "")]
    .filter(Boolean).join(" · ");
  const actions = document.createElement("span");
  actions.className = "device-actions";
  if (entry.active) {
    // The song playing comes from the folder in use before the switch, which left it alone.
    if (state.playing_from_other) {
      const now = document.createElement("button");
      now.type = "button";
      now.className = "btn btn-small btn-primary";
      now.dataset.icon = "play";
      now.textContent = t("usb.now");
      now.addEventListener("click", () => usbMusicPost({ key: entry.key, switch: "now" }));
      actions.append(now);
    }
    const stop = document.createElement("button");
    stop.type = "button";
    stop.className = "btn btn-small";
    stop.dataset.icon = "restart";
    stop.textContent = t("usb.stop");
    stop.addEventListener("click", () => usbMusicPost({ forget: true }));
    actions.append(stop);
  } else if (!entry.unplugged) {
    const use = document.createElement("button");
    use.type = "button";
    use.className = "btn btn-small btn-primary";
    use.dataset.icon = "check";
    use.textContent = t("usb.use");
    use.addEventListener("click", () => usbMusicPost({ key: entry.key }));
    actions.append(use);
  }
  if (entry.unplugged) {
    const forget = document.createElement("button");
    forget.type = "button";
    forget.className = "btn btn-small btn-danger-outline";
    forget.dataset.icon = "trash";
    forget.textContent = t("usb.forget");
    forget.addEventListener("click", () => usbMusicPost({ forget: true }));
    actions.append(forget);
  }
  row.append(name, meta, actions);
  return row;
}

async function usbMusicPost(body) {
  const result = await apiPost("/api/usb_music", body);
  if (!result.ok) {
    showToast(t("common.failed"), errorLabel(result.error), { error: true });
    return;
  }
  usbMusicPainted = JSON.stringify(result.data);
  paintUsbMusic(result.data);
}

let guestQuota = null;

function applyMute(d) {
  const btn = document.getElementById("btnMute");
  const muted = !!d.muted;
  btn.setAttribute("aria-pressed", muted ? "true" : "false");
  btn.dataset.icon = muted ? "volume-x" : "volume-low";
  const key = muted ? "home.unmute" : "home.mute";
  btn.dataset.i18nAriaLabel = key;
  btn.setAttribute("aria-label", t(key));
  btn.dataset.i18nTitle = key;
  btn.title = t(key);
  btn.closest(".volume-row").classList.toggle("is-muted", muted);
}
document.getElementById("btnMute").addEventListener("click", async () => {
  if (document.body.dataset.access === "guest") return;
  const r = await apiPost("/api/mute", { on: "toggle" });
  if (!r.ok) showToolError(t("common.failed"), r);
  else applyMute({ muted: r.data && r.data.muted });
});

function applyGuestCredits(d) {
  const line = document.getElementById("guestCredits");
  const q = d.quota;
  guestQuota = q || null;
  guestLocked = Array.isArray(d.guest_locked) ? d.guest_locked : [];
  dedicationsOn = !!d.dedications;
  dedicationsVoice = !!d.dedications_voice;
  const pagesOff = Array.isArray(d.guest_pages_off) ? d.guest_pages_off : [];
  if (pagesOff.join() !== guestPagesOff.join()) {
    guestPagesOff = pagesOff;
    refreshPageMenus();
    if (pagesOff.includes(document.body.dataset.page)) setActiveView("home", null);
  }
  line.hidden = !q;
  paintCosts();
  if (!q) return;
  line.textContent = t("quota.line", { n: q.tokens, max: q.max });
  line.toggleAttribute("data-low", q.tokens < 2);
}

let guestLocked = [];
let dedicationsOn = false;
let dedicationsVoice = false;

function applyCapabilities(d) {
  const caps = d.capabilities || null;
  const before = capabilities ? JSON.stringify(capabilities) : "";
  capabilities = caps;
  const on = Object.keys(caps || {}).filter((name) => caps[name] !== false && name !== "platform");
  document.body.setAttribute("data-caps", on.join(" "));
  // Only ever hides: an element hidden for its own reason (a file input) must stay
  // hidden when the capability is there.
  document.querySelectorAll("[data-needs]").forEach((el) => {
    if (!el.classList.contains("card") && !canDo(el.dataset.needs)) el.hidden = true;
  });
  if (JSON.stringify(caps) === before) return;
  updateStartTimeVisibility();
  if (document.body.dataset.page && !pageIsAvailable(
        cardOfPage(document.body.dataset.tab, document.body.dataset.page))) {
    setActiveView(document.body.dataset.tab, null, { hash: false });
  }
  refreshPageMenus();
}

function paintCost(el) {
  const locked = guestLocked.includes(el.dataset.costAction);
  el.toggleAttribute("data-locked", locked);
  if (locked) {
    el.setAttribute("aria-disabled", "true");
    el.setAttribute("aria-description", t("guestlock.badge"));
    el.removeAttribute("data-cost");
    el.removeAttribute("data-cost-short");
    return;
  }
  el.removeAttribute("aria-disabled");
  const q = guestQuota;
  const cost = q && q.costs ? Number(q.costs[el.dataset.costAction]) || 0 : 0;
  if (!cost) {
    el.removeAttribute("data-cost");
    el.removeAttribute("data-cost-short");
    el.removeAttribute("aria-description");
    return;
  }
  el.dataset.cost = String(cost);
  el.toggleAttribute("data-cost-short", cost > q.tokens);
  el.setAttribute("aria-description", t("quota.cost_badge", { n: cost }));
}
function paintCosts() {
  document.querySelectorAll("[data-cost-action]").forEach(paintCost);
}

let fallbackAsked = 0;
let fallbackSignature = "";
async function applyFallback(d) {
  const box = document.getElementById("npFallback");
  const ao = d.audio_output || {};
  const planned = ao.output || "bluetooth";
  const missing = ao.missing || (planned === "bluetooth" && d.speaker_mac && d.speaker_connected === false);
  if (!missing && !d.output_override) {
    box.hidden = true;
    fallbackSignature = "";
    return;
  }
  if (Date.now() - fallbackAsked < 15000 && fallbackSignature) return;
  fallbackAsked = Date.now();
  const r = await apiGet("/api/audio/fallback");
  if (!r.ok || !r.data) return;
  const state = r.data;
  const buttons = [];
  if (state.override) {
    const back = document.createElement("button");
    back.type = "button";
    back.className = "btn btn-small";
    back.dataset.icon = "restart";
    back.textContent = t("fallback.back", { output: t("audioout." + state.planned) });
    back.addEventListener("click", () => chooseFallback(null));
    buttons.push(back);
  } else if (state.missing) {
    state.outputs.forEach((kind) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn btn-small";
      btn.dataset.icon = "speaker";
      btn.dataset.costAction = "output";
      paintCost(btn);
      btn.textContent = t("fallback.use", { output: t("audioout." + kind) });
      btn.addEventListener("click", () => chooseFallback(kind));
      buttons.push(btn);
    });
  }
  const signature = JSON.stringify([state.override, state.missing, state.outputs]);
  if (signature !== fallbackSignature) {
    fallbackSignature = signature;
    box.replaceChildren(...buttons);
  }
  box.hidden = !buttons.length;
}

async function chooseFallback(kind) {
  const r = await apiPost("/api/audio/fallback", { output: kind });
  if (!r.ok) showToolError(t("common.failed"), r);
  fallbackSignature = "";
  fallbackAsked = 0;
  refreshStatus();
}

function applyNowMeta(d) {
  const next = document.getElementById("npNext");
  if (d.next_track) {
    const n = d.next_track;
    const label = n.title ? n.title + (n.artist ? " \u2014 " + n.artist : "") : n.name;
    next.textContent = t("np.next", { t: label });
    next.title = label;
  } else {
    next.textContent = d.mode === "music" && !d.music_loop ? t("np.next_end") : "";
    next.title = "";
  }
}

function piMinutes(d) {
  const m = /\s(\d{2}):(\d{2})/.exec(d.system_time || "");
  return m ? Number(m[1]) * 60 + Number(m[2]) : null;
}
const pad2 = (n) => String(n).padStart(2, "0");

let noticesSignature = "";
function controllerKindLabel(kind, address) {
  if (kind === "usb") return t("btctl.kind_usb");
  if (kind === "builtin") return t("btctl.kind_builtin");
  return address || "";
}

function applyNotices(d) {
  const items = [];
  const add = (level, icon, key, vars) => items.push({ level, icon, text: t(key, vars) });
  if (d.mode === "idle") {
    const mode = d.music_start_mode;
    if (mode === "scheduled" && d.music_start_time) {
      add("info", "clock", d.music_started_today ? "notice.start_tomorrow" : "notice.start_scheduled",
          { time: d.music_start_time });
    } else if (mode === "bluetooth" && !d.speaker_connected && !d.music_start_mode_inert) {
      add("info", "bluetooth", "notice.start_bluetooth");
    } else if (mode === "bluetooth" && d.music_start_mode_inert) {
      // The chosen mode waits for a speaker the sound does not even go to: the
      // radio starts at boot instead, and the option says so where it is chosen.
      add("info", "play", "notice.start_action");
    } else {
      add("info", "play", "notice.start_action");
    }
  } else if (d.mode === "stopped") {
    add("info", "play", "notice.stopped");
  }
  // A party says itself in the timers' line, under the player.
  if (d.schedule && !d.schedule.party) {
    add("info", "calendar", "notice.schedule", { name: d.schedule.name, time: d.schedule.until });
  } else if (["idle", "stopped"].includes(d.mode) && d.schedule_next) {
    add("info", "calendar", "notice.schedule_next",
        { name: d.schedule_next.name, when: scheduleNextWhen(d) });
  }
  paintScheduleRunning(d.schedule ? d.schedule.id : null);
  if (d.restart_pending) add("info", "restart", "notice.restart");
  const now = piMinutes(d);
  if (now !== null && d.cutoff_enabled !== false && typeof d.cutoff_hour === "number"
      && !["shutting_down"].includes(d.mode)) {
    const cutoff = d.cutoff_hour * 60 + (d.cutoff_minute || 0);
    const inMin = (cutoff - now + 1440) % 1440;
    if (inMin > 0 && inMin <= 60) {
      add("info", "power", "notice.cutoff_soon", { time: pad2(d.cutoff_hour) + ":" + pad2(d.cutoff_minute || 0) });
    }
  }
  const ao = d.audio_output || {};
  if (ao.missing) {
    add("warn", "speaker", "notice.output_missing", { output: t("audioout." + (ao.output || "usb")) });
  } else if (ao.server === false) {
    add("warn", "alert", "notice.no_audio_server");
  } else if (!d.output_override && (!ao.output || ao.output === "bluetooth")) {
    if (!d.speaker_mac) add("warn", "bluetooth", "notice.no_speaker");
    else if (d.speaker_paired === false) add("warn", "bluetooth", "notice.speaker_unpaired");
    else if (d.speaker_connected === false) add("warn", "bluetooth", "notice.speaker_off");
  }
  // Only someone of the house can go and press it: a guest is told nothing.
  if (d.flic_waiting && document.body.dataset.access !== "guest") add("warn", "bluetooth", "now.flic_waiting");
  if (d.speaker_connected && typeof d.speaker_battery === "number" && d.speaker_battery_low > 0
      && d.speaker_battery <= d.speaker_battery_low) {
    add("warn", "battery", "notice.speaker_battery_low", { n: d.speaker_battery });
  }
  if (d.speaker_connected && d.speaker_controller && d.speaker_expected &&
      d.speaker_controller !== d.speaker_expected) {
    add("warn", "bluetooth", "notice.speaker_other_controller", {
      found: controllerKindLabel(d.speaker_controller_kind, d.speaker_controller),
      expected: controllerKindLabel(d.speaker_expected_kind, d.speaker_expected),
    });
  }
  if (d.output_override) add("info", "speaker", "notice.override", { output: t("audioout." + d.output_override) });
  if (typeof d.volume_cap === "number") add("info", "volume-low", "notice.volume_cap", { n: d.volume_cap });
  // The daily curve, multiplying or deciding the volume: a hand that finds the
  // controls refused is told why.
  if (d.volume_forced) add("info", "volume-low", "notice.volume_forced");
  else if (typeof d.volume_curve === "number") {
    add("info", "volume-low", "notice.volume_curve", { n: Math.round(d.volume_curve) });
  }
  if (d.clock_ready === false) add("warn", "clock", "notice.clock");
  if (d.track_count === 0) add("warn", "music", "notice.no_tracks");
  if ((d.consecutive_play_errors || 0) >= 3) add("warn", "alert", "notice.errors", { n: d.consecutive_play_errors });

  const signature = JSON.stringify(items);
  if (signature === noticesSignature) return;
  noticesSignature = signature;
  const list = document.getElementById("npNotices");
  list.replaceChildren(...items.map((item) => {
    const li = document.createElement("li");
    li.dataset.level = item.level;
    li.dataset.icon = item.icon;
    li.textContent = item.text;
    return li;
  }));
  list.hidden = !items.length;
}
window.LANG_CHANGE_LISTENERS.push(() => {
  noticesSignature = "";
  if (playerStatus) {
    applyNowMeta(playerStatus);
    applyNotices(playerStatus);
  }
});

let playerStatus = null;

let timerEnds = { resume: null, sleep: null };
let timerTick = null;

/* Previous follows the daemon's own threshold (previous_restart_sec) without waiting for a status. */
let previousAction = null;

function paintPrevious() {
  const btn = document.getElementById("btnPrev");
  const position = currentTrackPosition();
  const threshold = (playerStatus && playerStatus.previous_restart_sec) || 5;
  const action = position !== null && position > threshold ? "restart" : "previous";
  if (action === previousAction) return;
  previousAction = action;
  btn.dataset.icon = action === "restart" ? "restart" : "prev";
  btn.dataset.i18n = action === "restart" ? "home.restart_track" : "home.previous";
  btn.textContent = t(btn.dataset.i18n);
}

function applyLoopAndTimers(d) {
  playerStatus = d;
  const loop = ["track", "album"].includes(d.loop_mode) ? d.loop_mode : "off";
  const btn = document.getElementById("btnLoop");
  btn.dataset.loop = loop;
  btn.setAttribute("aria-pressed", loop === "off" ? "false" : "true");
  const label = btn.querySelector("span");
  label.dataset.i18n = "home.loop_" + loop;
  label.textContent = t(label.dataset.i18n);
  document.getElementById("btnPrev").disabled = d.mode !== "music";
  paintPrevious();

  const timers = d.timers || {};
  const at = d.sampledAt || Date.now();
  ["resume", "sleep"].forEach((name) => {
    const left = timers[name];
    timerEnds[name] = typeof left === "number" ? at + left * 1000 : null;
  });
  paintTimers();
  const running = timerEnds.resume || timerEnds.sleep;
  if (running && !timerTick) timerTick = setInterval(paintTimers, 1000);
  if (!running && timerTick) {
    clearInterval(timerTick);
    timerTick = null;
  }
}

function formatLeft(ms) {
  const s = Math.max(0, Math.round(ms / 1000));
  return s >= 60 ? Math.ceil(s / 60) + " min" : s + " s";
}

function paintTimers() {
  const now = Date.now();
  const parts = [];
  if (timerEnds.resume) parts.push(t("timer.resume_in", { t: formatLeft(timerEnds.resume - now) }));
  if (timerEnds.sleep) parts.push(t("timer.sleep_in", { t: formatLeft(timerEnds.sleep - now) }));
  if (playerStatus && playerStatus.sleep_at_end) parts.push(t("timer.sleep_at_end"));
  if (playerStatus && playerStatus.party_at_end) parts.push(t("timer.party_at_end"));
  const party = playerStatus && playerStatus.schedule && playerStatus.schedule.party ? playerStatus.schedule : null;
  if (party && !playerStatus.party_at_end) {
    parts.push(party.until ? t("timer.party_until", { time: party.until }) : t("timer.party_on"));
  }
  const line = document.getElementById("npTimers");
  line.textContent = parts.join(" \u00b7 ");
  line.hidden = !parts.length;
}

document.getElementById("btnPrev").addEventListener("click", async () => {
  const btn = document.getElementById("btnPrev");
  btn.disabled = true;
  const result = await apiPost("/api/action/previous_track");
  btn.disabled = false;
  if (!result.ok) showToolError(t("home.transport_failed"), result);
  refreshStatus();
});

document.getElementById("btnLoop").addEventListener("click", async () => {
  const result = await apiPost("/api/loop", { mode: "cycle" });
  if (!result.ok) showToolError(t("home.transport_failed"), result);
  refreshStatus();
});

function timerSymbol(name, small) {
  const mark = document.createElement("span");
  mark.className = "timer-symbol" + (small ? " timer-symbol-small" : "");
  mark.dataset.icon = name;
  mark.setAttribute("aria-hidden", "true");
  return mark;
}

function legendLines(pairs) {
  const legend = document.createElement("div");
  legend.className = "timer-legend";
  pairs.forEach(([icon, text]) => {
    const line = document.createElement("p");
    line.className = "timer-legend-line";
    const label = document.createElement("span");
    label.textContent = text;
    line.append(timerSymbol(icon, true), label);
    legend.appendChild(line);
  });
  return legend;
}

/* Icon buttons that read as the timer's own heads: a round icon, the name of
   what it leaves behind, and no frame. The two dialogs below share them. */
function choiceButtons(choices) {
  const row = document.createElement("div");
  row.className = "choice-row";
  choices.forEach(([icon, value, key]) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "timer-group-head choice-btn";
    const label = document.createElement("span");
    label.className = "timer-group-title";
    label.textContent = t(key);
    btn.append(timerSymbol(icon, false), label);
    btn.addEventListener("click", () => closeModal(value));
    row.appendChild(btn);
  });
  return row;
}

/* What the two ways of stopping the music do, under the buttons that do them:
   the same legend as the timer's, with the icon each action carries. */
function stopChoiceBody() {
  return legendLines([["moon", t("home.legend_standby")],
                      ["power", t("home.legend_poweroff")]]);
}

function timerChoiceBody(groups, intro, after) {
  const wrap = document.createElement("div");
  wrap.className = "timer-groups";

  groups.forEach((group) => {
    const box = document.createElement("div");
    box.className = "timer-group" + (group.wide ? " timer-group-wide" : "");
    // The head is a button: it applies the shortest delay of its own group.
    const head = document.createElement("button");
    head.type = "button";
    head.className = "timer-group-head";
    const title = document.createElement("span");
    title.className = "timer-group-title";
    title.textContent = group.title;
    head.append(timerSymbol(group.icon, false), title);
    if (group.options.length) {
      head.addEventListener("click", () => closeModal(group.options[0].value));
    }
    box.appendChild(head);
    group.options.forEach((option) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn timer-option" + (option.quiet ? " timer-option-cancel" : "");
      btn.textContent = option.label;
      btn.addEventListener("click", () => closeModal(option.value));
      box.appendChild(btn);
    });
    wrap.appendChild(box);
  });

  if (after) wrap.appendChild(after);
  // Drawn icons, not the unicode pause/moon: many phone fonts have no glyph.
  wrap.appendChild(legendLines(groups.map((group) => [group.icon, group.legend])));

  if (intro) {
    const line = document.createElement("p");
    line.className = "timer-intro";
    line.textContent = intro;
    wrap.prepend(line);
  }
  return wrap;
}

/* A browser cannot tell why a stream is silent: the reason comes from
   src/stream.py's why_unavailable(). */
let listenAudio = null;
let listenUrl = "";
let listenWhy = "";

// From the tap until a moment after the sound starts, the button says the stream runs behind.
const LISTEN_DELAY_SHOWN_MS = 2500;
let listenStarting = false;
let listenStartTimer = null;

function listenStarted(done) {
  clearTimeout(listenStartTimer);
  listenStartTimer = null;
  listenStarting = !done;
}

function paintListen(playing) {
  const btn = document.getElementById("btnListen");
  const tell = document.getElementById("listenWhy");
  const label = btn && btn.querySelector("span");
  if (!btn || !tell || !label) return;
  const on = playing !== undefined ? playing : !!(listenAudio && !listenAudio.paused);
  btn.setAttribute("aria-pressed", on ? "true" : "false");
  const key = !on ? "stream.listen" : listenStarting ? "stream.delay" : "stream.stop";
  btn.dataset.i18n = key;
  label.dataset.i18n = key;
  label.textContent = t(key);
  const whyKey = on || !listenWhy ? "" : "stream.why_" + listenWhy;
  tell.dataset.i18n = whyKey;
  tell.textContent = whyKey ? t(whyKey) : "";
  tell.hidden = !whyKey;
  paintPlayerSmallRow();
}

function applyListen(d) {
  const btn = document.getElementById("btnListen");
  const tell = document.getElementById("listenWhy");
  // Both, not just the button: a status can arrive before the line below the
  // player is parsed, and paintListen() reads it.
  if (!btn || !tell) return;
  const stream = d.stream || {};
  // No stream field (an older daemon): leave the page as it is.
  if (!("enabled" in stream)) return;
  const available = !!stream.available && !!stream.url;
  listenUrl = stream.url || "";
  // Switched off needs no line; the other causes do.
  listenWhy = available || stream.why === "off" ? "" : (stream.why || "no_source");
  btn.hidden = !available;
  if (!available) {
    // Nothing to offer: the button goes, and the line beside the player says
    // what is missing - it is the only place the reason can be read.
    paintListen(false);
    return;
  }
  btn.dataset.i18nTitle = "stream.listen_hint";
  btn.title = t("stream.listen_hint");
  paintListen();
}

document.getElementById("btnListen").addEventListener("click", async () => {
  if (listenAudio) {
    listenAudio.pause();
    listenAudio.src = "";
    listenAudio = null;
    listenStarted(true);
    paintListen(false);
    return;
  }
  if (!listenUrl) return;
  listenAudio = new Audio(listenUrl);
  listenStarted(false);
  paintListen(true);
  listenAudio.addEventListener("playing", () => {
    if (!listenStartTimer && listenStarting) {
      listenStartTimer = setTimeout(() => {
        listenStarted(true);
        paintListen(!!listenAudio);
      }, LISTEN_DELAY_SHOWN_MS);
    }
    paintListen(true);
  });
  listenAudio.addEventListener("error", () => {
    showToast(t("stream.failed"));
    listenAudio = null;
    listenStarted(true);
    paintListen(false);
  });
  try {
    await listenAudio.play();
    paintListen(true);
  } catch (error) {
    showToast(t("stream.failed"));
    listenAudio = null;
    listenStarted(true);
    paintListen(false);
  }
});

document.getElementById("btnTimer").addEventListener("click", async () => {
  const d = playerStatus || {};
  const durations = Array.isArray(d.pause_durations) && d.pause_durations.length
    ? d.pause_durations : [5, 15, 30, 60];
  const sleeps = Array.isArray(d.sleep_durations) && d.sleep_durations.length
    ? d.sleep_durations : [30, 60, 90, 120];

  const pauseOptions = durations.map((n) => ({ value: "pause:" + n, label: t("timer.minutes", { n }) }));
  if (timerEnds.resume) {
    pauseOptions.push({ value: "resume", label: t("timer.pause_cancel"), quiet: true });
  }
  const sleepOptions = sleeps.map((n) => ({ value: "sleep:" + n, label: t("timer.minutes", { n }) }));
  if (timerEnds.sleep || d.sleep_at_end) {
    sleepOptions.push({ value: "sleep_off", label: t("timer.sleep_cancel"), quiet: true });
  }
  // A settings row, as in every card: the sleep and a party's end both follow it, and the
  // radio keeps the last choice for the buttons too.
  const finishRow = document.createElement("div");
  finishRow.className = "field-row timer-finish";
  const finishText = document.createElement("div");
  finishText.className = "field-text";
  const finishLabel = document.createElement("label");
  finishLabel.htmlFor = "timerFinishTrack";
  finishLabel.textContent = t("timer.finish_track");
  const finishDesc = document.createElement("p");
  finishDesc.className = "field-desc";
  finishDesc.id = "timerFinishTrackDesc";
  finishDesc.textContent = t("timer.finish_track_desc");
  finishText.append(finishLabel, finishDesc);
  const finish = document.createElement("input");
  finish.type = "checkbox";
  finish.id = "timerFinishTrack";
  finish.className = "switch-input";
  finish.setAttribute("role", "switch");
  finish.setAttribute("aria-describedby", finishDesc.id);
  finish.checked = !!d.finish_track;
  finishRow.append(finishText, finish);

  const groups = [
    { icon: "pause", title: t("timer.group_pause"), options: pauseOptions, legend: t("timer.legend_pause") },
    { icon: "moon", title: t("timer.group_sleep"), options: sleepOptions, legend: t("timer.legend_sleep") },
  ];
  if (d.party_ready && mayCall("/api/party")) {
    const partyOptions = [3, 6].map((n) => ({ value: "party:" + n, label: t("timer.hours", { n }) }));
    partyOptions.push({ value: "party:0", label: t("timer.party_open") });
    if (d.schedule && d.schedule.party) {
      partyOptions.push({ value: "party_off", label: t("timer.party_end"), quiet: true });
    }
    groups.push({ icon: "sparkles", title: t("timer.group_party"), options: partyOptions, wide: true,
                  legend: t("timer.legend_party") });
  }

  const choice = await openModal({
    title: t("timer.title"),
    bodyNode: timerChoiceBody(groups, t("timer.body"), finishRow),
    actions: false,
  });
  if (!choice) return;
  let result;
  if (choice.startsWith("pause:")) {
    result = await apiPost("/api/action/timed_pause", { minutes: Number(choice.slice(6)) });
  } else if (choice.startsWith("sleep:")) {
    result = await apiPost("/api/action/sleep_timer", { on: true, minutes: Number(choice.slice(6)),
                                                         finish_track: finish.checked });
  } else if (choice.startsWith("party:")) {
    result = await apiPost("/api/party", { hours: Number(choice.slice(6)), finish_track: finish.checked });
  } else if (choice === "party_off") {
    result = await apiFetch("/api/party", { method: "DELETE", headers: { "Content-Type": "application/json" },
                                            body: JSON.stringify({ finish_track: finish.checked }) });
  } else if (choice === "resume") {
    result = await apiPost("/api/action/toggle_pause");
  } else {
    result = await apiPost("/api/action/sleep_timer", { on: false });
  }
  if (!result.ok) showToolError(t("home.transport_failed"), result);
  refreshStatus();
});

function currentTrackPosition() {
  if (!trackClock || !trackClock.duration || trackClock.mode !== "music") return null;
  let position = trackClock.position;
  if (!trackClock.paused) position += (Date.now() - trackClock.at) / 1000;
  return Math.min(Math.max(position, 0), trackClock.duration);
}

function paintTrackProgress() {
  const box = document.getElementById("trackProgress");
  const position = currentTrackPosition();
  if (position === null) {
    box.classList.add("is-idle");
    if (!trackClock || ["idle", "stopped"].includes(trackClock.mode)) {
      document.getElementById("trackProgressBar").style.width = "0%";
      document.getElementById("trackPosition").textContent = formatClock(0);
      document.getElementById("trackDuration").textContent = formatClock(0);
    }
    return;
  }
  box.classList.remove("is-idle");

  document.getElementById("trackProgressBar").style.width =
    ((position / trackClock.duration) * 100).toFixed(3) + "%";
  document.getElementById("trackPosition").textContent = formatClock(position);
  document.getElementById("trackDuration").textContent = formatClock(trackClock.duration);
}

const nextFrame = window.requestAnimationFrame
  ? (fn) => window.requestAnimationFrame(fn)
  : (fn) => setTimeout(fn, 100);
function progressFrame() {
  paintTrackProgress();
  paintPrevious();
  paintLyrics(false);
  nextFrame(progressFrame);
}
nextFrame(progressFrame);

const LYRICS_OPEN_KEY = "rukebox_lyrics_open";

const LYRICS_LEAD_SEC = 0.25;

const LYRICS_MANUAL_SCROLL_MS = 5000;

let trackMediaKey = null;
let lyricsData = null;
let lyricsActive = -1;
let lyricsManualUntil = 0;
let lyricsOpen = false;
try { lyricsOpen = localStorage.getItem(LYRICS_OPEN_KEY) === "1"; } catch (e) {  }

/* What plays, at the foot of the side menu: every page but the player's own shows it. */
let navNowKey = null;
function paintNavNow(d, title) {
  const box = document.getElementById("navNow");
  const playing = !!title && !["idle", "stopped", "shutting_down"].includes(d.mode);
  box.hidden = !playing;
  if (!playing) return;
  document.getElementById("navNowTitle").textContent = title;
  document.getElementById("navNowArtist").textContent = d.track_artist || "";
  box.classList.toggle("is-paused", !!d.paused);
  const said = title + (d.track_artist ? " \u2014 " + d.track_artist : "");
  box.setAttribute("aria-label", t("nav.now_playing", { title: said }));
  box.title = said;
  const key = d.track_key || null;
  if (key === navNowKey) return;
  navNowKey = key;
  const img = document.getElementById("navNowCover");
  img.hidden = true;
  if (!key) {
    img.removeAttribute("src");
    return;
  }
  img.onload = () => { if (navNowKey === key) img.hidden = false; };
  img.onerror = () => { img.hidden = true; };
  img.src = "/api/now/cover?k=" + encodeURIComponent(key);
}
document.getElementById("navNow").addEventListener("click", () => {
  setActiveView(DEFAULT_VIEW.tab, DEFAULT_VIEW.page);
});

function applyTrackMedia(d) {
  const key = d.track_key && !["idle", "stopped"].includes(d.mode) ? d.track_key : null;
  if (key === trackMediaKey) return;
  trackMediaKey = key;

  const coverBox = document.getElementById("npCover");
  const img = document.getElementById("npCoverImg");
  const showEmpty = () => {
    img.hidden = true;
    coverBox.classList.add("is-empty");
  };
  if (key) {
    img.onload = () => {
      if (trackMediaKey !== key) return;
      img.hidden = false;
      coverBox.classList.remove("is-empty");
      img.classList.remove("np-enter");
      void img.offsetWidth;
      img.classList.add("np-enter");
    };
    img.onerror = () => { if (trackMediaKey === key) showEmpty(); };
    img.src = "/api/now/cover?k=" + encodeURIComponent(key);
  } else {
    img.removeAttribute("src");
    showEmpty();
  }

  lyricsData = null;
  renderLyrics();
  if (key) loadLyrics(key);
}

async function loadLyrics(key) {
  const result = await apiGet("/api/now/lyrics?k=" + encodeURIComponent(key));

  if (key !== trackMediaKey) return;
  lyricsData = result.ok ? result.data : null;
  renderLyrics();
}

function renderLyrics() {
  const btn = document.getElementById("btnLyrics");
  const panel = document.getElementById("lyricsPanel");
  const list = document.getElementById("lyricsLines");
  const plain = document.getElementById("lyricsPlain");
  const scroll = document.getElementById("lyricsScroll");
  const has = !!lyricsData;
  const open = has && lyricsOpen;
  btn.hidden = !has;
  btn.setAttribute("aria-pressed", open ? "true" : "false");
  btn.classList.toggle("is-on", open);
  panel.hidden = !open;
  if (!open && panel.classList.contains("is-fullscreen")) setLyricsFullscreen(false);
  list.innerHTML = "";
  plain.textContent = "";
  lyricsActive = -1;
  if (!has) return;

  const synced = !!lyricsData.synced;
  document.getElementById("lyricsSource").textContent =
    t(synced ? "lyrics.synced" : "lyrics.plain");
  scroll.classList.toggle("is-plain", !synced);
  list.hidden = !synced;
  plain.hidden = synced;
  if (synced) {
    lyricsData.lines.forEach((line) => {
      const li = document.createElement("li");

      li.textContent = line.text || "♪";
      list.appendChild(li);
    });
    scroll.scrollTop = 0;
    paintLyrics(true);
  } else {
    plain.textContent = lyricsData.text;
  }
}

function paintLyrics(instant) {
  if (!lyricsData || !lyricsData.synced) return;
  const panel = document.getElementById("lyricsPanel");
  if (panel.hidden) return;
  const position = currentTrackPosition();
  if (position === null) return;
  const lines = lyricsData.lines;
  let index = -1;
  for (let i = 0; i < lines.length && lines[i].t <= position + LYRICS_LEAD_SEC; i++) index = i;
  if (index === lyricsActive) return;
  lyricsActive = index;

  const items = document.getElementById("lyricsLines").children;
  for (let i = 0; i < items.length; i++) {
    items[i].classList.toggle("active", i === index);
    items[i].classList.toggle("past", i < index);
  }
  if (index < 0 || Date.now() < lyricsManualUntil) return;

  const scroll = document.getElementById("lyricsScroll");
  const li = items[index];
  const top = li.offsetTop - scroll.clientHeight / 2 + li.offsetHeight / 2;
  const reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  scroll.scrollTo({ top: Math.max(0, top), behavior: instant || reduced ? "auto" : "smooth" });
}

["wheel", "touchstart", "keydown"].forEach((type) => {
  document.getElementById("lyricsScroll").addEventListener(type, () => {
    lyricsManualUntil = Date.now() + LYRICS_MANUAL_SCROLL_MS;
  }, { passive: true });
});

let wakeLock = null;
let wakeVideo = null;

async function keepScreenAwake() {
  try {
    if (navigator.wakeLock && window.isSecureContext) {
      wakeLock = await navigator.wakeLock.request("screen");
      return;
    }
  } catch (e) {  }
  if (!wakeVideo) {
    wakeVideo = document.createElement("video");
    wakeVideo.src = "/keepawake.mp4";
    wakeVideo.muted = true;
    wakeVideo.loop = true;
    wakeVideo.playsInline = true;
    wakeVideo.setAttribute("muted", "");
    wakeVideo.setAttribute("playsinline", "");
    wakeVideo.setAttribute("aria-hidden", "true");
    wakeVideo.className = "keep-awake";
    document.body.appendChild(wakeVideo);
  }
  wakeVideo.play().catch(() => {  });
}

function letScreenSleep() {
  if (wakeLock) {
    wakeLock.release().catch(() => {});
    wakeLock = null;
  }
  if (wakeVideo) wakeVideo.pause();
}

document.addEventListener("visibilitychange", () => {
  if (!document.hidden && document.getElementById("lyricsPanel").classList.contains("is-fullscreen")) {
    keepScreenAwake();
  }
});

function setLyricsFullscreen(on) {
  const panel = document.getElementById("lyricsPanel");
  const btn = document.getElementById("btnLyricsFullscreen");
  panel.classList.toggle("is-fullscreen", on);
  document.body.classList.toggle("lyrics-fullscreen", on);
  btn.dataset.icon = on ? "shrink" : "expand";
  btn.setAttribute("aria-pressed", on ? "true" : "false");
  const key = on ? "lyrics.exit_fullscreen" : "lyrics.fullscreen";
  btn.dataset.i18nAriaLabel = key;
  btn.dataset.i18nTitle = key;
  btn.setAttribute("aria-label", t(key));
  btn.title = t(key);
  document.getElementById("lyricsTrack").textContent =
    on ? document.getElementById("npTrack").textContent : "";
  const root = document.documentElement;
  try {
    if (on && root.requestFullscreen && !document.fullscreenElement) {
      root.requestFullscreen().catch(() => {  });
    } else if (!on && document.fullscreenElement && document.exitFullscreen) {
      document.exitFullscreen().catch(() => {});
    }
  } catch (e) {  }
  if (on) keepScreenAwake(); else letScreenSleep();
  lyricsActive = -1;
  lyricsManualUntil = 0;
  paintLyrics(true);
}

document.getElementById("btnLyricsFullscreen").addEventListener("click", () => {
  setLyricsFullscreen(!document.getElementById("lyricsPanel").classList.contains("is-fullscreen"));
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && document.getElementById("lyricsPanel").classList.contains("is-fullscreen")) {
    setLyricsFullscreen(false);
  }
});

document.addEventListener("fullscreenchange", () => {
  if (!document.fullscreenElement && document.getElementById("lyricsPanel").classList.contains("is-fullscreen")) {
    setLyricsFullscreen(false);
  }
});

document.getElementById("btnLyrics").addEventListener("click", () => {
  lyricsOpen = !lyricsOpen;
  try { localStorage.setItem(LYRICS_OPEN_KEY, lyricsOpen ? "1" : "0"); } catch (e) {  }
  lyricsManualUntil = 0;
  renderLyrics();

  const panel = document.getElementById("lyricsPanel");
  if (lyricsOpen && !panel.hidden) {
    const reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    panel.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" });
  }
});

window.LANG_CHANGE_LISTENERS.push(() => {
  if (lyricsData) {
    document.getElementById("lyricsSource").textContent =
      t(lyricsData.synced ? "lyrics.synced" : "lyrics.plain");
  }
});

document.getElementById("btnPause").addEventListener("click", async () => {
  const btn = document.getElementById("btnPause");
  btn.disabled = true;
  const result = await apiPost(btn.dataset.action === "skip" ? "/api/action/skip_sound" : "/api/action/toggle_pause");
  btn.disabled = false;
  if (!result.ok) showToolError(t("home.transport_failed"), result);
  refreshStatus();
});

document.getElementById("btnNext").addEventListener("click", async () => {
  const btn = document.getElementById("btnNext");
  btn.disabled = true;

  const result = await apiPost("/api/action/next_track");
  btn.disabled = false;
  if (!result.ok) showToolError(t("home.transport_failed"), result);
  refreshStatus();
});

refreshStatus();
refreshEvery(refreshStatus, 4000);

const volumeSlider = document.getElementById("volumeSlider");
let volumeDebounce = null;
setVolumeFill(volumeSlider);

volumeSlider.addEventListener("input", () => {
  userIsDraggingVolume = true;
  volumePending = { value: Number(volumeSlider.value), until: Date.now() + VOLUME_HOLD_MS };
  document.getElementById("volumeValue").textContent = volumeSlider.value;
  setVolumeFill(volumeSlider);
  refreshAnnounceVolumeNotes();
  clearTimeout(volumeDebounce);
  volumeDebounce = setTimeout(() => {
    apiPost("/api/volume", { value: Number(volumeSlider.value) });
  }, 150);
});
volumeSlider.addEventListener("change", () => {
  userIsDraggingVolume = false;
});

document.getElementById("btnSingle").addEventListener("click", () => apiPost("/api/action/single_click"));

document.getElementById("btnDouble").addEventListener("click", async () => {
  const choices = [];
  customAnnouncementsCache.forEach((item) => {
    choices.push({
      value: "custom:" + item.id,
      label: item.name,
    });
  });
  choices.push({ value: "cutoff", label: t("source.cutoff") },
               { value: "meme", label: t("source.meme") });

  const source = await showChoice(t("home.announce_which"), choices,
                                  t("home.announcement"));
  if (!source) return;

  const result = await apiPost("/api/action/announce", { source });
  if (result.ok && result.data && result.data.skipped) {
    showToast(t("chance.not_this_time"), chanceLabel(result.data.chance));
    return;
  }
  if (result.ok) return;
  const messages = {
    not_playing_music: t("home.announce_not_playing"),
    no_announcement: t("home.announce_empty"),
    unknown_source: t("home.announce_unknown"),
  };
  showToast(t("common.failed"), messages[result.error] || errorLabel(result.error), { error: true });
});
document.getElementById("btnStart").addEventListener("click", () => apiPost("/api/action/start_music"));

const STOP_CHOICES = [["moon", "standby", "home.brief_standby"],
                      ["power", "poweroff", "home.brief_poweroff"]];

document.getElementById("btnLongPress").addEventListener("click", async () => {
  // Closing is the cross (Esc, or a tap outside): "off" is the only thing here
  // that cannot be taken back, and neither way of stopping is a mistake to undo.
  const choice = await openModal({
    title: t("home.stop_title"),
    bodyNode: choiceButtons(STOP_CHOICES),
    legend: stopChoiceBody(),
    actions: false,
  });
  if (choice === "standby") {
    const r = await apiPost("/api/action/standby");
    if (!r.ok) showToolError(t("common.failed"), r);
    else showToast(t("home.standby_done"));
  } else if (choice === "poweroff") {
    apiPost("/api/action/poweroff");
  }
});

document.getElementById("btnRescan").addEventListener("click", async () => {
  const r = await apiPost("/api/rescan_music");
  if (r.ok) showToast(t("system.rescan_scheduled")); else showError(r.error);
});

const btClockMac = document.getElementById("btClockMac");

function macDigits(value) {
  return String(value || "").toUpperCase().replace(/[^0-9A-F]/g, "").slice(0, 12);
}

function formatMac(value) {
  return macDigits(value).replace(/(..)(?=.)/g, "$1:");
}

function isCompleteMac(value) {
  return macDigits(value).length === 12;
}

btClockMac.addEventListener("input", () => {
  const raw = btClockMac.value;

  const digits = macDigits(raw);
  const wantedSeparator = /[:\-\s]$/.test(raw) && digits.length % 2 === 0 && digits.length < 12;
  const caret = btClockMac.selectionStart == null ? raw.length : btClockMac.selectionStart;
  const digitsBefore = macDigits(raw.slice(0, caret)).length;

  btClockMac.value = formatMac(raw) + (wantedSeparator ? ":" : "");

  const position = Math.min(digitsBefore + Math.floor(digitsBefore / 2), btClockMac.value.length);
  try {
    btClockMac.setSelectionRange(position, position);
  } catch (e) {
  }
});


// Only the changed fields are sent, so a stale tab cannot overwrite a setting.
let settingsBaseline = {};

const DURATION_MAX_MIN = 600;

