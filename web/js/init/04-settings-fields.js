function durationValues(el) {
  return String(el.value || "").split(/[,;]/)
    .map((part) => part.trim())
    .filter((part) => part.length > 0);
}

function saveDurations(el, values) {
  el.value = values.join(",");
}

function addDuration(el) {
  const input = el.querySelector(".duration-new");
  const typed = input.value.trim();
  if (!typed) return;
  const value = Math.round(Number(typed));
  if (!Number.isFinite(value) || value < 1 || value > DURATION_MAX_MIN) {
    showError("invalid_value");
    return;
  }
  const values = durationValues(el);
  input.value = "";
  if (values.includes(String(value))) {
    showToast(t("settings.duration_exists", { n: value }));
    return;
  }
  values.push(String(value));
  values.sort((a, b) => Number(a) - Number(b));
  saveDurations(el, values);
  showToast(t("settings.duration_added", { n: value }));
  input.focus();
}

// The row stays the height of its neighbours; this is where the values can be read.
function durationList(el) {
  const values = durationValues(el);
  const list = document.createElement("div");
  list.className = "duration-list";
  if (!values.length) {
    const empty = document.createElement("p");
    empty.className = "hint";
    empty.textContent = t("settings.duration_none");
    list.appendChild(empty);
    return list;
  }
  values.forEach((value) => {
    const row = document.createElement("div");
    row.className = "duration-row";
    const label = document.createElement("span");
    label.textContent = t("settings.duration_min", { n: value });
    const off = document.createElement("button");
    off.type = "button";
    // A trash bin, in red: a cross would read as another way out of the dialog.
    off.className = "btn btn-icon duration-row-x btn-danger-outline";
    off.dataset.icon = "trash";
    off.title = t("settings.duration_remove", { n: value });
    off.setAttribute("aria-label", off.title);
    off.disabled = values.length < 2;
    off.addEventListener("click", () => {
      const kept = durationValues(el);
      if (kept.length < 2) return;
      saveDurations(el, kept.filter((one) => one !== value));
      list.replaceWith(durationList(el));
    });
    row.append(label, off);
    list.appendChild(row);
  });
  return list;
}

function openDurationList(el) {
  const label = el.closest(".field-row").querySelector("label");
  return openModal({
    title: label ? label.textContent : t("settings.duration_manage"),
    bodyNode: durationList(el),
    actions: false,
  });
}

function durationField(el) {
  el.value = "";
  const input = document.createElement("input");
  input.type = "number";
  input.className = "num-input duration-new";
  input.id = el.id + "New";
  input.min = "1";
  input.max = String(DURATION_MAX_MIN);
  input.step = "1";
  input.inputMode = "numeric";
  input.placeholder = "15";
  input.autocomplete = "off";

  const plus = document.createElement("button");
  plus.type = "button";
  plus.className = "btn btn-icon duration-add-btn";
  plus.dataset.icon = "plus";
  plus.title = t("common.add");
  plus.setAttribute("aria-label", t("common.add"));
  plus.addEventListener("click", () => addDuration(el));

  const list = document.createElement("button");
  list.type = "button";
  list.className = "btn btn-icon duration-open";
  list.dataset.icon = "list";
  list.title = t("settings.duration_manage");
  list.setAttribute("aria-label", list.title);
  list.addEventListener("click", () => openDurationList(el));

  // `change` fires on leaving the field, so a value typed then left is read before Save.
  input.addEventListener("change", () => addDuration(el));
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();  // otherwise the settings form is submitted
      addDuration(el);
    }
  });
  el.replaceChildren(input, plus, list);
}

document.querySelectorAll(".duration-field").forEach(durationField);

/* One checkbox per codec: in a dialog - the eight names in the row wrapped and doubled its height. */
const BT_CODEC_LABELS = {
  ldac: "LDAC", aptx_hd: "aptX HD", aptx: "aptX", aac: "AAC",
  sbc_xq: "SBC-XQ", sbc: "SBC", faststream: "FastStream", opus: "Opus",
};

// The guest commands that can be locked, named after their prices.
// What a blind test round may ask, in the order the picker offers them.
const GAME_KINDS = ["song", "artist", "year", "genre", "album"];
const GUEST_LOCK_LABELS = {
  next: "quota.cost_next", previous: "quota.cost_previous", start: "quota.cost_start",
  pause: "quota.cost_pause", sound: "quota.cost_sound", announce: "quota.cost_announce",
  volume: "guestlock.volume", queue: "quota.cost_queue", play_now: "quota.cost_play_now",
  output: "quota.cost_output",
};

// The pages a guest can be kept away from, named after their own titles.
const GUEST_PAGE_LABELS = {
  upnext: "upnext.title", recent: "recent.title", today: "today.title",
  library: "library.title", game: "game.title", suggest: "suggest.title",
};

function choiceLabels(el) {
  if (el.dataset.choices === "game_kinds") {
    return Object.fromEntries(GAME_KINDS.map((kind) => [kind, t("game.kind_" + kind)]));
  }
  if (el.dataset.key === "security.guest_pages_off") {
    return Object.fromEntries(Object.entries(GUEST_PAGE_LABELS).map(([k, key]) => [k, t(key)]));
  }
  if (el.dataset.key === "security.guest_locked") {
    return Object.fromEntries(Object.entries(GUEST_LOCK_LABELS).map(([k, key]) => [k, t(key)]));
  }
  return BT_CODEC_LABELS;
}

function codecValues(el) {
  return String(el.value || "").split(/[,;]/)
    .map((part) => part.trim().toLowerCase())
    .filter((part) => part.length > 0);
}

function codecSummary(el) {
  const chosen = codecValues(el);
  const labels = choiceLabels(el);
  // How many, not which: the dialog lists them.
  return chosen.length
    ? t("common.picked_of", { n: chosen.length, total: Object.keys(labels).length })
    : t(el.dataset.empty || "audioout.codecs_none");
}

function paintCodecField(el) {
  const open = el.querySelector(".codec-open");
  if (open) open.textContent = codecSummary(el);
}

function codecList(el) {
  const chosen = codecValues(el);
  const labels = choiceLabels(el);
  // Offering no codec leaves the speaker nothing to agree on; a lock list may be empty.
  const least = el.dataset.min === undefined ? 1 : Number(el.dataset.min);
  const list = document.createElement("div");
  list.className = "codec-list";
  Object.keys(labels).forEach((codec) => {
    const label = document.createElement("label");
    label.className = "codec-box";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.value = codec;
    box.checked = chosen.includes(codec);
    box.disabled = box.checked && chosen.length <= least;
    if (box.disabled) label.classList.add("is-disabled");
    const text = document.createElement("span");
    text.textContent = labels[codec];
    box.addEventListener("change", () => {
      const kept = codecValues(el).filter((one) => one !== codec);
      if (box.checked) kept.push(codec);
      el.value = kept.join(",");
      paintCodecField(el);
      list.replaceWith(codecList(el));
      el.dispatchEvent(new Event("change", { bubbles: true }));
    });
    label.append(box, text);
    list.appendChild(label);
  });
  return list;
}

function openCodecList(el) {
  const row = el.closest(".field-row");
  const label = row ? row.querySelector("label") : null;
  return openModal({
    title: label ? label.textContent : t("audioout.codecs"),
    bodyNode: codecList(el),
    actions: false,
  });
}

function codecField(el) {
  el.value = el.value || "";
  const open = document.createElement("button");
  open.type = "button";
  open.className = "btn codec-open";
  open.addEventListener("click", () => openCodecList(el));
  el.replaceChildren(open);
  paintCodecField(el);
}

document.querySelectorAll(".codec-field").forEach(codecField);

function collectFieldValue(el) {
  if (el.type === "checkbox") return el.checked ? "true" : "false";
  return el.value;
}

function timeFieldParts(el) {
  const parts = String(el.value || "").split(":");
  const hour = Number(parts[0]);
  const minute = Number(parts[1]);
  return [
    String(hour >= 0 && hour <= 23 ? hour : 0),
    String(minute >= 0 && minute <= 59 ? minute : 0),
  ];
}

function setTimeField(el, values) {
  const hour = String(Number(values[el.dataset.key]) || 0).padStart(2, "0");
  const minute = String(Number(values[el.dataset.keyMinute]) || 0).padStart(2, "0");
  el.value = hour + ":" + minute;
}

function timeFieldChanged(el) {
  const [hour, minute] = timeFieldParts(el);
  return hour !== settingsBaseline[el.dataset.key]
    || minute !== settingsBaseline[el.dataset.keyMinute];
}

function setFieldValue(el, rawValue) {
  if (el.type === "checkbox") {
    el.checked = String(rawValue).toLowerCase() === "true";
  } else {
    // Options that do not hold the stored value come back empty on the next save; keep one.
    if (el.tagName === "SELECT"
        && !Array.from(el.options).some((one) => one.value === String(rawValue))) {
      const kept = document.createElement("option");
      kept.value = String(rawValue);
      kept.textContent = String(rawValue);
      el.appendChild(kept);
    }
    el.value = rawValue;
    if (el.classList.contains("codec-field")) paintCodecField(el);
  }
}

function collectChangedSettings(container) {
  const updates = {};
  container.querySelectorAll("[data-key]").forEach((el) => {
    const key = el.dataset.key;
    if (el.dataset.keyMinute) {
      const [hour, minute] = timeFieldParts(el);
      if (hour !== settingsBaseline[key]) updates[key] = hour;
      if (minute !== settingsBaseline[el.dataset.keyMinute]) updates[el.dataset.keyMinute] = minute;
      return;
    }
    const value = collectFieldValue(el);
    if (value !== settingsBaseline[key]) updates[key] = value;
  });
  return updates;
}

async function loadSettingsIntoForm() {
  const result = await apiGet("/api/settings");
  if (!result.ok) return;
  document.querySelectorAll("[data-key]").forEach((el) => {
    const key = el.dataset.key;
    if (el.dataset.keyMinute) {
      if (key in result.data) setTimeField(el, result.data);
      return;
    }
    if (key in result.data) setFieldValue(el, result.data[key]);
  });
  settingsBaseline = { ...result.data };
  noteVolumeShapeNamed(result.data);
  renderScheduleWeek();

  updateStartTimeVisibility();
  updateSpeakerFadeVisibility();
  updateClickSoundRows();
  paintStreamRows();
  paintVolumeShape();
  loadDjIntroFiles();

  if ("hardware.act_led" in result.data) {
    document.getElementById("actLedToggle").checked = result.data["hardware.act_led"] !== "off";
    if (result.data["hardware.usb_port_mode"]) document.getElementById("usbPortMode").value = result.data["hardware.usb_port_mode"];
  }
}

/* What the prepared introductions folder holds, and how much of the library it
   covers: walked by the server, only when the settings are read. */
async function loadDjIntroFiles() {
  const folder = document.getElementById("djIntroFolder");
  const count = document.getElementById("djIntroCount");
  if (!folder || !count) return;
  const result = await apiGet("/api/dj_announcements");
  if (!result.ok) return;
  const data = result.data || {};
  folder.dataset.i18nVarFolder = data.dir || "";
  count.dataset.i18nVarFiles = String(data.files || 0);
  count.dataset.i18nVarCovered = String(data.covered || 0);
  count.dataset.i18nVarTracks = String(data.tracks || 0);
  count.hidden = false;
  [folder, count].forEach((el) => {
    el.textContent = t(el.dataset.i18n, i18nVars(el));
  });
}

function updateSpeakerFadeVisibility() {
  document.getElementById("speakerResumeFadeRow").hidden =
    !document.getElementById("speakerLossPause").checked;
}
document.getElementById("speakerLossPause").addEventListener("change", updateSpeakerFadeVisibility);

async function offerReboot(title) {
  showToast(title, t("system.reboot_needed"), {
    action: {
      label: t("system.reboot_now"),
      icon: "restart",
      run: async () => {
        if (!(await showConfirm(t("system.reboot_confirm")))) return;
        const r = await apiPost("/api/system/reboot");
        if (!r.ok) showToolError(t("common.failed"), r);
      },
    },
  });
}
document.getElementById("usbPortMode").addEventListener("change", async (e) => {
  const select = e.target;
  const value = select.value;
  const previous = settingsBaseline["hardware.usb_port_mode"] || "gadget";
  const ok = await showConfirm(t(value === "host" ? "system.usb_host_confirm" : "system.usb_gadget_confirm"));
  if (!ok) {
    select.value = previous;
    return;
  }
  const r = await apiPost("/api/settings", { "hardware.usb_port_mode": value });
  if (!r.ok) {
    select.value = previous;
    showError(r.error);
    return;
  }
  settingsBaseline["hardware.usb_port_mode"] = value;
  offerReboot(t(value === "host" ? "system.usb_host_done" : "system.usb_gadget_done"));
});

[["usbPlug", "hardware.usb_music_on_plug", "offer"],
 ["usbSwitch", "hardware.usb_music_switch", "after"]].forEach(([id, key, fallback]) => {
  document.getElementById(id).addEventListener("change", async (e) => {
    const select = e.target;
    const value = select.value;
    const previous = settingsBaseline[key] || fallback;
    const r = await apiPost("/api/settings", { [key]: value });
    if (!r.ok) {
      select.value = previous;
      showError(r.error);
      return;
    }
    settingsBaseline[key] = value;
    showToast(t("alert.settings_saved"));
  });
});

document.getElementById("actLedToggle").addEventListener("change", async (e) => {
  const value = e.target.checked ? "default" : "off";
  const r = await apiPost("/api/settings", { "hardware.act_led": value });
  if (!r.ok) {
    e.target.checked = !e.target.checked;
    showError(r.error);
    return;
  }
  settingsBaseline["hardware.act_led"] = value;
  showToast(t(value === "off" ? "system.act_led_off" : "system.act_led_on"));
});

document.getElementById("updateAllowWeb").addEventListener("change", async (e) => {
  const value = e.target.checked ? "true" : "false";
  const r = await apiPost("/api/settings", { "updates.allow_from_web": value });
  if (!r.ok) {
    e.target.checked = !e.target.checked;
    showError(r.error);
    return;
  }
  settingsBaseline["updates.allow_from_web"] = value;
  showToast(t(value === "true" ? "update.allow_web_on" : "update.allow_web_off"));
  checkRelease(false);
});

loadSettingsIntoForm();

async function refreshSettingsIfIdle() {
  const result = await apiGet("/api/settings");
  if (!result.ok) return;
  document.querySelectorAll("[data-key]").forEach((el) => {
    const key = el.dataset.key;
    if (el.dataset.keyMinute) {
      if (!(key in result.data)) return;
      if (!timeFieldChanged(el)) setTimeField(el, result.data);
      settingsBaseline[key] = result.data[key];
      settingsBaseline[el.dataset.keyMinute] = result.data[el.dataset.keyMinute];
      return;
    }
    if (!(key in result.data)) return;
    if (collectFieldValue(el) === settingsBaseline[key]) {
      setFieldValue(el, result.data[key]);
    }
    settingsBaseline[key] = result.data[key];
  });
  updateStartTimeVisibility();
  paintVolumeShape();
}

refreshEvery(refreshSettingsIfIdle, 20000);

let restartPending = false;
let restartDirect = false;
let speechPreparing = false;
let restartTarget = "service";
// The label stays; a clock instead of the button's own icon says it waits for the end of the song.
const RESTART_BUTTONS = {
  service: { id: "btnRestartAfterSong", now: "system.restart_now", later: "system.restart_now",
             icon: "restart", planned: "system.restart_planned", idle: "system.restart_idle_detail" },
  reboot: { id: "btnRebootAfterSong", now: "system.reboot_rukebox", later: "system.reboot_rukebox",
            icon: "power", planned: "system.reboot_planned", idle: "system.reboot_idle_detail" },
};
function paintRestartAfterSong() {
  Object.entries(RESTART_BUTTONS).forEach(([target, b]) => {
    const btn = document.getElementById(b.id);
    const mine = restartPending && restartTarget === target;
    btn.dataset.i18n = mine ? "system.restart_cancel" : (restartDirect ? b.now : b.later);
    btn.dataset.icon = mine || !restartDirect ? "clock" : b.icon;
    btn.textContent = t(btn.dataset.i18n);
    btn.classList.toggle("is-on", mine);
  });
}
paintRestartAfterSong();
Object.entries(RESTART_BUTTONS).forEach(([target, b]) => {
  document.getElementById(b.id).addEventListener("click", async () => {
    const want = !(restartPending && restartTarget === target);
    if (want && target === "reboot" && restartDirect && !(await showConfirm(t("confirm.reboot_now")))) return;
    const r = await apiPost("/api/daemon/restart_after_song", { on: want, target });
    if (!r.ok) {
      showError(r.error);
      return;
    }
    restartPending = !!(r.data && r.data.pending);
    restartTarget = target;
    paintRestartAfterSong();
    if (!want) showToast(t("system.restart_cancelled"));
    else if (restartPending) showToast(t(b.planned));
    else showToast(t("system.restart_idle_title"), t(b.idle));
  });
});

async function restartDaemon() {
  const result = await apiPost("/api/daemon/restart");
  if (result.ok) showToast(t("alert.service_restarted")); else showError(result.error);
}

const SETTINGS_FORMS = ["gameVoiceForm", "tagsKeyForm", "statsPersonForm", "domainForm", "settingsForm", "playbackForm", "volumeForm", "fadesForm", "lightsForm", "mqttForm", "buttonsForm", "speakerForm", "audioOutputForm", "releaseRepoForm"]
  .map((id) => document.getElementById(id))
  .filter(Boolean);

SETTINGS_FORMS.forEach((form) => {
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const updates = collectChangedSettings(form);
    if (Object.keys(updates).length === 0) {
      showToast(t("alert.no_changes"));
      return;
    }
    const result = await apiPost("/api/settings", updates);
    if (!result.ok) {
      showError(result.error);
      return;
    }
    Object.assign(settingsBaseline, updates);

    const d = result.data || {};
    if (d.restart_needed) {
      showToast(t("alert.settings_saved"), t("alert.restart_reminder"), {
        action: { label: t("settings.restart_now"), icon: "restart", run: restartDaemon },
      });
    } else if ("bluetooth.bt_audio_codecs" in updates) {
      showToast(t("alert.settings_applied"),
        t(d.audio_reloaded === false ? "alert.audio_later" : "alert.audio_reloaded"));
    } else if (d.applied_live === false) {
      showToast(t("alert.settings_saved_later"));
    } else {
      showToast(t("alert.settings_applied"));
    }
  });
});


let wledPresets = [];

function paintWledScenes() {
  document.querySelectorAll("select.wled-scene").forEach((select) => {
    const value = select.value || settingsBaseline[select.dataset.key] || "0";
    select.innerHTML = "";
    const add = (id, label) => {
      const option = document.createElement("option");
      option.value = String(id);
      option.textContent = label;
      select.appendChild(option);
    };
    add(0, t("lights.keep"));
    add(-1, t("lights.off"));
    wledPresets.forEach((preset) => add(preset.id, preset.id + " · " + preset.name));
    setFieldValue(select, value);
  });
}

function paintWledDevices(devices) {
  const list = document.getElementById("wledDevices");
  list.innerHTML = "";
  devices.forEach((device) => {
    const li = document.createElement("li");
    const text = document.createElement("span");
    const name = document.createElement("span");
    name.className = "device-name";
    name.textContent = device.missing ? device.host : (device.name || device.host);
    const meta = document.createElement("span");
    meta.className = "device-sub";
    meta.textContent = device.missing ? t("lights.missing")
      : [device.host, t("lights.leds", { n: device.leds || 0 }), "WLED " + (device.ver || "?"),
         device.time ? t("lights.its_time", { time: device.time }) : ""].filter(Boolean).join(" · ");
    text.append(name, meta);
    li.appendChild(text);
    li.classList.toggle("is-off", !!device.missing);
    list.appendChild(li);
  });
  list.hidden = devices.length === 0;
}

function paintWledClock(data) {
  const line = document.getElementById("wledClockState");
  const devices = (data.devices || []).filter((device) => !device.missing);
  const lights = data.lights || {};
  if (!devices.length) {
    line.textContent = "";
  } else if (lights.clock_source === "wled") {
    line.textContent = t("lights.clock_came");
  } else if ((data.devices || []).some((device) => device.clock_known)) {
    line.textContent = t("lights.clock_known");
  } else {
    line.textContent = t("lights.clock_unknown");
  }
}

let wledLast = null;
async function refreshWled() {
  const result = await apiGet("/api/wled");
  if (!result.ok) return;
  wledLast = result.data || {};
  wledPresets = Array.isArray(wledLast.presets) ? wledLast.presets : [];
  paintWledScenes();
  paintWledDevices(wledLast.devices || []);
  paintWledClock(wledLast);
  const status = document.getElementById("wledStatus");
  const devices = wledLast.devices || [];
  status.textContent = !devices.length ? t("lights.none")
    : devices.every((device) => device.missing) ? t("lights.unreachable")
      : !wledPresets.length ? t("lights.no_presets") : "";
}
paintWledScenes();
refreshEvery(refreshWled, 20000, ["settings/lights"]);

/* The Home Assistant link, as the bridge last wrote it. */
let mqttLast = null;
function paintMqttStatus() {
  const s = mqttLast;
  const box = document.getElementById("mqttStatus");
  if (!s) { box.textContent = ""; return; }
  box.classList.toggle("warning", s.state === "error");
  box.textContent = !s.enabled ? t("mqtt.status_off") : s.state === "connected" ? t("mqtt.status_connected")
    : s.state === "error" ? t("mqtt.status_error", { why: t("mqtt.error_" + s.error) === "mqtt.error_" + s.error
      ? s.error : t("mqtt.error_" + s.error) }) : t("mqtt.status_waiting");
}
async function refreshMqtt() {
  const r = await apiGet("/api/mqtt/status");
  if (!r.ok) return;
  mqttLast = r.data || null;
  paintMqttStatus();
}
refreshEvery(refreshMqtt, 5000, ["settings/homeassistant"]);
window.LANG_CHANGE_LISTENERS.push(paintMqttStatus);

/* The night cap: its fields only while it is on, and what 100% and 50% then play at. */
/* The shape the volume takes by itself: nothing, the night cap, or the daily
   curve. One choice drives both sets of fields, so nothing is shown twice. */
let volumeShapeNamed = false;

/* A radio that updated has no shape in its file: the cap's own switch is what
   it does, so the card shows that, and says so. The moment the choice is made,
   the choice is what counts - including "nothing". */
function noteVolumeShapeNamed(data) {
  volumeShapeNamed = Object.prototype.hasOwnProperty.call(data || {}, "playback.volume_shape");
  if (volumeShapeNamed) return;
  document.getElementById("volumeShape").value =
    document.getElementById("volumeCapOn").checked ? "cap" : "off";
}

function volumeShape() {
  return document.getElementById("volumeShape").value || "off";
}

function paintVolumeShape() {
  const shape = volumeShape();
  const cap = shape === "cap";
  document.getElementById("volumeCapRow").hidden = !cap;
  document.getElementById("volumeCapHoursRow").hidden = !cap;
  document.getElementById("volumeCapFadeRow").hidden = !cap;
  document.getElementById("volumeCapOldRow").hidden = true;
  document.getElementById("volumeShapeOld").hidden = volumeShapeNamed || shape !== "cap";
  const percent = Math.max(0, Math.min(100,
    Number(document.getElementById("volumeCapPercent").value) || 0));
  document.getElementById("volumeCapExample").textContent = t("settings.volume_cap_example",
    { full: percent, half: Math.round(percent / 2) });
  const editing = shape === "curve";
  document.getElementById("volumeCurveRow").hidden = !editing;
  document.getElementById("volumeCurveManualRow").hidden = !editing;
  document.getElementById("volumeCurveEditor").hidden = !editing;
  if (editing) refreshVolumeCurve();
}
["volumeShape", "volumeCapPercent"].forEach((id) => {
  document.getElementById(id).addEventListener("input", paintVolumeShape);
  document.getElementById(id).addEventListener("change", paintVolumeShape);
});
// The cap's own switch is what a version before the curve reads: the choice
// above keeps it true or false, so the file never says two things at once.
document.getElementById("volumeShape").addEventListener("change", () => {
  volumeShapeNamed = true;
  document.getElementById("volumeCapOn").checked = volumeShape() === "cap";
});
window.LANG_CHANGE_LISTENERS.push(paintVolumeShape);

