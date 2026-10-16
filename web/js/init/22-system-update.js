function renderServices(services) {
  document.getElementById("serviceList").replaceChildren(...services.map((svc) => {
    const li = document.createElement("li");
    const dot = document.createElement("span");
    dot.className = "service-dot";
    dot.dataset.state = svc.oneshot && svc.result === "success" && svc.state !== "failed" ? "active" : svc.state;
    dot.setAttribute("aria-hidden", "true");
    const name = document.createElement("span");
    name.className = "service-name";
    name.textContent = t("svc." + svc.name);
    name.title = svc.name + ".service";
    const state = document.createElement("span");
    state.className = "service-state";
    state.textContent = serviceStateText(svc);
    li.append(dot, name, state);
    if (svc.restartable) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn btn-small btn-warning-outline";
      btn.dataset.icon = "restart";
      btn.textContent = t("health.restart");
      btn.addEventListener("click", async () => {
        const label = t("svc." + svc.name);
        if (!(await showConfirm(t("health.restart_confirm", { name: label })))) return;
        btn.disabled = true;
        const r = await apiPost("/api/system/services/" + encodeURIComponent(svc.name) + "/restart");
        btn.disabled = false;
        if (!r.ok) {
          showError(r.error, null, r.detail);
          return;
        }
        showToast(r.data && r.data.reload ? t("health.web_restarting") : t("health.restarted", { name: label }));
        setTimeout(refreshHealth, 2000);
      });
      li.append(btn);
    }
    return li;
  }));
}

async function refreshHealth() {
  const [info, services] = await Promise.all([apiGet("/api/system/info"), apiGet("/api/system/services")]);
  if (info.ok && info.data) renderHealthInfo(info.data);
  if (services.ok && Array.isArray(services.data)) renderServices(services.data);
}
refreshHealth();
setInterval(() => {
  if (document.body.dataset.tab === "system" && !document.hidden) refreshHealth();
}, 15000);
document.querySelectorAll('.tab-btn[data-tab="system"]').forEach((btn) => btn.addEventListener("click", refreshHealth));

function diagnosticButton(id, path, titleKey) {
  const btn = document.getElementById(id);
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    const result = await apiPost(path, {});
    btn.disabled = false;
    if (!result.ok) {
      showToolError(t("diag.failed"), result);
      return;
    }
    const text = (result.data && result.data.report) || "";
    const report = document.createElement("pre");
    report.className = "diag-text";
    report.textContent = text;
    const choice = await openModal({
      title: t(titleKey),
      bodyNode: report,
      modalClass: "modal-report",
      choices: [{ label: t("diag.copy"), value: "copy" }],
    });
    if (choice === "copy") copyText(text, report);
  });
}
diagnosticButton("btnAudioDiag", "/api/diag/audio", "diag.title");
diagnosticButton("btnNetDiag", "/api/diag/network", "diag.net_button");

function renderRecentStats(series) {
  const sum = (rows, key) => rows.reduce((acc, row) => acc + (Number(row[key]) || 0), 0);
  const fill = (id, rows) => {
    const values = rows ? [
      ["stats.r_music", formatDuration(sum(rows, "seconds_music"))],
      ["stats.r_tracks", Math.round(sum(rows, "tracks_played"))],
      ["stats.r_clicks", Math.round(sum(rows, "clicks"))],
      ["stats.r_errors", Math.round(sum(rows, "playback_errors"))],
    ] : ["stats.r_music", "stats.r_tracks", "stats.r_clicks", "stats.r_errors"].map((k) => [k, "\u2013"]);
    document.getElementById(id).replaceChildren(...values.flatMap(([key, value]) => {
      const dt = document.createElement("dt");
      dt.dataset.i18n = key;
      dt.textContent = t(key);
      const dd = document.createElement("dd");
      dd.textContent = value;
      return [dt, dd];
    }));
  };
  fill("statsToday", series ? series.slice(-1) : null);
  fill("statsWeek", series ? series.slice(-7) : null);
}

document.getElementById("statsDays").addEventListener("change", refreshStats);
document.getElementById("btnStatsRefresh").addEventListener("click", () => {
  refreshStats(true);
  loadEvents(false);
});
document.getElementById("btnRefreshEvents").addEventListener("click", () => {
  oldestEventId = null;
  loadEvents(false);
});
document.getElementById("eventTypeFilter").addEventListener("change", () => {
  oldestEventId = null;
  loadEvents(false);
});
let eventSearchTimer = null;
document.getElementById("eventSearch").addEventListener("input", () => {
  clearTimeout(eventSearchTimer);
  eventSearchTimer = setTimeout(() => {
    oldestEventId = null;
    loadEvents(false);
  }, 300);
});
document.getElementById("btnMoreEvents").addEventListener("click", () => loadEvents(true));

document.getElementById("btnStatsExport").addEventListener("click", () => {
  window.location.href = "/api/journal/export";
});

/* Since the last start: what the radio did by itself can be taken back. */
let lastRun = null;
const runSummaryBox = document.getElementById("runSummary");
const btnEraseRun = document.getElementById("btnEraseRun");

function describeRun(run) {
  return t("run.summary", {
    tracks: run.tracks,
    time: formatDuration(run.seconds || 0),
    at: formatDateTime(run.started_at),
  });
}

async function refreshRun() {
  const result = await apiGet("/api/journal/run");
  lastRun = result.ok ? result.data : null;
  btnEraseRun.hidden = !lastRun;
  runSummaryBox.textContent = lastRun ? describeRun(lastRun) : t("run.none");
}

/* The state the radio is left in once the run is gone: the timer's own heads,
   side by side, and the legend under them saying what each one leaves behind. */
const RUN_CHOICES = [["moon", "standby", "run.choice_standby"],
                     ["play", "music", "run.choice_music"],
                     ["power", "poweroff", "run.choice_poweroff"]];

function runChoiceBody(at) {
  const box = document.createElement("div");
  const intro = document.createElement("p");
  intro.className = "run-intro";
  intro.textContent = t("run.erase_intro", { at });
  const list = document.createElement("ul");
  list.className = "run-list";
  ["run.erase_played", "run.erase_points", "run.erase_journal"].forEach((key) => {
    const line = document.createElement("li");
    line.textContent = t(key);
    list.appendChild(line);
  });
  box.append(intro, list, choiceButtons(RUN_CHOICES));
  return box;
}

/* The legend of the timer and of the switch-off dialog, saying what the run
   leaves behind: nothing to undo either way, the music simply goes on from there.
   Standby carries the moon, the one the timer gives it. */
function runChoiceLegend() {
  return legendLines([["moon", t("run.legend_standby")],
                      ["play", t("run.legend_music")],
                      ["power", t("run.legend_poweroff")]]);
}

btnEraseRun.addEventListener("click", async () => {
  if (!lastRun) return;
  const choice = await openModal({
    title: t("run.erase"),
    bodyNode: runChoiceBody(formatDateTime(lastRun.started_at)),
    legend: runChoiceLegend(),
    actions: false,
  });
  if (!choice) return;

  const result = await apiPost("/api/journal/erase_run", { session: lastRun.session });
  if (!result.ok) {
    showError(result.error);
    return;
  }
  oldestEventId = null;
  await refreshRun();
  await refreshStats();
  await loadEvents(false);
  showToast(t("run.done"), t("run.done_detail", {
    tracks: result.data.tracks || 0, events: result.data.events || 0 }));

  // Off ends the page's own business: the radio is not there to answer any more.
  if (choice === "poweroff") {
    apiPost("/api/action/poweroff");
    return;
  }
  const answer = await apiPost(choice === "standby" ? "/api/action/standby"
                                                   : "/api/action/start_music");
  if (!answer.ok) showToolError(t("home.transport_failed"), answer);
  refreshStatus();
});

document.getElementById("btnStatsReset").addEventListener("click", async () => {
  const clean = await showChoice(t("stats.reset_prompt"), [
    { value: "events", label: t("stats.reset_events") },
    { value: "counters", label: t("stats.reset_counters") },
    { value: "all", label: t("stats.reset_all"), danger: true },
  ]);

  if (!clean) return;
  if (!(await showConfirm(t("stats.confirm_reset", { scope: clean })))) return;

  const result = await apiPost("/api/journal/reset", { scope: clean });
  if (!result.ok) {
    showError(result.error);
    return;
  }
  oldestEventId = null;
  await refreshStats();
  await loadEvents(false);
  showToast(t("stats.reset_done"));
});

/* A page that reads its data only when it is shown hears about it here. */
document.addEventListener("page-shown", (event) => {
  const page = event.detail && event.detail.page;
  if (page === "events" && document.getElementById("eventList").children.length === 0) {
    loadEvents(false);
  }
  if (page === "likes") refreshLikes();
  if (page === "duplicates") refreshDuplicates();
  if (page === "previous") refreshPrevious();
  if (page === "banned") refreshBanned();
  if (page === "suggest" && suggestNewCount) noteNewSuggestions();
  if (page === "djfiles") loadPrepared("intros");
});

refreshStats();
refreshRun();

refreshEvery(refreshStats, 30000, ["stats/"]);
/* Reading the run's figures walks the whole log, so this stays at one minute
   on top of the read a page opening already does. */
refreshEvery(refreshRun, 60000, ["stats/overview"]);

let updatePollTimer = null;

let updateAwaitingStart = 0;

let updateNotStartedShown = false;
const UPDATE_START_GRACE_MS = 60000;

function describeVersion(v) {
  if (!v || !v.tree_hash_short) return t("update.unknown_version");
  let text = t("update.version_prefix", { hash: v.tree_hash_short });
  if (v.release) text += " \u00b7 " + v.release;
  if (v.git && v.git.describe) text += " (" + v.git.describe + ")";
  else if (v.git && v.git.short) text += " (git " + v.git.short + ")";
  return text;
}

function describeUpdateSource(v) {
  if (!v || !v.installed_at) return "";
  const SOURCE_KEYS = { usb: "update.source_usb", wifi: "update.source_wifi",
    git: "update.source_git", release: "update.source_release",
    install: "update.source_install", local: "update.source_local" };
  const parts = [t("update.installed_prefix", { date: formatDateTime(v.installed_at) })];
  if (SOURCE_KEYS[v.source]) parts.push(t(SOURCE_KEYS[v.source]));
  if (v.git && v.git.branch) parts.push(t("update.branch_prefix", { branch: v.git.branch }));
  if (v.git && v.git.dirty) parts.push(t("update.uncommitted"));
  if (v.file_count) parts.push(t("update.files_count", { n: v.file_count }));
  return parts.join(" · ");
}

async function refreshUpdate() {
  const result = await apiGet("/api/update/status");
  if (!result.ok) return;
  const d = result.data;

  document.getElementById("updateVersion").textContent = describeVersion(d.version);
  document.getElementById("updateSource").textContent = describeUpdateSource(d.version);

  const osLine = document.getElementById("updateOs");
  const parts = [];
  if (d.os_pending === null || d.os_pending === undefined) parts.push(t("update.os_unknown"));
  else if (d.os_pending === 0) parts.push(t("update.os_none"));
  else parts.push(t("update.os_pending", { n: d.os_pending }));
  if (d.has_internet === false) parts.push(t("update.no_internet"));
  osLine.textContent = parts.join(" · ");
  osLine.classList.toggle("warning", d.has_internet === false);

  // A container has no tree to replace: the image is the version, so the card
  // shows the one line that updates it. Its own rows are hidden by CSS.
  const containerBox = document.getElementById("containerUpdateBox");
  containerBox.hidden = d.self_update !== false;
  if (!containerBox.hidden) {
    const image = d.docker_image || "ghcr.io/arubinu/rukebox";
    document.getElementById("containerPullCmd").textContent = "docker pull " + image;
  }

  const logBox = document.getElementById("updateLog");
  const hint = document.getElementById("gitUpdateHint");
  if (d.log_tail) {
    logBox.hidden = false;
    logBox.textContent = d.log_tail;
    logBox.scrollTop = logBox.scrollHeight;
  }
  const btn = document.getElementById("btnReleaseInstall");
  const awaiting = updateAwaitingStart
    && (Date.now() - updateAwaitingStart < UPDATE_START_GRACE_MS);

  document.getElementById("btnReleaseInstall").disabled = !!d.running || !!(updateAwaitingStart
    && (Date.now() - updateAwaitingStart < UPDATE_START_GRACE_MS));
  if (d.running) {
    updateAwaitingStart = 0;
    updateNotStartedShown = false;
    btn.disabled = true;
    hint.textContent = t("update.in_progress");
    hint.classList.add("update-running");
  } else if (awaiting) {
    btn.disabled = true;
    hint.textContent = t("update.starting");
    hint.classList.add("update-running");
  } else {
    if (updateAwaitingStart) {
      updateAwaitingStart = 0;
      updateNotStartedShown = true;
      hint.textContent = t("update.not_started");
    }
    btn.disabled = false;
    hint.classList.remove("update-running");
    if (updatePollTimer) {
      clearInterval(updatePollTimer);
      updatePollTimer = null;
      if (!updateNotStartedShown) {
        hint.textContent = t("update.finished");
      }
      refreshStats();
    }
    // No end marker means the run died halfway: say so instead of waiting for ever.
    if (d.interrupted && !updateNotStartedShown) {
      hint.textContent = t("update.interrupted");
    }
  }
}

let latestRelease = null;

async function checkRelease(force) {
  const status = document.getElementById("releaseStatus");
  const install = document.getElementById("btnReleaseInstall");
  const btn = document.getElementById("btnReleaseCheck");
  btn.disabled = true;
  status.textContent = t("update.release_checking");
  status.classList.remove("warning");
  try {
    const result = await apiGet("/api/update/release" + (force ? "?force=1" : ""));
    latestRelease = result.ok ? result.data : null;
    install.hidden = true;
    if (!result.ok) {
      status.textContent = errorLabel(result.error);
      status.classList.toggle("warning", result.error !== "release_none");
      return;
    }
    const r = result.data;
    const when = r.published_at ? new Date(r.published_at).toLocaleDateString() : "";
    if (!r.newer) {
      status.textContent = "";
      if (force) showToast(t("update.release_current", { tag: r.tag }));
    } else if (!r.allowed) {
      status.textContent = t("update.release_available", { tag: r.tag, date: when }) + " " + t("update.release_not_allowed");
    } else {
      status.textContent = t("update.release_available", { tag: r.tag, date: when });
      install.hidden = false;
      install.textContent = t("update.release_install", { tag: r.tag });
    }
  } finally {
    btn.disabled = false;
    refreshUpdate();
  }
}

document.getElementById("btnReleaseCheck").addEventListener("click", () => checkRelease(true));

document.getElementById("releaseRepoForm").addEventListener("submit", () => {
  latestRelease = null;
  document.getElementById("btnReleaseInstall").hidden = true;
  document.getElementById("releaseStatus").textContent = "";
});
document.getElementById("btnReleaseInstall").addEventListener("click", async () => {
  if (!latestRelease) return;
  if (!(await showConfirm(t("update.confirm_release", { tag: latestRelease.tag })))) return;
  const hint = document.getElementById("gitUpdateHint");
  hint.textContent = t("update.starting");
  const result = await apiPost("/api/update/release");
  if (!result.ok) {
    hint.textContent = t("common.failed_prefix", { error: errorLabel(result.error) });
    return;
  }
  document.getElementById("btnReleaseInstall").disabled = true;
  if (updatePollTimer) clearInterval(updatePollTimer);
  updatePollTimer = setInterval(refreshUpdate, 3000);
  updateAwaitingStart = Date.now();
  refreshUpdate();
});

refreshUpdate();
setInterval(() => {
  if (!updatePollTimer) refreshUpdate();
}, 30000);

document.getElementById("btnConfigExport").addEventListener("click", () => {
  window.location.href = "/api/config/export";
});

async function refreshBackupSizes() {
  const r = await apiGet("/api/backup/sizes");
  if (!r.ok || !r.data) return;
  [["stats", "backupStatsSize"], ["suggestions", "backupSuggestionsSize"], ["sounds", "backupSoundsSize"]]
    .forEach(([part, id]) => { document.getElementById(id).textContent = formatBytes(r.data[part] || 0); });
}
document.getElementById("backupSection").addEventListener("toggle", (e) => {
  if (e.target.open) refreshBackupSizes();
});
document.getElementById("btnBackupDownload").addEventListener("click", () => {
  const parts = [["backupStats", "stats"], ["backupSuggestions", "suggestions"], ["backupSounds", "sounds"]]
    .filter(([id]) => document.getElementById(id).checked).map(([, part]) => part);
  window.location.href = "/api/backup?parts=" + parts.join(",");
});
document.getElementById("btnBackupRestore").addEventListener("click", () => {
  document.getElementById("backupFileInput").click();
});
document.getElementById("backupFileInput").addEventListener("change", async (event) => {
  const input = event.target;
  const file = input.files && input.files[0];
  const status = document.getElementById("backupStatus");
  if (!file) return;
  try {
    status.textContent = t("backup.reading");
    const form = new FormData();
    form.append("file", file);

    const inspect = await apiFetch("/api/backup/inspect", { method: "POST", body: form });
    status.textContent = "";
    if (!inspect.ok) {
      showError(inspect.error, t("backup.restore"));
      return;
    }
    const d = inspect.data;
    const parts = d.parts.map((p) => t("backup.part_" + p)
      + (p === "sounds" ? " (" + d.sounds + ")" : "")).join(", ");
    const ok = await showConfirm(t("backup.confirm", {
      name: file.name, date: d.created ? formatDateTime(d.created) : "?", parts }));
    if (!ok) return;
    status.textContent = t("backup.restoring");
    const r = await apiPost("/api/backup/restore", { token: d.token });
    status.textContent = "";
    if (!r.ok) {
      showError(r.error, t("backup.restore"));
      return;
    }
    const summary = t("backup.restored", { settings: r.data.settings_changed, sounds: r.data.sounds });
    status.textContent = summary;
    showToast(t("backup.restored_title"), summary + (r.data.daemon_restarted ? " " + t("backup.daemon_restarted") : ""));
    loadSettingsIntoForm();
  } finally {
    input.value = "";
  }
});

document.getElementById("btnConfigImport").addEventListener("click", () => {
  document.getElementById("configFileInput").click();
});

document.getElementById("configFileInput").addEventListener("change", async (event) => {
  const input = event.target;
  const file = input.files && input.files[0];
  const status = document.getElementById("configStatus");
  if (!file) return;
  try {
    const text = await file.text();
    let bundle;
    try {
      bundle = JSON.parse(text);
    } catch (e) {
      showError("not_a_bundle", t("config.import"));
      return;
    }

    if (!bundle || typeof bundle !== "object" || !bundle.settings) {
      showError("not_a_bundle", t("config.import"));
      return;
    }
    const ok = await showConfirm(t("config.confirm_import", { name: file.name }));
    if (!ok) return;
    status.textContent = t("config.importing");
    const result = await apiPost("/api/config/import", bundle);
    if (!result.ok) {
      status.textContent = "";
      showError(result.error, t("config.import"));
      return;
    }
    const d = result.data || {};
    const summary = t("config.imported", {
      settings: d.settings_changed || 0,
      announcements: d.announcements || 0,
      orders: d.track_order || 0,
    }) + ("schedules" in d ? " " + t("config.imported_more", {
      schedules: d.schedules || 0,
      lists: d.music_lists || 0,
      likes: d.likes || 0,
      hidden: d.hidden || 0,
    }) : "");

    status.textContent = summary;
    showToast(t("config.imported_title"), summary);
    loadSettingsIntoForm();
  } finally {
    input.value = "";
  }
});

let musicManifest = null;
let musicQueue = [];
let musicBusy = false;
let musicStopRequested = false;

const MUSIC_FAILURE_STREAK = 3;

function refreshMusicLibrary() {
  const line = document.getElementById("musicLibraryLine");
  if (!line) return Promise.resolve();
  return apiGet("/api/music/manifest").then((result) => {
    if (!result.ok || !result.data) {
      line.textContent = errorLabel(result.error || "unreachable");
      return;
    }
    const data = result.data;
    musicManifest = {};
    (data.files || []).forEach((f) => { musicManifest[f.path] = f; });
    musicManifestFree = Number(data.free_bytes) || 0;
    if (!data.exists) {
      line.textContent = t("music.folder_missing", { dir: data.dir || "?" });
      return;
    }
    const count = (data.files || []).length;
    // The songs, as the radio counts them (src/config_and_scan.py): lyrics and covers are files too.
    const tracks = (data.files || []).filter((f) => /\.(mp3|flac|ogg|m4a|wav|opus)$/i.test(f.path || "")).length;
    const bytes = (data.files || []).reduce((sum, f) => sum + (Number(f.size) || 0), 0);
    line.textContent = t("music.library_line", {
      tracks: tracks, count: count, size: formatBytes(bytes), free: formatBytes(musicManifestFree),
    });

    if (pendingMusicFiles.length) buildMusicPlan(pendingMusicFiles, true);
  });
}

let musicManifestFree = 0;
let pendingMusicFiles = [];

let musicSelectedFiles = [];

function musicTargetPath(file) {
  const relative = file.webkitRelativePath || "";
  const parts = relative ? relative.split("/").slice(1) : [file.name];
  return parts.filter(Boolean).join("/");
}

