function scheduleNextWhen(d) {
  const next = d.schedule_next;
  if (String(d.system_time || "").slice(0, 10) === next.date) return next.at;
  const day = new Intl.DateTimeFormat(currentLang, { weekday: "long", timeZone: "UTC" })
    .format(new Date(next.date + "T00:00:00Z"));
  return day + " " + next.at;
}

function paintScheduleRunning(id) {
  runningScheduleId = id;
  document.querySelectorAll("#scheduleList .ann-item").forEach((li) => {
    li.classList.toggle("is-playing", li.dataset.id === id);
  });
}

function setScheduleOpen(id) {
  openScheduleId = id;
  document.querySelectorAll("#scheduleList .ann-item").forEach((li) => {
    const open = li.dataset.id === id;
    li.classList.toggle("is-open", open);
    li.querySelector(".ann-head").setAttribute("aria-expanded", open ? "true" : "false");
    li.querySelector(".ann-body").hidden = !open;
  });
}

function scheduleRow(item) {
  const li = document.createElement("li");
  li.className = "ann-item";
  li.dataset.id = item.id;
  if (!item.enabled) li.classList.add("is-off");
  if (item.id === runningScheduleId) li.classList.add("is-playing");
  const open = item.id === openScheduleId;
  if (open) li.classList.add("is-open");

  const bodyId = "sched-body-" + item.id;
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
  meta.textContent = scheduleMeta(item);
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
  head.addEventListener("click", () => setScheduleOpen(li.classList.contains("is-open") ? null : item.id));

  const body = document.createElement("div");
  body.className = "ann-body";
  body.id = bodyId;
  body.hidden = !open;

  const actions = document.createElement("div");
  actions.className = "ann-actions";
  const edit = document.createElement("button");
  edit.type = "button";
  edit.className = "btn sched-edit";
  edit.dataset.icon = "pencil";
  edit.textContent = t("common.edit");
  edit.addEventListener("click", () => startEditSchedule(item));
  const copy = document.createElement("button");
  copy.type = "button";
  copy.className = "btn sched-copy";
  copy.dataset.icon = "copy";
  copy.textContent = t("common.duplicate");
  copy.addEventListener("click", () => startEditSchedule(
    Object.assign({}, item, { name: t("common.copy_name", { name: item.name }).slice(0, 40) }), true));
  const del = document.createElement("button");
  del.type = "button";
  del.className = "btn btn-danger-outline sched-delete";
  del.dataset.icon = "trash";
  del.textContent = t("common.delete");
  del.addEventListener("click", async () => {
    if (!(await showConfirm(t("confirm.delete_schedule", { name: item.name })))) return;
    const r = await apiDelete("/api/schedules/" + item.id);
    if (!r.ok) showError(r.error);
    if (editingScheduleId === item.id) resetScheduleForm();
    refreshSchedules();
  });
  actions.append(edit, copy, del);

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
    const r = await apiPost("/api/schedules/" + item.id, { enabled: wanted });
    if (!r.ok) {
      toggle.checked = !wanted;
      showError(r.error);
      return;
    }
    refreshSchedules();
  });
  // The first of the list wins when two overlap, so the order is the owner's to set.
  const order = document.createElement("span");
  order.className = "sched-order";
  const index = schedulesData.findIndex((one) => one.id === item.id);
  [["arrow-up", "trackorder.move_up", -1], ["arrow-down", "trackorder.move_down", 1]].forEach(([icon, key, step]) => {
    const arrow = document.createElement("button");
    arrow.type = "button";
    arrow.className = "btn btn-icon btn-small sched-move";
    arrow.dataset.icon = icon;
    arrow.dataset.step = String(step);
    arrow.setAttribute("aria-label", t(key));
    arrow.title = t(key);
    arrow.disabled = index + step < 0 || index + step >= schedulesData.length;
    arrow.addEventListener("click", () => moveSchedule(item.id, step));
    order.appendChild(arrow);
  });
  manage.append(switchLabel, order);

  body.append(actions, manage);
  li.append(head, body);
  return li;
}

async function moveSchedule(id, step) {
  const ids = schedulesData.map((one) => one.id);
  const from = ids.indexOf(id);
  const to = from + step;
  if (from < 0 || to < 0 || to >= ids.length) return;
  ids.splice(to, 0, ids.splice(from, 1)[0]);
  const r = await apiPost("/api/schedule_order", { order: ids });
  if (!r.ok) {
    showError(r.error);
    return;
  }
  schedulesData = r.data.schedules;
  renderSchedules();
}

function minutesOfDay(text) {
  const parts = String(text || "").split(":");
  return Number(parts[0]) * 60 + Number(parts[1]);
}

/* The coming seven days, a line each: what runs when, and where the daily cutoff falls. */
function renderScheduleWeek() {
  const box = document.getElementById("scheduleWeek");
  const live = schedulesData.filter((one) => one.enabled);
  box.hidden = !live.length;
  if (!live.length) {
    box.replaceChildren();
    return;
  }
  const today = (playerStatus && /^\d{4}-\d\d-\d\d/.test(playerStatus.system_time || ""))
    ? playerStatus.system_time.slice(0, 10) : new Date().toISOString().slice(0, 10);
  const base = new Date(today + "T00:00:00Z").getTime();
  const rows = [0, 1, 2, 3, 4, 5, 6].map(() => ({ segments: [], marks: [] }));
  const matches = (item, date) => {
    if (item.date) return item.date === date.toISOString().slice(0, 10);
    const days = item.days || [];
    return !days.length || days.includes((date.getUTCDay() + 6) % 7);
  };
  for (let day = -1; day < 7; day += 1) {
    const date = new Date(base + day * 86400000);
    live.forEach((item) => {
      if (!matches(item, date)) return;
      if (!item.start) {
        if (day >= 0) rows[day].marks.push({ at: minutesOfDay(item.stop), kind: "stop", item });
        return;
      }
      const begin = minutesOfDay(item.start);
      let end = item.stop ? minutesOfDay(item.stop) : 1440;
      if (end <= begin) end += 1440;
      if (day >= 0) rows[day].segments.push({ from: begin, to: Math.min(end, 1440), item });
      if (end > 1440 && day + 1 < 7) rows[day + 1].segments.push({ from: 0, to: end - 1440, item });
    });
  }
  const cutoff = settingsBaseline["schedule.cutoff_enabled"] !== "false"
    && settingsBaseline["schedule.cutoff_hour"] !== undefined
    ? Number(settingsBaseline["schedule.cutoff_hour"]) * 60
      + Number(settingsBaseline["schedule.cutoff_minute"] || 0) : null;
  const dayName = new Intl.DateTimeFormat(currentLang, { weekday: "short", day: "numeric", timeZone: "UTC" });
  const clock = (minutes) => formatHM(Math.floor(minutes / 60) % 24, minutes % 60);

  const title = document.createElement("p");
  title.className = "week-title";
  title.textContent = t("schedules.week_title");
  const lines = rows.map((row, day) => {
    const line = document.createElement("div");
    line.className = "week-row" + (day === 0 ? " is-today" : "");
    const label = document.createElement("span");
    label.className = "week-day";
    label.textContent = dayName.format(new Date(base + day * 86400000));
    const track = document.createElement("div");
    track.className = "week-track";
    const said = [];
    row.segments.forEach((segment) => {
      const bar = document.createElement("button");
      bar.type = "button";
      bar.className = "week-seg" + (segment.item.date ? " is-dated" : "");
      bar.style.left = (segment.from / 14.4) + "%";
      bar.style.width = ((segment.to - segment.from) / 14.4) + "%";
      bar.textContent = segment.item.name;
      const words = segment.item.name + " " + clock(segment.from) + "\u2013" + clock(segment.to);
      bar.title = words;
      said.push(words);
      bar.addEventListener("click", () => {
        setScheduleOpen(segment.item.id);
        const target = document.querySelector('#scheduleList .ann-item[data-id="' + CSS.escape(segment.item.id) + '"]');
        if (target) target.scrollIntoView({ behavior: "smooth", block: "center" });
      });
      track.appendChild(bar);
    });
    row.marks.forEach((mark) => {
      const tick = document.createElement("span");
      tick.className = "week-mark is-stop";
      tick.style.left = (mark.at / 14.4) + "%";
      tick.title = mark.item.name + " " + clock(mark.at);
      said.push(tick.title);
      track.appendChild(tick);
    });
    if (cutoff !== null) {
      const tick = document.createElement("span");
      tick.className = "week-mark is-cutoff";
      tick.style.left = (cutoff / 14.4) + "%";
      tick.title = t("schedules.week_cutoff", { time: clock(cutoff) });
      said.push(tick.title);
      track.appendChild(tick);
    }
    track.setAttribute("role", "group");
    track.setAttribute("aria-label", label.textContent + ": " + (said.join(", ") || t("schedules.week_nothing")));
    line.append(label, track);
    return line;
  });
  const axis = document.createElement("div");
  axis.className = "week-row week-axis";
  axis.setAttribute("aria-hidden", "true");
  const scale = document.createElement("div");
  scale.className = "week-scale";
  ["0", "6", "12", "18", "24"].forEach((hour) => {
    const mark = document.createElement("span");
    mark.textContent = hour;
    scale.appendChild(mark);
  });
  axis.append(document.createElement("span"), scale);
  box.replaceChildren(title, ...lines, axis);
}

function renderSchedules() {
  renderScheduleWeek();
  const list = document.getElementById("scheduleList");
  list.replaceChildren();
  if (!schedulesData.length) {
    const li = document.createElement("li");
    li.textContent = t("schedules.none_yet");
    list.appendChild(li);
    return;
  }
  schedulesData.forEach((item) => list.appendChild(scheduleRow(item)));
}

async function refreshSchedules() {
  const result = await apiGet("/api/schedules");
  if (!result.ok || !result.data) return;
  schedulesData = Array.isArray(result.data.schedules) ? result.data.schedules : [];
  schedulesAllowed = Array.isArray(result.data.settings) ? result.data.settings : [];
  renderSchedules();
  fillScheduleSettingChoices();
}

const schedForm = document.getElementById("scheduleForm");
const schedOverrides = document.getElementById("schedOverrides");
const schedAddSetting = document.getElementById("schedAddSetting");

function paintScheduleDays(chosen) {
  const box = document.getElementById("schedDays");
  const picked = new Set(chosen || Array.from(box.querySelectorAll("input:checked")).map((el) => Number(el.value)));
  box.replaceChildren(...weekdayNames().map((label, day) => {
    const chip = document.createElement("label");
    chip.className = "day-chip";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = String(day);
    input.checked = picked.has(day);
    const text = document.createElement("span");
    text.textContent = label;
    chip.append(input, text);
    return chip;
  }));
}

function updateScheduleForm() {
  const when = document.getElementById("schedWhen").value;
  document.getElementById("schedDays").hidden = when !== "days";
  document.getElementById("schedDateRow").hidden = when !== "date";
  const startOn = document.getElementById("schedStartOn").checked;
  const stopOn = document.getElementById("schedStopOn").checked;
  document.getElementById("schedStart").disabled = !startOn;
  document.getElementById("schedStop").disabled = !stopOn;
  document.getElementById("schedActionRow").hidden = !stopOn;
  document.getElementById("schedAnnouncementRow").hidden = !startOn;
}
["schedWhen", "schedStartOn", "schedStopOn"].forEach((id) => {
  document.getElementById(id).addEventListener("change", updateScheduleForm);
});

function fillScheduleLists(value) {
  const select = document.getElementById("schedList");
  let lists = [];
  try {
    lists = (listsData && listsData.lists) || [];
  } catch (e) {
    // Declared further down: at startup the music lists are not there yet.
  }
  select.replaceChildren();
  [["keep", t("schedules.list_keep")], ["all", t("schedules.list_all")]]
    .concat(lists.map((one) => ["id:" + one.id, one.name]))
    .forEach(([optionValue, label]) => {
      const option = document.createElement("option");
      option.value = optionValue;
      option.textContent = label;
      select.appendChild(option);
    });
  const wanted = value === null || value === undefined ? "keep" : value === "" ? "all" : "id:" + value;
  setFieldValue(select, wanted);
}

function fillScheduleAnnouncements(value) {
  const select = document.getElementById("schedAnnouncement");
  select.replaceChildren();
  [["", t("schedules.announcement_none")]]
    .concat(customAnnouncementsCache.map((one) => [one.id, one.name]))
    .forEach(([optionValue, label]) => {
      const option = document.createElement("option");
      option.value = optionValue;
      option.textContent = label;
      select.appendChild(option);
    });
  setFieldValue(select, value || "");
}

/* A schedule's setting is edited with a copy of the very control the settings pages use. */
function overrideSource(key) {
  const el = document.querySelector('#main [data-key="' + key + '"]');
  return el && ["INPUT", "SELECT"].includes(el.tagName) ? el : null;
}

/* A setting's name as its page shows it; the key itself when no field carries it. */
function settingName(key) {
  const el = document.querySelector('#main [data-key="' + key + '"], #main [data-key-minute="' + key + '"]');
  const label = el && el.id ? document.querySelector('label[for="' + el.id + '"]') : null;
  const text = label ? label.textContent : el && el.getAttribute("aria-label");
  return text && text.trim() ? text.trim().replace(/\s+/g, " ") : key;
}

function overrideRow(key, values) {
  const source = overrideSource(key);
  if (!source) return null;
  const row = document.createElement("div");
  row.className = "field-row override-row";
  const label = document.createElement("span");
  label.className = "field-label";
  label.textContent = settingName(key);
  const control = source.cloneNode(true);
  ["id", "data-key", "data-key-minute", "aria-describedby", "hidden", "disabled"]
    .forEach((name) => control.removeAttribute(name));
  control.dataset.override = key;
  control.setAttribute("aria-label", label.textContent);
  if (source.dataset.keyMinute) {
    control.dataset.overrideMinute = source.dataset.keyMinute;
    control.value = formatHM(Number(values[key]) || 0, Number(values[source.dataset.keyMinute]) || 0);
  } else {
    setFieldValue(control, values[key]);
  }
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "btn btn-icon btn-small override-remove";
  remove.dataset.icon = "x";
  remove.setAttribute("aria-label", t("schedules.remove_setting", { name: label.textContent }));
  remove.addEventListener("click", () => {
    row.remove();
    fillScheduleSettingChoices();
  });
  const holder = document.createElement("span");
  holder.className = "override-control";
  holder.append(control, remove);
  row.append(label, holder);
  return row;
}

function currentSettingValues(key) {
  const source = overrideSource(key);
  const values = {};
  if (!source) return values;
  if (source.dataset.keyMinute) {
    const [hour, minute] = timeFieldParts(source);
    values[key] = hour;
    values[source.dataset.keyMinute] = minute;
  } else {
    values[key] = collectFieldValue(source);
  }
  return values;
}

function fillScheduleSettingChoices() {
  const used = new Set(Array.from(schedOverrides.querySelectorAll("[data-override]"))
    .map((el) => el.dataset.override));
  const choices = schedulesAllowed
    .filter((key) => key !== "playback.base_volume" && !used.has(key) && overrideSource(key))
    .map((key) => [key, settingName(key)])
    .sort((a, b) => a[1].localeCompare(b[1]));
  schedAddSetting.replaceChildren();
  [["", t("schedules.add_setting")]].concat(choices).forEach(([value, label]) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    schedAddSetting.appendChild(option);
  });
}
schedAddSetting.addEventListener("change", () => {
  const key = schedAddSetting.value;
  if (!key) return;
  const row = overrideRow(key, currentSettingValues(key));
  if (row) schedOverrides.appendChild(row);
  fillScheduleSettingChoices();
});

function collectScheduleSettings() {
  const out = {};
  schedOverrides.querySelectorAll("[data-override]").forEach((el) => {
    if (el.dataset.overrideMinute) {
      const [hour, minute] = timeFieldParts(el);
      out[el.dataset.override] = hour;
      out[el.dataset.overrideMinute] = minute;
    } else {
      out[el.dataset.override] = collectFieldValue(el);
    }
  });
  const volume = document.getElementById("schedVolume").value.trim();
  if (volume !== "") out["playback.base_volume"] = volume;
  return out;
}

function fillScheduleForm(item) {
  const data = item || { name: "", days: [], date: null, start: "07:00", stop: null,
                         stop_action: "pause", list: null, settings: {} };
  document.getElementById("schedName").value = data.name;
  document.getElementById("schedWhen").value = data.date ? "date" : (data.days || []).length ? "days" : "daily";
  paintScheduleDays(data.days || []);
  document.getElementById("schedDate").value = data.date || "";
  document.getElementById("schedStartOn").checked = !!data.start;
  document.getElementById("schedStart").value = data.start || "07:00";
  document.getElementById("schedStopOn").checked = !!data.stop;
  document.getElementById("schedStop").value = data.stop || "09:00";
  document.getElementById("schedAction").value = data.stop_action || "pause";
  const settings = data.settings || {};
  document.getElementById("schedVolume").value =
    "playback.base_volume" in settings ? settings["playback.base_volume"] : "";
  fillScheduleLists(data.list);
  fillScheduleAnnouncements(data.announcement);
  schedOverrides.replaceChildren();
  Object.keys(settings).forEach((key) => {
    if (key === "playback.base_volume") return;
    const row = overrideRow(key, settings);
    if (row) schedOverrides.appendChild(row);
  });
  fillScheduleSettingChoices();
  updateScheduleForm();
}

function setScheduleFormTitle(item) {
  const title = document.getElementById("scheduleFormTitle");
  title.dataset.i18n = item ? "schedules.edit_section" : "schedules.add_section";
  if (item) title.dataset.i18nVarName = item.name;
  else delete title.dataset.i18nVarName;
  title.textContent = t(title.dataset.i18n, item ? { name: item.name } : undefined);
}

function resetScheduleForm() {
  editingScheduleId = null;
  setScheduleFormTitle(null);
  fillScheduleForm(null);
  document.getElementById("scheduleFormSection").open = false;
}

function startEditSchedule(item, asCopy) {
  editingScheduleId = asCopy ? null : item.id;
  setScheduleFormTitle(asCopy ? null : item);
  fillScheduleForm(item);
  const section = document.getElementById("scheduleFormSection");
  section.open = true;
  section.scrollIntoView({ behavior: "smooth", block: "start" });
}

document.getElementById("scheduleFormSection").addEventListener("toggle", (event) => {
  // The lists are only known once their own card has loaded, well after this one.
  if (!event.target.open || editingScheduleId) return;
  // Only an untouched form: a copy being prepared has its own choices.
  if (document.getElementById("schedName").value) return;
  fillScheduleLists(null);
  fillScheduleAnnouncements(null);
});
document.getElementById("schedCancel").addEventListener("click", resetScheduleForm);

schedForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const when = document.getElementById("schedWhen").value;
  const list = document.getElementById("schedList").value;
  const body = {
    name: document.getElementById("schedName").value,
    days: when === "days"
      ? Array.from(document.querySelectorAll("#schedDays input:checked")).map((el) => Number(el.value)) : [],
    date: when === "date" ? document.getElementById("schedDate").value : null,
    start: document.getElementById("schedStartOn").checked ? document.getElementById("schedStart").value : null,
    stop: document.getElementById("schedStopOn").checked ? document.getElementById("schedStop").value : null,
    stop_action: document.getElementById("schedAction").value,
    list: list === "keep" ? null : list === "all" ? "" : list.slice(3),
    announcement: document.getElementById("schedAnnouncement").value || null,
    settings: collectScheduleSettings(),
  };
  if (when === "days" && !body.days.length) {
    showError("schedule_bad_days");
    return;
  }
  if (when === "date" && !body.date) {
    showError("schedule_bad_date");
    return;
  }
  const result = await apiPost(editingScheduleId ? "/api/schedules/" + editingScheduleId : "/api/schedules", body);
  if (!result.ok) {
    showError(result.error);
    return;
  }
  openScheduleId = result.data.id;
  showToast(t("schedules.saved", { name: result.data.name }));
  resetScheduleForm();
  refreshSchedules();
});

fillScheduleForm(null);
refreshSchedules();
refreshEvery(refreshSchedules, 20000);
window.LANG_CHANGE_LISTENERS.push(() => {
  renderSchedules();
  paintScheduleDays();
  fillScheduleSettingChoices();
});

let audioOutputs = [];
let audioOutputsKnown = false;

function paintStreamRows() {
  // UPnP follows the stream: a player keeps an empty folder until it restarts.
  const on = document.getElementById("streamEnabled").checked;
  ["upnpNameRow"].forEach((id) => {
    document.getElementById(id).hidden = !on;
  });
  document.getElementById("upnpNeedsStream").hidden = on;
}

let snapcastState = null;

