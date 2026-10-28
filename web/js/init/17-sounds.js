async function refreshSystemSounds() {
  const result = await apiGet("/api/system_sounds");
  const list = document.getElementById("systemSoundsList");
  if (!result.ok || !Array.isArray(result.data)) return;
  await refreshAnnouncementVolumes();
  list.innerHTML = "";
  result.data.forEach((item) => {
    // Not one of the sounds but what comes before each: a switch of the card, not an entry.
    if (item.key === "SIGNATURE_SOUND") {
      document.getElementById("systemSignatureRow").hidden = false;
      document.getElementById("systemSignatureOn").checked = !item.off;
      document.getElementById("systemSignatureListen").disabled = item.off || !item.exists;
      return;
    }
    const li = document.createElement("li");
    li.className = "ann-item sys-item";
    li.dataset.key = item.key;
    const open = item.key === openSystemSoundKey;
    if (open) li.classList.add("is-open");

    const bodyId = "sys-sound-" + item.key;
    const head = document.createElement("button");
    head.type = "button";
    head.className = "ann-head";
    head.setAttribute("aria-expanded", open ? "true" : "false");
    head.setAttribute("aria-controls", bodyId);

    const text = document.createElement("span");
    text.className = "ann-text";
    const title = document.createElement("span");
    title.className = "ann-name";
    title.textContent = t("syssounds." + item.key);
    const state = document.createElement("span");
    state.className = "ann-meta sound-state";
    state.textContent = item.off ? t("syssounds.off")
      : item.custom ? t("syssounds.custom", { name: item.name })
      : t("syssounds.default");
    text.append(title, state);
    const chevron = document.createElement("span");
    chevron.className = "ann-chevron";
    chevron.dataset.icon = "chevron";
    chevron.setAttribute("aria-hidden", "true");
    head.append(text, chevron);
    head.addEventListener("click", () => {
      setSystemSoundOpen(li.classList.contains("is-open") ? null : item.key);
    });

    const body = document.createElement("div");
    body.className = "ann-body";
    body.id = bodyId;
    body.hidden = !open;
    if (!item.off) body.appendChild(volumeControl(item.key, { inline: true }));

    const actions = document.createElement("span");
    actions.className = "device-actions";

    const listen = document.createElement("button");
    listen.type = "button";
    listen.className = "btn";
    listen.dataset.icon = "play";
    listen.textContent = t("syssounds.listen");
    listen.disabled = !item.exists;
    listen.addEventListener("click", async () => {
      const r = await apiPost("/api/system_sounds/" + item.key + "/test");
      if (!r.ok) showError(r.error);
    });

    const replace = document.createElement("button");
    replace.type = "button";
    replace.className = "btn";
    replace.dataset.icon = "upload";
    replace.textContent = t("syssounds.replace");
    replace.addEventListener("click", () => {
      systemSoundTarget = item.key;
      systemSoundInput.click();
    });

    const restore = document.createElement("button");
    restore.type = "button";
    restore.className = "btn btn-warning-outline";
    restore.dataset.icon = "restart";
    restore.textContent = t("syssounds.restore");

    const off = document.createElement("button");
    off.type = "button";
    off.className = "btn";
    off.dataset.icon = "x";
    off.textContent = t("syssounds.disable");
    off.addEventListener("click", async () => {
      const r = await apiPost("/api/system_sounds/" + item.key + "/off");
      if (!r.ok) {
        showError(r.error);
        return;
      }
      showToast(t("syssounds.disabled", { name: t("syssounds." + item.key) }));
      refreshSystemSounds();
    });
    restore.addEventListener("click", async () => {
      const r = await apiDelete("/api/system_sounds/" + item.key);
      if (!r.ok) {
        showError(r.error);
        return;
      }
      showToast(t("syssounds.restored", { name: t("syssounds." + item.key) }));
      refreshSystemSounds();
    });

    const right = [];
    // Both write into the music folder, read-only in some containers.
    if (canDo("media_upload")) {
      if (item.custom || item.off) right.push(restore);
      if (!item.off) right.push(off);
      splitActions(actions, [listen, replace], right);
    } else {
      splitActions(actions, [listen], item.off ? [] : [off]);
    }
    body.appendChild(actions);
    li.append(head, body);
    list.appendChild(li);
  });
}

document.getElementById("systemSignatureOn").addEventListener("change", async (event) => {
  // On is the original sound back; off leaves the sounds without it.
  const r = event.target.checked ? await apiDelete("/api/system_sounds/SIGNATURE_SOUND")
    : await apiPost("/api/system_sounds/SIGNATURE_SOUND/off");
  if (!r.ok) showError(r.error);
  refreshSystemSounds();
});

document.getElementById("systemSignatureListen").addEventListener("click", async () => {
  const r = await apiPost("/api/system_sounds/SIGNATURE_SOUND/test");
  if (!r.ok) showError(r.error);
});

systemSoundInput.addEventListener("change", async (event) => {
  const file = (event.target.files || [])[0];
  event.target.value = "";
  const key = systemSoundTarget;
  if (!file || !key) return;
  const body = new FormData();
  body.append("file", file, file.name);
  const r = await apiFetch("/api/system_sounds/" + key, { method: "POST", body });
  if (!r.ok) {
    showToast(t("common.failed"), errorLabel(r.error), { error: true });
    return;
  }
  showToast(t("syssounds.replaced", { name: t("syssounds." + key) }));
  refreshSystemSounds();
});

refreshSystemSounds();
window.LANG_CHANGE_LISTENERS.push(refreshSystemSounds);

async function deleteAnnounceFile(name) {
  if (!(await showConfirm(t("annfiles.confirm_delete", { name })))) return;
  const source = trackOrderSourceSelect.value;
  const result = await apiDelete("/api/announce_files/" + encodeURIComponent(source) +
                                 "?name=" + encodeURIComponent(name));
  if (result.ok) showToast(t("annfiles.deleted", { name }));
  else showError(result.error);
  await loadTrackOrder();
  refreshAnnouncements();
}

function fillPickerNotes() {
  const touch = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
  document.querySelectorAll(".picker-note").forEach((note) => {
    note.hidden = !touch;
    let question = note.querySelector(".picker-q");
    let answer = note.querySelector(".picker-more");
    if (!question) {
      question = document.createElement("button");
      question.type = "button";
      question.className = "btn-link picker-q";
      question.setAttribute("aria-expanded", "false");
      answer = document.createElement("span");
      answer.className = "picker-more";
      answer.hidden = true;
      question.addEventListener("click", () => {
        const open = answer.hidden;
        answer.hidden = !open;
        question.setAttribute("aria-expanded", open ? "true" : "false");
      });
      note.append(question, answer);
    }
    question.textContent = t("files.portal_q");

    const parts = t(note.dataset.note || "files.portal_note", { url: "\u0000" }).split("\u0000");
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "copy-chip";
    copy.dataset.icon = "copy";
    copy.textContent = location.origin;
    copy.title = t("files.copy");
    copy.setAttribute("aria-label", t("files.copy") + " " + location.origin);
    copy.addEventListener("click", () => copyText(location.origin, copy));
    answer.replaceChildren(parts[0] || "", copy, parts[1] || "");
  });
}

async function copyText(text, element) {
  let done = false;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      done = true;
    }
  } catch (e) {  }
  if (!done) {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    try { done = document.execCommand("copy"); } catch (e) { done = false; }
    area.remove();
  }
  if (done) {
    showToast(t("files.copied"), text);
  } else if (element) {
    const range = document.createRange();
    range.selectNodeContents(element);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    showToast(t("files.copy_manual"));
  }
}
fillPickerNotes();
window.LANG_CHANGE_LISTENERS.push(fillPickerNotes);

document.getElementById("btnAnnFilesAdd").addEventListener("click", () => {
  document.getElementById("annFilesInput").click();
});
document.getElementById("annFilesInput").addEventListener("change", (event) => {
  const files = Array.prototype.slice.call(event.target.files || []);

  event.target.value = "";
  if (files.length) uploadAnnounceFiles(files);
});

let trackOrderShownSource = null;
function paintAnnounceVolumeRow(source) {
  const holder = document.getElementById("announceVolumeHolder");
  if (!holder) return;
  holder.innerHTML = "";
  holder.appendChild(volumeControl(source, { hint: true }));
}
window.LANG_CHANGE_LISTENERS.push(() => paintAnnounceVolumeRow(trackOrderSourceSelect.value));

async function loadTrackOrder() {
  const source = trackOrderSourceSelect.value;
  if (source !== trackOrderShownSource) {
    trackOrderSelected = new Set();
    trackOrderShownSource = source;
  }
  await refreshAnnouncementVolumes();
  paintAnnounceVolumeRow(source);
  const hint = document.getElementById("trackOrderHint");
  hint.textContent = "";
  const result = await apiGet("/api/track_order/" + encodeURIComponent(source));

  if (source !== trackOrderSourceSelect.value) return;
  const folderLine = document.getElementById("annFilesFolder");
  folderLine.textContent = result.ok && result.data && result.data.folder
    ? t("annfiles.folder", { folder: result.data.folder }) : "";
  if (!result.ok || !result.data || !Array.isArray(result.data.order)) {
    trackOrderCurrent = [];
    renderTrackOrderList();
    hint.textContent = t("trackorder.load_failed", { error: errorLabel(result.error) });
    return;
  }
  trackOrderCurrent = result.data.order.slice();
  renderTrackOrderList();
}

trackOrderSourceSelect.addEventListener("change", loadTrackOrder);

document.getElementById("btnTrackOrderSave").addEventListener("click", async () => {
  const source = trackOrderSourceSelect.value;
  const result = await apiPost("/api/track_order/" + encodeURIComponent(source), { order: trackOrderCurrent });
  if (result.ok) showToast(t("trackorder.saved"));
  else showToast(t("common.failed"), errorLabel(result.error), { error: true });
});

document.getElementById("btnTrackOrderReset").addEventListener("click", async () => {
  if (!(await showConfirm(t("trackorder.confirm_reset")))) return;
  const source = trackOrderSourceSelect.value;
  const result = await apiDelete("/api/track_order/" + encodeURIComponent(source));
  if (!result.ok) {
    showToast(t("common.failed"), errorLabel(result.error), { error: true });
    return;
  }
  loadTrackOrder();
  showToast(t("trackorder.reset_done"));
});

loadTrackOrder();

/* The introductions mirror the music folder: a browser of that tree. */
const PREPARED = {
  intros: { list: "introsList", path: "introsPath", count: "introsCount", hint: "introsHint",
            add: "introsAdd", input: "introsInput", up: "introsUp", folder: "introsNewFolder",
            progress: "introsProgress", counts: "/api/dj_announcements",
            countKey: "settings.dj_files_count", empty: "prepared.empty_files",
            emptyAdvice: "prepared.empty_files_advice",
            picture: false, icon: "audio-lines" },
};
const preparedPath = { intros: "" };
const preparedParent = { intros: null };
const preparedLoaded = { intros: false };

function preparedFull(kind, name) {
  return preparedPath[kind] ? preparedPath[kind] + "/" + name : name;
}

function preparedFileUrl(kind, path) {
  return "/api/prepared/" + kind + "/file?path=" + encodeURIComponent(path);
}

function openPrepared(kind, path) {
  preparedPath[kind] = path || "";
  loadPrepared(kind);
}

async function loadPrepared(kind) {
  const result = await apiGet("/api/prepared/" + kind + "?path=" +
                              encodeURIComponent(preparedPath[kind]));
  if (!result.ok || !result.data) return;
  if (result.data.path !== preparedPath[kind]) return;
  preparedLoaded[kind] = true;
  paintPrepared(kind, result.data);
  loadPreparedCount(kind);
}

async function loadPreparedCount(kind) {
  const spec = PREPARED[kind];
  const result = await apiGet(spec.counts);
  if (!result.ok || !result.data) return;
  const d = result.data;
  document.getElementById(spec.count).textContent =
    t(spec.countKey, { files: d.files, covered: d.covered, tracks: d.tracks });
  document.getElementById(spec.add).disabled = !d.dir;
}

function paintPrepared(kind, data) {
  const spec = PREPARED[kind];
  preparedParent[kind] = data.parent;
  document.getElementById(spec.up).disabled = data.parent === null;
  paintPreparedPath(kind, data);
  const list = document.getElementById(spec.list);
  list.replaceChildren();
  data.dirs.forEach((dir) => list.append(preparedDirRow(kind, dir.name)));
  data.files.forEach((file) => list.append(preparedFileRow(kind, file)));
  const empty = data.missing || (!data.dirs.length && !data.files.length);
  const hint = document.getElementById(spec.hint);
  hint.replaceChildren();
  if (empty) {
    // Two lines, both centred: the plain statement, then the advice under it.
    const lead = document.createElement("span");
    lead.className = "prepared-empty";
    lead.textContent = t(spec.empty);
    const advice = document.createElement("span");
    advice.className = "prepared-empty-advice";
    advice.textContent = t(spec.emptyAdvice);
    hint.append(lead, advice);
  }
}

function paintPreparedPath(kind, data) {
  const box = document.getElementById(PREPARED[kind].path);
  box.replaceChildren();
  // Only what is below the prepared folder gets a crumb: its own name is what
  // the card's title and the guide already say, and an empty level has nothing.
  const parts = String(data.path || "").split("/").filter(Boolean);
  box.hidden = !parts.length;
  parts.forEach((name, index) => {
    if (index) {
      const sep = document.createElement("span");
      sep.className = "prepared-sep";
      sep.textContent = "›";
      box.append(sep);
    }
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "prepared-crumb" + (index === parts.length - 1 ? " is-here" : "");
    btn.textContent = name;
    if (index === parts.length - 1) btn.disabled = true;
    else btn.addEventListener("click", () => openPrepared(kind, parts.slice(0, index + 1).join("/")));
    box.append(btn);
  });
}

function preparedDirRow(kind, name) {
  const row = document.createElement("li");
  row.className = "prepared-row is-dir";
  const open = document.createElement("button");
  open.type = "button";
  open.className = "prepared-open";
  open.dataset.icon = "folder";
  const label = document.createElement("span");
  label.textContent = name;
  open.append(label);
  open.addEventListener("click", () => openPrepared(kind, preparedFull(kind, name)));
  row.append(open);
  return row;
}

function preparedFileRow(kind, file) {
  const spec = PREPARED[kind];
  const path = preparedFull(kind, file.name);
  const row = document.createElement("li");
  row.className = "prepared-row" + (file.used ? "" : " is-unused");

  const art = document.createElement("span");
  art.className = "prepared-art";
  if (!file.used) {
    art.dataset.icon = "file";
  } else if (spec.picture) {
    art.dataset.icon = spec.icon;
    const img = document.createElement("img");
    img.loading = "lazy";
    img.alt = "";
    img.src = preparedFileUrl(kind, path);
    img.addEventListener("error", () => img.remove());
    art.append(img);
  } else {
    const play = document.createElement("button");
    play.type = "button";
    play.className = "btn btn-icon prepared-play" + (preparedPreview.path === path ? " is-on" : "");
    play.dataset.icon = preparedPreview.path === path ? "pause" : "play";
    play.title = t("prepared.listen");
    play.setAttribute("aria-label", t("prepared.listen"));
    play.addEventListener("click", () => togglePreparedPreview(kind, path));
    art.append(play);
  }

  const name = document.createElement("span");
  name.className = "prepared-name";
  name.textContent = file.name;
  name.title = file.name;
  if (!file.used) {
    const tag = document.createElement("span");
    tag.className = "prepared-tag";
    tag.textContent = t("prepared.unused");
    name.append(tag);
  }

  const size = document.createElement("span");
  size.className = "prepared-size";
  size.textContent = formatBytes(file.size);

  const drop = document.createElement("button");
  drop.type = "button";
  drop.className = "btn btn-icon btn-danger-outline";
  drop.dataset.icon = "trash";
  drop.title = t("prepared.delete");
  drop.setAttribute("aria-label", t("prepared.delete"));
  drop.addEventListener("click", () => deletePrepared(kind, path, file.name));

  row.append(art, name, size, drop);
  return row;
}

const preparedPreview = { audio: new Audio(), path: null };
preparedPreview.audio.addEventListener("ended", () => {
  preparedPreview.path = null;
  if (preparedLoaded.intros) loadPrepared("intros");
});

function togglePreparedPreview(kind, path) {
  const audio = preparedPreview.audio;
  if (preparedPreview.path === path && !audio.paused) {
    audio.pause();
    preparedPreview.path = null;
  } else {
    audio.src = preparedFileUrl(kind, path);
    preparedPreview.path = path;
    audio.play().catch(() => {
      preparedPreview.path = null;
      showError("preview_failed");
    });
  }
  loadPrepared(kind);
}

async function uploadPrepared(kind, files) {
  const spec = PREPARED[kind];
  const btn = document.getElementById(spec.add);
  const progress = document.getElementById(spec.progress);
  btn.disabled = true;
  progress.hidden = false;
  let added = 0;
  const failed = [];
  for (let i = 0; i < files.length; i++) {
    const file = files[i];
    progress.textContent = t("prepared.sending", { n: i + 1, total: files.length, name: file.name });
    navTransfer.show(kind, i, files.length, file.name);
    const body = new FormData();
    body.append("path", preparedPath[kind]);
    body.append("name", file.name);
    body.append("mtime", String(Math.round(file.lastModified / 1000)));
    body.append("file", file, file.name);
    const result = await apiFetch("/api/prepared/" + kind + "/file", { method: "POST", body });
    if (result.ok) added += 1;
    else failed.push({ name: file.name, error: result.error });
  }
  progress.hidden = true;
  navTransfer.finish(kind, !failed.length,
                     t(failed.length ? "transfer.failed" : "transfer.done",
                       { sent: added, failed: failed.length }));
  btn.disabled = false;
  await loadPrepared(kind);
  if (!failed.length) {
    showToast(t("prepared.added", { count: added }));
  } else {
    showToast(t("prepared.some_failed", { count: failed.length, total: files.length }),
              failed[0].name + " : " + errorLabel(failed[0].error), { error: true });
  }
}

async function deletePrepared(kind, path, name) {
  if (!(await showConfirm(t("prepared.delete_confirm", { name })))) return;
  const result = await apiDelete(preparedFileUrl(kind, path));
  if (!result.ok) {
    showToast(t("common.failed"), errorLabel(result.error), { error: true });
    return;
  }
  showToast(t("prepared.deleted", { name }));
  if (preparedPreview.path === path) {
    preparedPreview.audio.pause();
    preparedPreview.path = null;
  }
  await loadPrepared(kind);
}

async function askPreparedFolderName(kind) {
  const box = document.createElement("div");
  box.className = "prepared-dialog";
  const hint = document.createElement("p");
  hint.className = "hint";
  hint.textContent = t("prepared.new_folder_hint");
  const input = document.createElement("input");
  input.type = "text";
  input.className = "text-input";
  input.maxLength = 80;
  input.placeholder = t("prepared.new_folder_placeholder");
  input.setAttribute("aria-label", t("prepared.new_folder"));
  box.append(hint, input);
  const answer = openModal({ title: t("prepared.new_folder"), bodyNode: box, confirm: true });
  input.focus();
  if (!(await answer)) return;
  const name = input.value.trim();
  if (!name) return;
  const result = await apiPost("/api/prepared/" + kind + "/folder",
                               { path: preparedPath[kind], name: name });
  if (!result.ok) {
    showToast(t("common.failed"), errorLabel(result.error), { error: true });
    return;
  }
  showToast(t("prepared.folder_created", { name: name }));
  openPrepared(kind, result.data.path);
}

Object.keys(PREPARED).forEach((kind) => {
  const spec = PREPARED[kind];
  const input = document.getElementById(spec.input);
  document.getElementById(spec.add).addEventListener("click", () => input.click());
  input.addEventListener("change", (event) => {
    const files = Array.prototype.slice.call(event.target.files || []);
    event.target.value = "";
    if (files.length) uploadPrepared(kind, files);
  });
  document.getElementById(spec.up).addEventListener("click", () => {
    if (preparedParent[kind] !== null) openPrepared(kind, preparedParent[kind]);
  });
  document.getElementById(spec.folder).addEventListener("click", () => askPreparedFolderName(kind));
});

window.LANG_CHANGE_LISTENERS.push(() => {
  Object.keys(PREPARED).forEach((kind) => {
    if (preparedLoaded[kind]) loadPrepared(kind);
  });
});

function deviceUtcString(now) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${now.getUTCFullYear()}-${pad(now.getUTCMonth() + 1)}-${pad(now.getUTCDate())} ` +
    `${pad(now.getUTCHours())}:${pad(now.getUTCMinutes())}:${pad(now.getUTCSeconds())}`;
}

function clockWriteDetail(result) {
  const data = result.data || {};
  const rtc = data.written_to_rtc ? "clock.rtc_written" : data.has_rtc ? "clock.rtc_failed" : "clock.rtc_none";
  return [data.system_time, t(rtc)]
    .filter(Boolean).join(" · ");
}

document.getElementById("btnUseDeviceTime").addEventListener("click", async () => {
  const btn = document.getElementById("btnUseDeviceTime");
  const now = new Date();
  const utc = deviceUtcString(now);

  const pad = (n) => String(n).padStart(2, "0");
  document.getElementById("timeInput").value =
    `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}` +
    `T${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
  btn.disabled = true;
  const result = await apiPost("/api/time", { utc });
  btn.disabled = false;
  if (!result.ok) {
    showError(result.error, t("clock.sync_btn"));
    return;
  }
  showToast(t("clock.device_time_set"), clockWriteDetail(result));

  refreshStatus();
});

document.getElementById("manualTimeSection").addEventListener("toggle", (event) => {
  const input = document.getElementById("timeInput");
  if (!event.target.open || input.value) return;
  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  input.value = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}` +
    `T${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
});

document.getElementById("timeForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const raw = document.getElementById("timeInput").value;
  if (!raw) return;
  const formatted = raw.replace("T", " ");
  const result = await apiPost("/api/time", { datetime: formatted });
  if (result.ok) showToast(t("clock.time_set"), clockWriteDetail(result));
  else showError(result.error);
});

document.getElementById("btnSaveBtClock").addEventListener("click", async () => {
  const updates = collectChangedSettings(document.getElementById("btClockMac").closest(".subsection"));
  if (Object.keys(updates).length === 0) {
    showToast(t("alert.no_changes"));
    return;
  }

  if (updates["clock.bluetooth_mac"] && !isCompleteMac(updates["clock.bluetooth_mac"])) {
    showToast(t("clock.mac_incomplete"));
    return;
  }
  const result = await apiPost("/api/settings", updates);
  if (result.ok) Object.assign(settingsBaseline, updates);
  if (result.ok) showToast(t("alert.bt_saved")); else showError(result.error);
});

document.getElementById("btnTestBtClock").addEventListener("click", async () => {
  const btn = document.getElementById("btnTestBtClock");
  const mac = btClockMac.value.trim();
  if (!mac) {
    showToast(t("clock.enter_mac_first"));
    return;
  }
  if (!isCompleteMac(mac)) {
    showToast(t("clock.mac_incomplete"));
    return;
  }
  if (btn.disabled) return;

  const setBusy = (busy) => {
    btn.disabled = busy;
    btn.dataset.i18n = busy ? "clock.testing" : "clock.test_device";
    btn.textContent = t(btn.dataset.i18n);
  };
  setBusy(true);

  showToast(t("clock.test_duration"));
  try {
    const result = await apiPost("/api/clock/test_bt", { mac });
    if (result.ok) showToast(t("clock.time_recovered", { time: result.time }));
    else showToast(t("common.failed"), errorLabel(result.error), { error: true });
  } finally {
    setBusy(false);
  }
});

let timezonesLoaded = false;
let timezoneCurrent = null;
let timezoneShown = null;
const UNSET_ZONES = ["", "n/a", "Factory"];

function browserTimeZone() {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch (e) { return ""; }
}

