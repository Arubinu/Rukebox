function syncClickSourceOptions(customItems) {
  document.querySelectorAll(".click-source-select").forEach((select) => {
    const key = select.dataset.key;

    const current = select.value;
    select.querySelectorAll('option[data-custom="1"]').forEach((opt) => opt.remove());
    customItems.forEach((item) => {
      const opt = document.createElement("option");
      opt.value = "custom:" + item.id;
      opt.textContent = item.name;
      opt.dataset.custom = "1";
      select.appendChild(opt);
    });
    if (key && key in settingsBaseline) {
      setFieldValue(select, settingsBaseline[key]);
    } else if ([...select.options].some((o) => o.value === current)) {
      select.value = current;
    } else if (select.id === "trackOrderSource" && current !== select.value) {
      loadTrackOrder();
    }
  });
}

const CLICK_SOUND_ACTIONS = ["next", "previous", "sound"];
function updateClickSoundRows() {
  document.querySelectorAll(".click-sound-row").forEach((row) => {
    const action = document.querySelector('.click-action-select[data-click="' + row.dataset.click + '"]');
    row.hidden = action && !CLICK_SOUND_ACTIONS.includes(action.value);
  });
}
document.querySelectorAll(".click-action-select").forEach((select) => {
  select.addEventListener("change", updateClickSoundRows);
});

document.getElementById("btnSkipVote").addEventListener("click", async () => {
  const r = await apiPost("/api/vote/skip", {});
  if (!r.ok) {
    showError(r.error, t("vote.title"));
    return;
  }
  if (r.data.skipped) showToast(t("vote.skipped"));
  else paintSkipVote(r.data);
  refreshStatus();
});

document.getElementById("btnSpeechTest").addEventListener("click", async () => {
  const r = await apiPost("/api/speech/test", { kind: "time_date" });
  if (!r.ok) showError(r.error, t("settings.speech_test"));
});

document.querySelectorAll(".click-test-btn").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const field = btn.dataset.click;

    if (document.getElementById(field + "ClickAction").value === "poweroff"
        && !(await showConfirm(t("home.confirm_shutdown")))) return;
    const r = await apiPost("/api/action/test_click", {
      kind: btn.dataset.kind || field,
      action: document.getElementById(field + "ClickAction").value,
      sound: document.getElementById(field + "ClickSource").value,
    });
    if (!r.ok) {
      showToast(t("common.failed"),
                r.error === "not_playing_music" ? t("click.test_needs_music") : errorLabel(r.error),
                { error: true });
    }
  });
});

let announceVolumes = {};

async function refreshAnnouncementVolumes() {
  const result = await apiGet("/api/announcement_volumes");
  if (result.ok && result.data && typeof result.data === "object") announceVolumes = result.data;
  return announceVolumes;
}

function announceVolume(key) {
  const entry = announceVolumes[key] || {};
  const volume = Number(entry.volume);
  return { on: !!entry.on, volume: Number.isFinite(volume) ? volume : null };
}

async function saveAnnounceVolume(key, on, volume) {
  const result = await apiPost("/api/announcement_volumes/" + encodeURIComponent(key),
                               { on, volume });
  if (!result.ok) {
    showError(result.error);
    return false;
  }
  announceVolumes[key] = result.data || { on, volume };
  return true;
}

function defaultAnnounceVolume() {
  const slider = document.getElementById("volumeSlider");
  const value = slider ? Number(slider.value) : NaN;
  return Number.isFinite(value) ? value : 50;
}

function announceVolumeWarning(entry, music) {
  /* 4x the music is about 12 dB: past that the music is covered. */
  if (!entry.on || entry.volume === null || !music) return "";
  if (entry.volume < music * 4) return "";
  return t("annvol.louder", { music: Math.round(music) });
}

function refreshAnnounceVolumeNotes() {
  const music = defaultAnnounceVolume();
  document.querySelectorAll(".ann-volume-note").forEach((note) => {
    const text = announceVolumeWarning(announceVolume(note.dataset.key), music);
    note.textContent = text;
    note.hidden = !text;
  });
}

let volumeControlSeq = 0;

function volumeField(labelKey, control, describedBy) {
  const row = document.createElement("div");
  row.className = "field-row";
  const text = document.createElement("div");
  text.className = "field-text";
  const label = document.createElement("label");
  label.htmlFor = control.id;
  label.textContent = t(labelKey);
  text.appendChild(label);
  if (describedBy) {
    text.appendChild(describedBy);
    label.setAttribute("aria-describedby", describedBy.id);
  }
  row.append(text, control);
  return row;
}

function volumeControl(key, options) {
  const opts = options || {};
  const current = announceVolume(key);
  const uid = "annvol" + (++volumeControlSeq);

  const toggle = document.createElement("input");
  toggle.type = "checkbox";
  toggle.id = uid + "-on";
  toggle.className = "switch-input";
  toggle.setAttribute("role", "switch");
  toggle.checked = current.on;

  const value = document.createElement("input");
  value.type = "number";
  value.id = uid + "-value";
  value.className = "num-input ann-volume-value";
  value.min = "0";
  value.max = "100";
  value.step = "1";
  value.inputMode = "numeric";
  value.value = String(current.volume === null ? defaultAnnounceVolume() : current.volume);
  value.setAttribute("aria-label", t("annvol.label"));

  let hint = null;
  if (opts.hint) {
    hint = document.createElement("p");
    hint.className = "field-desc";
    hint.id = uid + "-desc";
    hint.textContent = t("annvol.desc");
  }

  const holder = document.createElement("div");
  holder.className = "ann-volume" + (opts.inline ? " is-inline" : "");

  const note = document.createElement("p");
  note.className = "hint ann-volume-note";
  note.dataset.key = key;
  // Painted here, not left to refreshAnnounceVolumeNotes(): the holder is not in the page yet.
  const paintNote = () => {
    const text = announceVolumeWarning(announceVolume(key), defaultAnnounceVolume());
    note.textContent = text;
    note.hidden = !text;
  };

  const paint = () => {
    value.disabled = !toggle.checked;
    holder.classList.toggle("is-on", toggle.checked);
    paintNote();
  };
  const store = async () => {
    const volume = Math.max(0, Math.min(100, Math.round(Number(value.value) || 0)));
    value.value = String(volume);
    if (await saveAnnounceVolume(key, toggle.checked, volume)) paint();
    paintNote();
  };
  toggle.addEventListener("change", store);
  value.addEventListener("change", store);

  paint();
  holder.append(volumeField("annvol.own", toggle, hint), volumeField("annvol.label", value),
                note);
  return holder;
}

async function refreshAnnouncements() {
  // The list is emptied only once the answer is in: clearing it first made the card blink.
  const [result] = await Promise.all([apiGet("/api/announcements"),
                                      refreshAnnouncementVolumes()]);
  const list = document.getElementById("announcementList");
  list.innerHTML = "";

  const items = result.ok && Array.isArray(result.data) ? result.data : [];

  customAnnouncementsCache = items;
  syncClickSourceOptions(items);
  if (!items.length) {
    const li = document.createElement("li");
    li.textContent = t("announcements.none_yet");
    list.appendChild(li);
    return;
  }

  items.forEach((item) => list.appendChild(announcementRow(item)));
}

let openAnnouncementId = null;

function announcementWhen(item) {
  const trigger = item.trigger || "time";
  if (trigger === "manual") return t("announcements.on_demand_cap");
  if (trigger === "time") return formatHM(item.hour, item.minute);
  const key = trigger === "after_music" ? "when.after_music" : "when.after_boot";
  const n = Number(item.repeat_times);
  const repeat = n === 0 ? t("when.forever") : n > 1 ? t("when.times", { n }) : "";
  return t(key, { min: item.delay_min }) + (repeat ? " \u00b7 " + repeat : "");
}

function setAnnouncementOpen(id) {
  openAnnouncementId = id;
  document.querySelectorAll("#announcementList .ann-item").forEach((li) => {
    const open = li.dataset.id === id;
    li.classList.toggle("is-open", open);
    li.querySelector(".ann-head").setAttribute("aria-expanded", open ? "true" : "false");
    li.querySelector(".ann-body").hidden = !open;
  });
}

function announcementRow(item) {
  const li = document.createElement("li");
  li.className = "ann-item";
  li.dataset.id = item.id;

  if (!item.enabled) li.classList.add("is-off");
  const open = item.id === openAnnouncementId;
  if (open) li.classList.add("is-open");

  const bodyId = "ann-body-" + item.id;
  const head = document.createElement("button");
  head.type = "button";
  head.className = "ann-head";
  head.setAttribute("aria-expanded", open ? "true" : "false");
  head.setAttribute("aria-controls", bodyId);

  const text = document.createElement("span");
  text.className = "ann-text";
  const name = document.createElement("span");
  name.className = "ann-name";
  name.textContent = item.name;
  const meta = document.createElement("span");
  meta.className = "ann-meta";

  const when = announcementWhen(item);
  const chance = item.trigger !== "manual" && item.auto_chance && item.auto_chance !== "1/1"
    ? " \u00b7 " + chanceLabel(item.auto_chance) : "";
  meta.textContent = when + chance + " \u00b7 " + item.file_count + " " +
    t(item.file_count === 1 ? "common.file_one" : "common.file_other");
  if (!item.enabled) {
    const off = document.createElement("span");
    off.className = "sr-only";
    off.textContent = ", " + t("announcements.state_off");
    meta.appendChild(off);
  }
  text.append(name, meta);
  const chevron = document.createElement("span");
  chevron.className = "ann-chevron";
  chevron.dataset.icon = "chevron";
  chevron.setAttribute("aria-hidden", "true");
  head.append(text, chevron);
  head.addEventListener("click", () => {
    setAnnouncementOpen(li.classList.contains("is-open") ? null : item.id);
  });

  const body = document.createElement("div");
  body.className = "ann-body";
  body.id = bodyId;
  body.hidden = !open;

  const actions = document.createElement("div");
  actions.className = "ann-actions";
  const button = (icon, key, onClick) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "btn";
    b.dataset.icon = icon;
    b.textContent = t(key);
    b.addEventListener("click", onClick);
    return b;
  };
  actions.append(
    button("play", "announcements.play", async () => {
      const r = await apiPost("/api/announcements/" + item.id + "/play");
      if (!r.ok) {
        showToast(t("common.failed"),
                  r.error === "not_playing_music"
                    ? t("announcements.not_playing")
                    : errorLabel(r.error), { error: true });
      }
    }),

    button("folder", "annfiles.files_btn", () => openAnnounceFiles("custom:" + item.id)),

    button("pencil", "common.edit", () => startEditAnnouncement(item)),
  );

  const manage = document.createElement("div");
  manage.className = "ann-manage";
  const switchLabel = document.createElement("label");
  switchLabel.className = "ann-switch";
  const toggle = document.createElement("input");
  toggle.type = "checkbox";
  toggle.className = "switch-input";
  toggle.setAttribute("role", "switch");
  toggle.checked = !!item.enabled;
  const switchText = document.createElement("span");
  switchText.textContent = t("announcements.active");
  switchLabel.append(toggle, switchText);
  toggle.addEventListener("change", async () => {
    const wanted = toggle.checked;
    li.classList.toggle("is-off", !wanted);
    const r = await apiPost("/api/announcements/" + item.id, { enabled: wanted });
    if (!r.ok) {
      toggle.checked = !wanted;
      li.classList.toggle("is-off", wanted);
      showError(r.error);
      return;
    }
    refreshAnnouncements();
  });

  const del = button("trash", "common.delete", async () => {
    if (!(await showConfirm(t("confirm.delete_announcement", { name: item.name })))) return;
    const r = await apiDelete("/api/announcements/" + item.id);
    if (!r.ok) showError(r.error);
    if (openAnnouncementId === item.id) openAnnouncementId = null;
    refreshAnnouncements();
  });
  del.classList.add("btn-danger-outline", "ann-delete");
  const dup = button("copy", "common.duplicate", () => startDuplicateAnnouncement(item));
  dup.classList.add("ann-duplicate");
  manage.append(switchLabel, dup, del);

  body.append(actions, manage);
  li.append(head, body);
  return li;
}

refreshAnnouncements();
refreshEvery(refreshAnnouncements, 20000);

/* Schedules: a row per schedule, and one form for a new one or the one being edited. */
let schedulesData = [];
let schedulesAllowed = [];
let openScheduleId = null;
let editingScheduleId = null;
let runningScheduleId = null;

function weekdayNames() {
  const format = new Intl.DateTimeFormat(currentLang, { weekday: "short", timeZone: "UTC" });
  // 1 January 2024 was a Monday, which is day 0 here as on the Pi.
  return [0, 1, 2, 3, 4, 5, 6].map((i) => format.format(new Date(Date.UTC(2024, 0, 1 + i))).replace(/\.$/, ""));
}

/* The same day as `iso` in any year; a 29 February comes on the 28th otherwise. */
function scheduleYearlyMatches(iso, date) {
  const month = Number(iso.slice(5, 7));
  let day = Number(iso.slice(8, 10));
  const year = date.getUTCFullYear();
  const leap = (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0;
  if (month === 2 && day === 29 && !leap) day = 28;
  return date.getUTCMonth() + 1 === month && date.getUTCDate() === day;
}

function scheduleDaysLabel(item) {
  if (item.party) return t("schedules.when_party");
  if (item.date && item.yearly) {
    const day = new Intl.DateTimeFormat(currentLang, { day: "numeric", month: "long", timeZone: "UTC" })
      .format(new Date(item.date + "T00:00:00Z"));
    return t("schedules.every_year", { day });
  }
  if (item.date) {
    return new Intl.DateTimeFormat(currentLang, { dateStyle: "medium", timeZone: "UTC" })
      .format(new Date(item.date + "T00:00:00Z"));
  }
  const days = item.days || [];
  if (!days.length || days.length === 7) return t("schedules.when_daily");
  const names = weekdayNames();
  return days.map((day) => names[day]).join(" ");
}

function scheduleMeta(item) {
  const bits = [scheduleDaysLabel(item)];
  if (item.party) bits.push(t(item.guests ? "schedules.party_guests" : "schedules.party_no_guests"));
  else if (item.start && item.stop) bits.push(item.start + " → " + item.stop);
  else if (item.start) bits.push(item.start);
  else bits.push("→ " + item.stop);
  if (item.stop) bits.push(t("schedules.action_" + item.stop_action));
  const extra = Object.keys(item.settings || {}).length;
  if (extra) bits.push(t("schedules.settings_count", { n: extra }));
  return bits.join(" · ");
}

