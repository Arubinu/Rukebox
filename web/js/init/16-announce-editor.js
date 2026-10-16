function setAnnRepeat(times) {
  const n = Number(times);
  annRepeat.value = n === 0 ? "forever" : n === 1 ? "once" : "count";
  document.getElementById("annRepeatCount").value = String(n > 1 ? n : 3);
}
function annRepeatTimes() {
  if (annRepeat.value === "forever") return 0;
  if (annRepeat.value === "once") return 1;
  return Math.max(2, Number(document.getElementById("annRepeatCount").value) || 2);
}

function setAnnouncementFormMode(item) {
  editingAnnouncementId = item ? item.id : null;
  const title = document.getElementById("announcementFormTitle");
  const submit = document.getElementById("annSubmit");
  if (item) {
    title.dataset.i18n = "announcements.edit_section";
    title.dataset.i18nVarName = item.name;
    title.textContent = t("announcements.edit_section", { name: item.name });
    submit.dataset.i18n = "common.save";
    submit.dataset.icon = "check";
    submit.textContent = t("common.save");
  } else {
    title.dataset.i18n = "announcements.add_section";
    delete title.dataset.i18nVarName;
    title.textContent = t("announcements.add_section");
    submit.dataset.i18n = "common.add";
    submit.dataset.icon = "plus";
    submit.textContent = t("common.add");
  }
  document.getElementById("annEditCancel").hidden = !item;
}

function paintAnnFormVolume(key) {
  const row = document.getElementById("annVolumeRow");
  row.hidden = !key;
  row.innerHTML = "";
  if (key) row.appendChild(volumeControl(key, { hint: true }));
}

function resetAnnouncementForm() {
  document.getElementById("announcementForm").reset();
  document.getElementById("annTime").value = "12:00";
  annTrigger.value = "time";
  document.getElementById("annDelay").value = "30";
  setAnnRepeat(1);
  document.getElementById("annAutoChance").value = "1/1";
  document.getElementById("annManualChance").value = "1/1";
  document.getElementById("annAfter").value = "none";
  document.getElementById("annSpeech").value = "none";
  updateAnnTimeVisibility();
  paintAnnFormVolume(null);
  setAnnouncementFormMode(null);
}

function startEditAnnouncement(item) {
  document.getElementById("annName").value = item.name;
  document.getElementById("annFolder").value = item.folder || "";
  document.getElementById("annTime").value =
    String(item.hour).padStart(2, "0") + ":" + String(item.minute).padStart(2, "0");
  annTrigger.value = item.trigger || "time";
  document.getElementById("annDelay").value = String(item.delay_min || 30);
  setAnnRepeat(item.repeat_times === undefined ? 1 : item.repeat_times);
  document.getElementById("annAutoChance").value = item.auto_chance || "1/1";
  document.getElementById("annManualChance").value = item.manual_chance || "1/1";
  document.getElementById("annAfter").value = item.after_action || "none";
  document.getElementById("annSpeech").value = item.speech || "none";
  updateAnnTimeVisibility();
  paintAnnFormVolume("custom:" + item.id);
  setAnnouncementFormMode(item);
  const section = document.getElementById("announcementFormSection");
  section.open = true;
  section.scrollIntoView({ behavior: "smooth", block: "start" });
  document.getElementById("annName").focus({ preventScroll: true });
}

/* A copy is a new announcement on the same folder: the same sounds, at another time. */
function startDuplicateAnnouncement(item) {
  startEditAnnouncement(Object.assign({}, item,
    { name: t("common.copy_name", { name: item.name }).slice(0, 80) }));
  paintAnnFormVolume(null);
  setAnnouncementFormMode(null);
}

function returnToAnnouncement(id) {
  document.getElementById("announcementFormSection").open = false;
  if (!id) return;
  setAnnouncementOpen(id);
  const row = document.querySelector('#announcementList .ann-item[data-id="' + CSS.escape(id) + '"]');
  if (!row) return;
  const reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  row.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "center" });
  const head = row.querySelector(".ann-head");
  if (head) head.focus({ preventScroll: true });
}

document.getElementById("annEditCancel").addEventListener("click", () => {
  const id = editingAnnouncementId;
  resetAnnouncementForm();
  returnToAnnouncement(id);
});

const ANNOUNCE_CHANCES = ["1/1", "3/4", "2/3", "1/2", "1/3", "1/4", "1/5", "1/7", "1/10"];
function chanceLabel(ratio) {
  if (!ratio || ratio === "1/1") return t("chance.always");
  const [k, n] = ratio.split("/");
  return t(k === "1" ? "chance.one_in" : "chance.k_in", { k, n });
}
function fillChanceSelects() {
  document.querySelectorAll(".chance-select").forEach((select) => {
    const current = select.value || "1/1";
    select.innerHTML = "";
    ANNOUNCE_CHANCES.forEach((ratio) => {
      const opt = document.createElement("option");
      opt.value = ratio;
      opt.textContent = chanceLabel(ratio);
      select.appendChild(opt);
    });
    select.value = current;
  });
}
fillChanceSelects();
window.LANG_CHANGE_LISTENERS.push(fillChanceSelects);

document.getElementById("announcementForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const [hour, minute] = timeFieldParts(document.getElementById("annTime"));
  const body = {
    name: document.getElementById("annName").value,
    folder: document.getElementById("annFolder").value,
    hour: Number(hour),
    minute: Number(minute),
    trigger: annTrigger.value,
    delay_min: Number(document.getElementById("annDelay").value),
    repeat_times: annRepeatTimes(),
    auto_chance: document.getElementById("annAutoChance").value,
    manual_chance: document.getElementById("annManualChance").value,
    after_action: annTrigger.value === "manual" ? "none" : document.getElementById("annAfter").value,
    speech: document.getElementById("annSpeech").value,
  };
  if (editingAnnouncementId) {
    if (!body.folder.trim()) delete body.folder;
    const r = await apiPost("/api/announcements/" + encodeURIComponent(editingAnnouncementId), body);
    if (!r.ok) {
      showError(r.error);
      return;
    }
    const id = editingAnnouncementId;
    resetAnnouncementForm();
    await refreshAnnouncements();
    returnToAnnouncement(id);
    showToast(t("announcements.updated"));
    return;
  }
  const result = await apiPost("/api/announcements", body);
  if (!result.ok) {
    showError(result.error);
    return;
  }
  resetAnnouncementForm();

  await refreshAnnouncements();
  if (result.data && result.data.id) openAnnounceFiles("custom:" + result.data.id);
  showToast(t("annfiles.created", { name: result.data ? result.data.name : "" }),
            t("annfiles.created_detail"));
});

const trackOrderSourceSelect = document.getElementById("trackOrderSource");
let trackOrderCurrent = [];

let trackOrderSelected = new Set();

function updateFilesBatch() {
  const bar = document.getElementById("annFilesBatch");
  const count = trackOrderCurrent.filter((n) => trackOrderSelected.has(n)).length;
  bar.hidden = trackOrderCurrent.length === 0;
  const all = document.getElementById("annFilesSelectAll");
  all.checked = count > 0 && count === trackOrderCurrent.length;
  all.indeterminate = count > 0 && count < trackOrderCurrent.length;
  const label = document.getElementById("annFilesBatchCount");
  label.dataset.i18n = count ? "annfiles.selected" : "annfiles.select_all";
  label.textContent = count ? t("annfiles.selected", { count }) : t("annfiles.select_all");
  ["annFilesBatchUp", "annFilesBatchDown", "annFilesBatchDelete"].forEach((id) => {
    document.getElementById(id).disabled = count === 0;
  });
}

function moveSelection(direction) {
  const items = trackOrderCurrent;
  const order = direction < 0 ? items.map((_, i) => i) : items.map((_, i) => items.length - 1 - i);
  order.forEach((i) => {
    const j = i + direction;
    if (j < 0 || j >= items.length) return;
    if (trackOrderSelected.has(items[i]) && !trackOrderSelected.has(items[j])) {
      [items[i], items[j]] = [items[j], items[i]];
    }
  });
  renderTrackOrderList();
}

document.getElementById("annFilesSelectAll").addEventListener("change", (e) => {
  trackOrderSelected = e.target.checked ? new Set(trackOrderCurrent) : new Set();
  renderTrackOrderList();
});
document.getElementById("annFilesBatchUp").addEventListener("click", () => moveSelection(-1));
document.getElementById("annFilesBatchDown").addEventListener("click", () => moveSelection(1));
document.getElementById("annFilesBatchDelete").addEventListener("click", async () => {
  const names = trackOrderCurrent.filter((n) => trackOrderSelected.has(n));
  if (!names.length) return;
  if (!(await showConfirm(t("annfiles.confirm_delete_many", { count: names.length })))) return;
  const source = trackOrderSourceSelect.value;
  let failed = 0;
  for (const name of names) {
    const r = await apiDelete("/api/announce_files/" + encodeURIComponent(source) +
                              "?name=" + encodeURIComponent(name));
    if (!r.ok) failed += 1;
  }
  trackOrderSelected = new Set();
  if (failed) showToast(t("common.failed"), t("annfiles.delete_some_failed", { count: failed }), { error: true });
  else showToast(t("annfiles.deleted_many", { count: names.length }));
  await loadTrackOrder();
  refreshAnnouncements();
});

const announcePreview = { audio: new Audio(), name: null };
announcePreview.audio.addEventListener("ended", () => {
  announcePreview.name = null;
  renderTrackOrderList();
});
function toggleAnnouncePreview(name) {
  const audio = announcePreview.audio;
  if (announcePreview.name === name && !audio.paused) {
    audio.pause();
    announcePreview.name = null;
  } else {
    const source = document.getElementById("trackOrderSource").value;
    audio.src = "/api/announce_files/" + encodeURIComponent(source) + "?name=" + encodeURIComponent(name);
    announcePreview.name = name;
    audio.play().catch(() => {
      announcePreview.name = null;
      showError("preview_failed");
      renderTrackOrderList();
    });
  }
  renderTrackOrderList();
}

function renderTrackOrderList() {
  const list = document.getElementById("trackOrderList");
  list.innerHTML = "";
  updateFilesBatch();
  if (!trackOrderCurrent.length) {
    const li = document.createElement("li");
    li.textContent = t("annfiles.empty");
    list.appendChild(li);
    return;
  }
  trackOrderCurrent.forEach((name, i) => {
    const li = document.createElement("li");
    const pick = document.createElement("input");
    pick.type = "checkbox";
    pick.className = "row-select";
    pick.checked = trackOrderSelected.has(name);
    pick.setAttribute("aria-label", name);
    pick.addEventListener("change", () => {
      if (pick.checked) trackOrderSelected.add(name); else trackOrderSelected.delete(name);
      li.classList.toggle("is-selected", pick.checked);
      updateFilesBatch();
    });
    li.classList.toggle("is-selected", pick.checked);
    li.appendChild(pick);
    const label = document.createElement("span");

    label.textContent = name.replace(/\.[a-z0-9]{2,5}$/i, "");
    label.title = name;

    const actions = document.createElement("span");
    actions.className = "device-actions";

    const upBtn = document.createElement("button");
    upBtn.type = "button";
    upBtn.className = "btn btn-icon";
    upBtn.dataset.icon = "arrow-up";
    upBtn.setAttribute("aria-label", t("trackorder.move_up") + " - " + name);
    upBtn.disabled = i === 0;
    upBtn.addEventListener("click", () => {
      [trackOrderCurrent[i - 1], trackOrderCurrent[i]] = [trackOrderCurrent[i], trackOrderCurrent[i - 1]];
      renderTrackOrderList();
    });

    const downBtn = document.createElement("button");
    downBtn.type = "button";
    downBtn.className = "btn btn-icon";
    downBtn.dataset.icon = "arrow-down";
    downBtn.setAttribute("aria-label", t("trackorder.move_down") + " - " + name);
    downBtn.disabled = i === trackOrderCurrent.length - 1;
    downBtn.addEventListener("click", () => {
      [trackOrderCurrent[i], trackOrderCurrent[i + 1]] = [trackOrderCurrent[i + 1], trackOrderCurrent[i]];
      renderTrackOrderList();
    });

    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "btn btn-icon btn-danger-outline";
    delBtn.dataset.icon = "trash";
    delBtn.setAttribute("aria-label", t("annfiles.delete_aria", { name }));
    delBtn.title = t("annfiles.delete_aria", { name });
    delBtn.addEventListener("click", () => deleteAnnounceFile(name));

    const listenBtn = document.createElement("button");
    listenBtn.type = "button";
    listenBtn.className = "btn btn-icon";
    const playing = announcePreview.name === name && !announcePreview.audio.paused;
    listenBtn.dataset.icon = playing ? "stop" : "headphones";
    listenBtn.setAttribute("aria-label", playing ? t("annfiles.stop_listen") : t("annfiles.listen", { name }));
    listenBtn.title = listenBtn.getAttribute("aria-label");
    listenBtn.addEventListener("click", () => toggleAnnouncePreview(name));

    splitActions(actions, [upBtn, downBtn, listenBtn], [delBtn]);
    li.append(label, actions);
    list.appendChild(li);
  });
}

function openAnnounceFiles(source) {
  if ([...trackOrderSourceSelect.options].some((o) => o.value === source)) {
    trackOrderSourceSelect.value = source;
    loadTrackOrder();
  }
  goToCard("settings", "trackOrderTitle");
}

async function uploadAnnounceFiles(files) {
  const source = trackOrderSourceSelect.value;
  const btn = document.getElementById("btnAnnFilesAdd");
  const progress = document.getElementById("annFilesProgress");
  btn.disabled = true;
  trackOrderSourceSelect.disabled = true;
  progress.hidden = false;
  let added = 0;
  const failed = [];
  for (let i = 0; i < files.length; i++) {
    const file = files[i];
    progress.textContent = t("annfiles.sending", { n: i + 1, total: files.length, name: file.name });
    navTransfer.show("announce", i, files.length, file.name);
    const body = new FormData();
    body.append("name", file.name);
    body.append("mtime", String(Math.round(file.lastModified / 1000)));
    body.append("file", file, file.name);
    const result = await apiFetch("/api/announce_files/" + encodeURIComponent(source), { method: "POST", body });
    if (result.ok) added += 1;
    else failed.push({ name: file.name, error: result.error });
  }
  progress.hidden = true;
  navTransfer.finish("announce", !failed.length,
                     t(failed.length ? "transfer.failed" : "transfer.done",
                       { sent: added, failed: failed.length }));
  btn.disabled = false;
  trackOrderSourceSelect.disabled = false;
  await loadTrackOrder();
  refreshAnnouncements();
  if (!failed.length) {
    showToast(t("annfiles.added", { count: added }));
  } else {
    showToast(t("annfiles.some_failed", { count: failed.length, total: files.length }),
              failed[0].name + " : " + errorLabel(failed[0].error), { error: true });
  }
}

const systemSoundInput = document.getElementById("systemSoundInput");
let systemSoundTarget = null;

let openSystemSoundKey = null;

function setSystemSoundOpen(key) {
  openSystemSoundKey = key;
  document.querySelectorAll("#systemSoundsList .sys-item").forEach((li) => {
    const open = li.dataset.key === key;
    li.classList.toggle("is-open", open);
    li.querySelector(".ann-head").setAttribute("aria-expanded", open ? "true" : "false");
    li.querySelector(".ann-body").hidden = !open;
  });
}

