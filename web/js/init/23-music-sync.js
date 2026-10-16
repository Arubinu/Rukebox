const MUSIC_SYNC_EXTENSIONS = [
  "mp3", "opus", "ogg", "oga", "wav", "m4a", "aac", "flac", "wma", "mp4", "webm",

  "lrc", "srt", "vtt", "txt",

  "jpg", "jpeg", "png", "webp",
];

function musicSyncWanted(path) {
  if (path.split("/").some((part) => part.startsWith("."))) return false;
  const dot = path.lastIndexOf(".");
  return dot > 0 && MUSIC_SYNC_EXTENSIONS.includes(path.slice(dot + 1).toLowerCase());
}

function buildMusicPlan(files, quiet) {
  const manifest = musicManifest || {};
  const plan = [];
  let toSend = 0;
  let present = 0;
  let presentBytes = 0;
  let empty = 0;
  files.forEach((file) => {
    const path = musicTargetPath(file);
    if (!path || !musicSyncWanted(path)) return;

    if (file.size === 0) {
      empty += 1;
      return;
    }
    const known = manifest[path];
    const stamp = Math.round(file.lastModified / 1000);

    if (known && known.size === file.size && Math.abs(known.mtime - stamp) <= 2) {
      present += 1;
      presentBytes += file.size;
      return;
    }
    plan.push({ file, path });
    toSend += file.size;
  });
  plan.sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));
  musicQueue = plan;

  const box = document.getElementById("musicPlanBox");
  const line = document.getElementById("musicPlanLine");
  box.hidden = plan.length === 0;
  if (plan.length === 0) {
    if (!quiet) {
      showToast(t("music.up_to_date"), t("music.up_to_date_detail", {
        count: present, size: formatBytes(presentBytes),
      }) + (empty ? " · " + t("music.plan_empty", { n: empty }) : ""));
    }
    return;
  }
  const over = musicManifestFree > 0 && toSend > musicManifestFree;
  line.textContent = t(over ? "music.plan_no_space" : "music.plan_line", {
    count: plan.length,
    size: formatBytes(toSend),
    present: present,
    free: formatBytes(musicManifestFree),
  }) + (empty ? " \u00b7 " + t("music.plan_empty", { n: empty }) : "");
  document.getElementById("btnMusicSync").disabled = over;
  document.getElementById("musicProgressLine").textContent = "";
  document.getElementById("musicProgressBar").style.width = "0%";
}

function updateMusicButtons() {
  document.getElementById("btnMusicFolder").disabled = musicBusy;
  document.getElementById("btnMusicFiles").disabled = musicBusy;
  document.getElementById("btnMusicSync").disabled = musicBusy || musicQueue.length === 0;
  const cancel = document.getElementById("btnMusicCancel");
  cancel.dataset.i18n = musicBusy ? "music.stop" : "common.cancel";
  cancel.textContent = t(cancel.dataset.i18n);

  cancel.disabled = false;
}

function musicProgress(done, total, label, bytes) {
  document.getElementById("musicProgressBar").style.width =
    Math.round((done / Math.max(total, 1)) * 100) + "%";
  document.getElementById("musicProgressLine").textContent = t("music.sending", {
    done: done, total: total, path: label, size: formatBytes(bytes),
  });
}

const NAV_TRANSFER_TARGETS = {
  music: { tab: "home", card: "musicCard", title: "transfer.music" },
  announce: { tab: "settings", card: "trackOrderCard", title: "transfer.announce" },
  covers: { tab: "home", card: "coversCard", title: "transfer.covers" },
  intros: { tab: "settings", card: "introsCard", title: "transfer.intros" },
};
const navTransfer = {
  kind: null,
  hideTimer: null,
  show(kind, done, total, name) {
    const box = document.getElementById("navTransfer");
    clearTimeout(this.hideTimer);
    this.kind = kind;
    box.classList.remove("done", "failed");
    box.dataset.kind = kind;
    document.getElementById("navTransferTitle").textContent = t(NAV_TRANSFER_TARGETS[kind].title);
    document.getElementById("navTransferBar").style.width =
      Math.round((done / Math.max(total, 1)) * 100) + "%";
    document.getElementById("navTransferLine").textContent =
      t("transfer.progress", { done, total }) + (name ? " · " + name : "");
    box.hidden = false;
  },
  finish(kind, ok, text) {
    const box = document.getElementById("navTransfer");
    if (this.kind !== kind) return;
    box.classList.add(ok ? "done" : "failed");
    document.getElementById("navTransferBar").style.width = "100%";
    document.getElementById("navTransferLine").textContent = text;
    this.hideTimer = setTimeout(() => { box.hidden = true; this.kind = null; }, 8000);
  },
};
document.getElementById("navTransfer").addEventListener("click", () => {
  const target = NAV_TRANSFER_TARGETS[navTransfer.kind];
  if (!target) return;
  const card = document.getElementById(target.card);
  const page = card && card.dataset.page;
  setActiveView(target.tab, page || null);
  if (card) setTimeout(() => card.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
});

async function runMusicSync() {
  if (musicBusy || musicQueue.length === 0) return;
  musicBusy = true;
  musicStopRequested = false;
  updateMusicButtons();

  const total = musicQueue.length;
  navTransfer.show("music", 0, total, "");
  const remaining = [];
  let sent = 0;
  let sentBytes = 0;
  let failed = 0;
  let firstError = null;
  let streak = 0;
  let piSilent = false;

  const unreadable = [];

  for (let index = 0; index < musicQueue.length; index += 1) {
    const item = musicQueue[index];
    if (musicStopRequested) {
      remaining.push(...musicQueue.slice(index));
      break;
    }

    let content;
    try {
      content = new Blob([await item.file.arrayBuffer()], { type: item.file.type });
    } catch (e) {
      unreadable.push(item.path);
      remaining.push(item);
      musicProgress(sent + failed + unreadable.length, total, item.path, sentBytes);
      continue;
    }
    const body = new FormData();
    body.append("path", item.path);
    body.append("mtime", String(Math.round(item.file.lastModified / 1000)));
    body.append("file", content, item.file.name);

    const result = await apiFetch("/api/music/upload", { method: "POST", body });

    const wrongSize = result.ok && result.data &&
      Number(result.data.size) !== item.file.size;

    if (result.ok && !wrongSize) {
      sent += 1;
      sentBytes += item.file.size;
      streak = 0;
    } else if (musicStopRequested) {
      // Stopped on purpose while this file was in flight: back to the list, not an error.
      remaining.push(item);
      break;
    } else {
      failed += 1;
      streak += 1;
      remaining.push(item);
      if (!firstError) {
        firstError = wrongSize
          ? { error: "size_mismatch", detail: item.path }
          : result;
      }

      if (streak >= MUSIC_FAILURE_STREAK) {
        piSilent = true;
        remaining.push(...musicQueue.slice(index + 1));
        break;
      }
    }
    musicProgress(sent + failed, total, item.path, sentBytes);
    navTransfer.show("music", sent + failed, total, item.path.split("/").pop());
  }

  musicBusy = false;
  failed += unreadable.length;
  navTransfer.finish("music", failed === 0,
                     t(failed ? "transfer.failed" : "transfer.done", { sent, failed }));
  const stopped = musicStopRequested;

  musicQueue = remaining;
  updateMusicButtons();

  if (sent > 0) {
    await apiPost("/api/rescan_music");
  }

  pendingMusicFiles = remaining.length ? musicSelectedFiles.slice() : [];
  await refreshMusicLibrary();
  if (!musicQueue.length) {
    document.getElementById("musicPlanBox").hidden = true;
    document.getElementById("musicProgressLine").textContent = "";
    document.getElementById("musicProgressBar").style.width = "0%";
    musicSelectedFiles = [];
  }
  refreshStatus();

  const done = t("music.sent", { count: sent, size: formatBytes(sentBytes) });
  if (unreadable.length) {
    showToast(t("music.unreadable", { n: unreadable.length }),
      unreadable.slice(0, 3).map((p) => p.split("/").pop()).join(", ")
        + (unreadable.length > 3 ? "\u2026" : "") + " \u2014 " + t("music.unreadable_why"),
      { error: true });
  }
  if (piSilent) {
    showError("unreachable", t("music.pi_silent", { remaining: remaining.length }));
  } else if (failed > unreadable.length) {
    showToolError(t("music.failed", { count: failed }), firstError);
  } else if (stopped) {
    showToast(t("music.stopped"), done);
  } else {
    showToast(t("music.done"), done);
  }
}

document.getElementById("btnMusicFolder").addEventListener("click", () => {
  document.getElementById("musicFolderInput").click();
});
document.getElementById("btnMusicFiles").addEventListener("click", () => {
  document.getElementById("musicFilesInput").click();
});

function onMusicSelection(event) {
  const files = Array.prototype.slice.call(event.target.files || []);

  event.target.value = "";
  if (files.length === 0) return;
  pendingMusicFiles = files;
  musicSelectedFiles = files;
  if (musicManifest === null) refreshMusicLibrary();
  else buildMusicPlan(files);
}

document.getElementById("musicFolderInput").addEventListener("change", onMusicSelection);
document.getElementById("musicFilesInput").addEventListener("change", onMusicSelection);

document.getElementById("btnMusicSync").addEventListener("click", runMusicSync);

document.getElementById("btnMusicCancel").addEventListener("click", () => {
  if (musicBusy) {
    musicStopRequested = true;
    document.getElementById("btnMusicCancel").disabled = true;
    return;
  }
  musicQueue = [];
  pendingMusicFiles = [];
  musicSelectedFiles = [];
  document.getElementById("musicPlanBox").hidden = true;
  updateMusicButtons();
});

refreshMusicLibrary();
updateMusicButtons();

window.LANG_CHANGE_LISTENERS.push(() => {
  refreshStatus();
  refreshAnnouncements();
  loadTrackOrder();
  refreshStats();
  if (document.getElementById("eventList").children.length > 0) {
    oldestEventId = null;
    loadEvents(false);
  }
  refreshUpdate();
  refreshHomeWifi();
  refreshSecurityCard();
  refreshMusicLibrary();
  updateMusicButtons();
});

let gpioPinout = null;
let gpioTargetInput = null;
let gpioDetectAbort = false;

async function loadPinout() {
  if (gpioPinout) return gpioPinout;
  const result = await apiGet("/api/gpio/pinout");

  if (!result.ok || !result.data || !Array.isArray(result.data.pins)) return null;
  gpioPinout = result.data;
  return gpioPinout;
}

function pinTitle(pin, takenBy) {
  if (takenBy) return t("gpio.taken_by", { name: takenBy });
  if (pin.reserved) return t("gpio.reserved_" + pin.reserved);
  if (!pin.selectable) return pin.label;
  return t("gpio.pin_title", { bcm: pin.bcm, physical: pin.physical });
}

function renderPinHeader(data) {
  const container = document.getElementById("pinHeader");
  container.textContent = "";

  const current = parseInt(gpioTargetInput && gpioTargetInput.value, 10);

  const otherKey = gpioTargetInput && gpioTargetInput.dataset.key === "buttons.gpio_button_pin"
    ? "security.gpio_reset_pin" : "buttons.gpio_button_pin";
  const otherPin = data.assigned ? parseInt(data.assigned[otherKey], 10) : NaN;
  const otherName = otherKey === "security.gpio_reset_pin"
    ? t("security.gpio_pin") : t("settings.gpio_button_pin");

  data.pins.forEach((pin) => {
    const cell = document.createElement("button");
    cell.type = "button";

    cell.className = "pin-cell kind-" + pin.kind
      + (pin.physical % 2 ? " pin-left" : " pin-right");

    const takenBy = (pin.bcm === otherPin && !Number.isNaN(otherPin)) ? otherName : null;
    const usable = pin.selectable && !takenBy;
    if (!usable) cell.classList.add("pin-disabled");
    if (pin.reserved) cell.classList.add("kind-reserved");
    if (pin.bcm === current && !Number.isNaN(current)) cell.classList.add("pin-current");
    cell.disabled = !usable;
    cell.title = pinTitle(pin, takenBy);

    const num = document.createElement("span");
    num.className = "pin-num";
    num.textContent = pin.physical;
    const label = document.createElement("span");
    label.className = "pin-label";
    label.textContent = pin.label;
    cell.append(num, label);

    if (usable) {
      cell.addEventListener("click", () => selectPin(pin.bcm));
    }
    container.appendChild(cell);
  });
}

function selectPin(bcm) {
  if (gpioTargetInput) {
    gpioTargetInput.value = bcm;

    gpioTargetInput.dispatchEvent(new Event("input", { bubbles: true }));
    gpioTargetInput.dispatchEvent(new Event("change", { bubbles: true }));
  }
  closeGpioPicker();
}

