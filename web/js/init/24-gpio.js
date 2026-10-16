function closeGpioPicker() {
  gpioDetectAbort = true;
  document.getElementById("gpioOverlay").hidden = true;
}

async function openGpioPicker(input) {
  gpioTargetInput = input;
  gpioDetectAbort = false;
  const status = document.getElementById("gpioStatus");
  status.textContent = "";
  const data = await loadPinout();
  if (!data) {
    showToast(t("common.failed"), t("gpio.load_failed"), { error: true });
    return;
  }
  renderPinHeader(data);

  document.getElementById("gpioDetectBtn").hidden = !data.supported;
  document.getElementById("gpioOverlay").hidden = false;
}

document.querySelectorAll(".pin-pick-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    openGpioPicker(document.getElementById(btn.dataset.pinTarget));
  });
});

document.getElementById("gpioCancel").addEventListener("click", closeGpioPicker);
document.getElementById("gpioOverlay").addEventListener("click", (e) => {
  if (e.target === e.currentTarget) closeGpioPicker();
});

document.getElementById("gpioDetectBtn").addEventListener("click", async () => {
  const btn = document.getElementById("gpioDetectBtn");
  const status = document.getElementById("gpioStatus");
  if (btn.disabled) return;
  btn.disabled = true;
  status.textContent = t("gpio.detect_waiting");
  try {
    const result = await apiPost("/api/gpio/detect");

    if (gpioDetectAbort) return;
    if (result.ok && result.data) {
      status.textContent = t("gpio.detect_found", {
        bcm: result.data.bcm, physical: result.data.physical,
      });
      selectPin(result.data.bcm);
      return;
    }
    const messages = {
      timeout: t("gpio.detect_timeout"),
      not_supported: t("gpio.detect_unsupported"),
      detect_in_progress: t("gpio.detect_busy"),
      no_pins_available: t("gpio.detect_no_pins"),
    };
    status.textContent = messages[result.error] || t("common.failed_prefix", { error: errorLabel(result.error) });
  } finally {
    btn.disabled = false;
  }
});

const guestAccessForm = document.getElementById("guestAccessForm");

guestAccessForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const updates = {};
  guestAccessForm.querySelectorAll("[data-key]").forEach((el) => {
    updates[el.dataset.key] = collectFieldValue(el);
  });
  const result = await apiPost("/api/settings", updates);
  if (result.ok) {
    Object.assign(settingsBaseline, updates);
    showToast(t("alert.settings_saved"));
    refreshSuggestions();
  } else {
    showError(result.error);
  }
});

const gpioResetForm = document.getElementById("gpioResetForm");

gpioResetForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const updates = {};
  gpioResetForm.querySelectorAll("[data-key]").forEach((el) => {
    updates[el.dataset.key] = collectFieldValue(el);
  });
  const result = await apiPost("/api/settings", updates);
  if (result.ok) showToast(t("security.gpio_saved"));
  else showError(result.error);
  if (result.ok) {
    gpioPinout = null;
    Object.assign(settingsBaseline, updates);
  }
});

function selectionBox(target, key) {
  const box = document.createElement("input");
  box.type = "checkbox";
  box.className = "row-select";
  box.dataset.target = target;
  box.dataset.key = String(key);
  box.addEventListener("change", updateDeleteButtons);

  queueMicrotask(() => {
    const row = box.parentElement;
    const text = row ? row.textContent.replace(/\s+/g, " ").trim() : "";
    if (text) box.setAttribute("aria-label", text.slice(0, 120));
  });
  return box;
}

function selectionSpacer() {
  const span = document.createElement("span");
  span.className = "row-select-spacer";
  return span;
}

function selectedIn(container) {
  return [...container.querySelectorAll(".row-select:checked")];
}

function updateSelectButtons() {
  document.querySelectorAll("[data-select-scope]").forEach((btn) => {
    const container = document.getElementById(btn.dataset.selectScope);
    if (!container) return;
    const boxes = [...container.querySelectorAll(".row-select")];
    const all = boxes.length > 0 && boxes.every((b) => b.checked);
    btn.disabled = boxes.length === 0;
    btn.dataset.i18n = all ? "stats.deselect_all" : "stats.select_all";
    btn.textContent = t(btn.dataset.i18n);
  });
}

const REMOTE_SCOPE_OF = { topsBox: "played", errorsBox: "errors", sessionList: "sessions", eventList: "events" };
const remoteTotals = { played: 0, errors: 0, sessions: 0, events: 0 };
const remoteSelected = { topsBox: false, errorsBox: false, sessionList: false, eventList: false };

function remoteTotalFor(containerId) {
  return remoteTotals[REMOTE_SCOPE_OF[containerId]] || 0;
}

function updateRemoteOffer(containerId) {
  const line = document.getElementById("selectRemote-" + containerId);
  const container = document.getElementById(containerId);
  if (!line || !container) return;
  const boxes = [...container.querySelectorAll(".row-select")];
  const allTicked = boxes.length > 0 && boxes.every((b) => b.checked);
  const total = remoteTotalFor(containerId);
  if (!allTicked || total <= boxes.length) {
    remoteSelected[containerId] = false;
    line.hidden = true;
    return;
  }
  line.hidden = false;
  const text = line.querySelector(".select-remote-text");
  const link = line.querySelector("[data-remote-scope]");
  if (remoteSelected[containerId]) {
    text.textContent = t("stats.all_n_selected", { n: total });
    link.textContent = t("stats.clear_selection");
  } else {
    text.textContent = t("stats.shown_selected", { n: boxes.length });
    link.textContent = t("stats.select_all_n", { n: total });
  }
}

document.querySelectorAll("[data-remote-scope]").forEach((link) => {
  link.addEventListener("click", () => {
    const id = link.dataset.remoteScope;
    if (remoteSelected[id]) {
      remoteSelected[id] = false;
      document.getElementById(id).querySelectorAll(".row-select").forEach((b) => { b.checked = false; });
    } else {
      remoteSelected[id] = true;
    }
    updateDeleteButtons();
  });
});

function updateDeleteButtons() {
  Object.keys(REMOTE_SCOPE_OF).forEach(updateRemoteOffer);
  document.querySelectorAll("[data-delete-scope]").forEach((btn) => {
    const id = btn.dataset.deleteScope;
    const container = document.getElementById(id);
    if (!container) return;
    const n = remoteSelected[id] ? remoteTotalFor(id) : selectedIn(container).length;
    btn.disabled = n === 0;
    btn.textContent = n ? t("stats.delete_n", { n }) : t("stats.delete_selected");
  });
  updateSelectButtons();
}

document.querySelectorAll("[data-select-scope]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const container = document.getElementById(btn.dataset.selectScope);
    if (!container) return;
    const boxes = [...container.querySelectorAll(".row-select")];
    const all = boxes.length > 0 && boxes.every((b) => b.checked);
    boxes.forEach((b) => { b.checked = !all; });
    updateDeleteButtons();
  });
});

async function deleteAllOf(btn, containerId) {
  const total = remoteTotalFor(containerId);
  if (!(await showConfirm(t("stats.confirm_delete", { n: total })))) return;
  btn.disabled = true;
  const body = { all: true, scope: REMOTE_SCOPE_OF[containerId] };
  if (containerId === "eventList") body.type = document.getElementById("eventTypeFilter").value || null;
  const result = await apiPost("/api/journal/delete", body);
  remoteSelected[containerId] = false;
  if (!result.ok) {
    showError(result.error);
    updateDeleteButtons();
    return;
  }
  showToast(t("stats.deleted"), t("stats.deleted_n", { n: (result.data && result.data.removed) || 0 }));
  if (containerId === "eventList") {
    oldestEventId = null;
    await loadEvents(false);
  }
  await refreshStats(true);
  updateDeleteButtons();
}

async function deleteSelected(btn) {
  const container = document.getElementById(btn.dataset.deleteScope);
  if (remoteSelected[btn.dataset.deleteScope]) {
    await deleteAllOf(btn, btn.dataset.deleteScope);
    return;
  }
  const boxes = selectedIn(container);
  if (!boxes.length) return;

  const target = boxes[0].dataset.target;
  const keys = boxes.map((b) => (target === "items" ? JSON.parse(b.dataset.key) : b.dataset.key));

  if (!(await showConfirm(t("stats.confirm_delete", { n: keys.length })))) return;

  btn.disabled = true;
  const result = await apiPost("/api/journal/delete", { target, keys });
  if (!result.ok) {
    showError(result.error);
    updateDeleteButtons();
    return;
  }
  const removed = (result.data && result.data.removed) || 0;

  if (removed < keys.length) {
    showToast(t("stats.deleted"), t("stats.delete_partial", { removed, asked: keys.length }));
  }

  if (target === "events") {
    oldestEventId = null;
    await loadEvents(false);
  } else {
    await refreshStats(true);
  }
  updateDeleteButtons();
}

document.querySelectorAll("[data-delete-scope]").forEach((btn) => {
  btn.addEventListener("click", () => deleteSelected(btn));
});
updateDeleteButtons();

