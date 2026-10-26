/* Syncs from another device: the connections, the syncs, their runs, and the toasts that
   say when one starts and ends. The owner's alone: the server answers no one else. */
let syncData = null;
let syncEditing = null;
let syncConnEditing = null;
let syncLastStatus = null;

function syncWhen(stamp) {
  return stamp ? new Date(stamp * 1000).toLocaleString(currentLang, { dateStyle: "short", timeStyle: "short" }) : "";
}

function syncPlaceLabel(place) {
  if (!place) return "";
  if (place.kind === "announcement") return t("sync.place_announcement", { name: place.name || "" });
  if (place.kind === "system_sounds") return t("sync.place_system_sounds", { name: place.id.slice(7) });
  return t("sync.place_" + place.kind);
}

function syncPlaceOf(target) {
  const places = (syncData && syncData.places) || [];
  // The longest folder that holds the target: an announcement's folder before its parent.
  return places.filter((p) => target === p.target || target.startsWith(p.target + "/"))
    .sort((a, b) => b.target.length - a.target.length)[0] || null;
}

function syncJob(id) {
  return ((syncData && syncData.jobs) || []).find((job) => job.id === id) || null;
}

function syncIconButton(icon, key, vars, onClick) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "btn btn-icon btn-small";
  b.dataset.icon = icon;
  const label = t(key, vars || {});
  b.setAttribute("aria-label", label);
  b.title = label;
  b.addEventListener("click", onClick);
  return b;
}

function syncRow(name, sub, extra, actions) {
  const li = document.createElement("li");
  const text = document.createElement("span");
  text.className = "device-label";
  const title = document.createElement("span");
  title.className = "device-name";
  title.textContent = name;
  text.append(title);
  [sub, extra].filter(Boolean).forEach((line) => {
    const el = document.createElement("span");
    el.className = "device-sub";
    el.textContent = line;
    text.append(el);
  });
  const box = document.createElement("span");
  box.className = "device-actions";
  box.append(...actions);
  li.append(text, box);
  return li;
}

function syncEveryLabel(hours) {
  const known = [0, 1, 6, 12, 24, 168];
  return known.includes(hours) ? t("sync.every_" + hours) : t("sync.every_n", { n: hours });
}

function syncRunLine(run) {
  const vars = { when: syncWhen(run.ended || run.started), added: run.added || 0, updated: run.updated || 0,
                 removed: run.removed || 0, n: run.would_remove || 0 };
  if (run.status === "ok") return t(run.removed ? "sync.last_ok_removed" : "sync.last_ok", vars);
  const key = { stopped: "sync.last_stopped", mirror_confirm: "sync.last_confirm",
                mirror_source_empty: "sync.last_source_empty" }[run.status];
  return t(key || "sync.last_failed", vars);
}

function syncLastLine(job) {
  const status = syncData.status || {};
  const held = (status.held || {})[job.id];
  if (held) return t("sync.held", { why: errorLabel(held) });
  const run = (status.runs || []).find((one) => one.job === job.id);
  return run ? syncRunLine(run) : t("sync.last_never");
}

function renderSyncJobs() {
  const running = (syncData.status || {}).running;
  const rows = syncData.jobs.map((job) => {
    const run = syncIconButton("play", "sync.run_now", { name: job.name }, () => runSync(job));
    run.disabled = !!running || !syncData.rclone;
    const actions = [run];
    const pending = ((syncData.status || {}).mirror || {})[job.id];
    if (pending) {
      actions.unshift(syncIconButton("alert", "sync.mirror_review", { name: job.name },
        () => reviewMirror(job, pending)));
    }
    actions.push(syncIconButton("pencil", "sync.edit", { name: job.name }, () => openSyncJobForm(job)),
      syncIconButton("trash", "sync.delete", { name: job.name }, () => deleteSyncJob(job)));
    return syncRow(job.name + (job.enabled ? "" : " · " + t("sync.off")),
      syncPlaceLabel(syncPlaceOf(job.target)) + " · " + syncEveryLabel(job.every_hours)
        + (job.mode === "mirror" ? " · " + t("sync.mode_mirror_short") : ""),
      syncLastLine(job), actions);
  });
  document.getElementById("syncJobList").replaceChildren(...rows);
  document.getElementById("syncEmpty").hidden = rows.length > 0;
}

function renderSyncConns() {
  const rows = syncData.connections.map((conn) => {
    const badges = [];
    if (conn.secret_lost) badges.push(t("sync.secret_lost_badge"));
    if (conn.type === "sftp" && !conn.host_key) badges.push(t("sync.host_needed"));
    const actions = [syncIconButton("check", "sync.test", { name: conn.name }, () => testSyncConn(conn))];
    if (conn.type === "sftp") actions.push(syncIconButton("key", "sync.host_check", { name: conn.name }, () => checkSyncHost(conn)));
    actions.push(syncIconButton("pencil", "sync.edit", { name: conn.name }, () => openSyncConnForm(conn)),
      syncIconButton("trash", "sync.delete", { name: conn.name }, () => deleteSyncConn(conn)));
    const where = (conn.user ? conn.user + "@" : "") + conn.host + (conn.path ? "/" + conn.path : "");
    return syncRow(conn.name, t("sync.type_short_" + conn.type) + " · " + where, badges.join(" · "), actions);
  });
  document.getElementById("syncConnList").replaceChildren(...rows);
  document.getElementById("syncConnEmpty").hidden = rows.length > 0;
}

function renderSyncNow() {
  const running = (syncData.status || {}).running;
  const box = document.getElementById("syncNow");
  box.hidden = !running;
  if (!running) return;
  const job = syncJob(running.job);
  const progress = running.progress || {};
  const pct = progress.total_bytes ? Math.round(100 * progress.bytes / progress.total_bytes) : 0;
  document.getElementById("syncNowLine").textContent = t("sync.running", {
    name: job ? job.name : running.job, done: progress.done || 0, total: progress.total || 0, pct });
  document.getElementById("syncProgressBar").style.width = pct + "%";
}

function renderSyncRuns() {
  const runs = (syncData.status || {}).runs || [];
  const rows = runs.map((run) => {
    const job = syncJob(run.job);
    return syncRow(job ? job.name : run.job, syncRunLine(run), (run.errors || [])[0] || "", []);
  });
  document.getElementById("syncRunList").replaceChildren(...rows);
  document.getElementById("syncRunsEmpty").hidden = rows.length > 0;
}

function renderSyncHere() {
  document.querySelectorAll(".sync-here").forEach((box) => {
    const place = ((syncData && syncData.places) || []).find((p) => p.id === box.dataset.syncPlace);
    box.hidden = !place || !syncData.rclone;
    if (box.hidden) return;
    const count = syncData.jobs.filter((job) => syncPlaceOf(job.target) === place).length;
    const line = document.createElement("p");
    line.className = "hint";
    line.textContent = count ? t("sync.here_count", { n: count }) : t("sync.here_none");
    const add = document.createElement("button");
    add.type = "button";
    add.className = "btn";
    add.dataset.icon = "refresh";
    add.textContent = t("sync.here_add");
    add.addEventListener("click", () => {
      goToCard("home", "syncTitle");
      openSyncJobForm(null, place.id);
    });
    // In a row like every other button of the page: it spans the card, never sits on one side.
    const row = document.createElement("div");
    row.className = "actions-row";
    row.append(add);
    box.replaceChildren(line, row);
  });
}

function renderSync() {
  document.getElementById("syncNoRclone").hidden = syncData.rclone;
  renderSyncJobs();
  renderSyncConns();
  renderSyncNow();
  renderSyncRuns();
  renderSyncHere();
}

/* A run that starts or ends is told once, to whoever may manage the syncs. */
function noteSyncStatus(status) {
  const before = syncLastStatus;
  syncLastStatus = status;
  if (!before) return;
  const was = before.running && before.running.job;
  const now = status.running && status.running.job;
  if (now && now !== was) {
    const job = syncJob(now);
    showToast(t("sync.started", { name: job ? job.name : now }));
  }
  if (was && was !== now) {
    const job = syncJob(was);
    const run = (status.runs || []).find((one) => one.job === was);
    const name = job ? job.name : was;
    if (!run || run.status === "stopped") showToast(t("sync.stopped_toast", { name }));
    else if (run.status === "ok") {
      showToast(t("sync.done", { name, added: run.added || 0, updated: run.updated || 0 }));
    } else if (run.status === "mirror_confirm") {
      showToast(t("sync.mirror_waits", { name, n: run.would_remove || 0 }));
    } else showToast(t("sync.failed_toast", { name }), (run.errors || [])[0] || "", { error: true });
  }
}

async function refreshSync() {
  if (!mayCall("/api/sync")) return;
  const r = await apiGet("/api/sync");
  if (!r.ok || !r.data) return;
  syncData = r.data;
  noteSyncStatus(syncData.status);
  renderSync();
}

/* A sync runs on the radio, whoever started it and from wherever: the side menu shows it on every
   page, as it shows this page's own uploads. */
function paintNavSync(status) {
  const running = status.running;
  const box = document.getElementById("navSync");
  box.hidden = !running;
  if (!running) return;
  const job = syncJob(running.job);
  const progress = running.progress || {};
  const pct = progress.total_bytes ? Math.round(100 * progress.bytes / progress.total_bytes) : 0;
  document.getElementById("navSyncBar").style.width = pct + "%";
  setScrollingText(document.getElementById("navSyncLine"), t("sync.running", {
    name: running.name || (job ? job.name : running.job), done: progress.done || 0, total: progress.total || 0, pct }));
}

document.getElementById("navSync").addEventListener("click", () => setActiveView("home", "sync"));

async function watchSync() {
  if (!mayCall("/api/sync/status")) return;
  const r = await apiGet("/api/sync/status");
  if (!r.ok || !r.data) return;
  paintNavSync(r.data);
  const changed = !syncLastStatus || r.data.seq !== syncLastStatus.seq
    || JSON.stringify(r.data.running) !== JSON.stringify(syncLastStatus.running);
  noteSyncStatus(r.data);
  if (syncData && changed) {
    syncData.status = r.data;
    renderSync();
  }
}

function fillSyncPlaces(selected) {
  const select = document.getElementById("sjPlace");
  select.replaceChildren(...syncData.places.map((place) => {
    const option = document.createElement("option");
    option.value = place.id;
    option.textContent = syncPlaceLabel(place);
    return option;
  }));
  if (selected) select.value = selected;
}

function syncPlaceChosen() {
  const place = syncData.places.find((p) => p.id === document.getElementById("sjPlace").value);
  if (!place) return;
  document.getElementById("sjTarget").value = place.target;
  document.getElementById("sjRecentRow").hidden = place.kind !== "music";
  document.getElementById("sjExtensions").placeholder = (place.extensions || []).join(", ");
}

function openSyncJobForm(job, placeId) {
  if (!syncData) return;
  if (!syncData.connections.length) {
    showToast(t("sync.conn_first"));
    document.getElementById("syncConnSection").open = true;
    openSyncConnForm(null);
    return;
  }
  syncEditing = job ? job.id : "";
  const connection = document.getElementById("sjConnection");
  connection.replaceChildren(...syncData.connections.map((conn) => {
    const option = document.createElement("option");
    option.value = conn.id;
    option.textContent = conn.name;
    return option;
  }));
  const place = job ? syncPlaceOf(job.target) : syncData.places.find((p) => p.id === (placeId || "music"));
  fillSyncPlaces(place ? place.id : null);
  syncPlaceChosen();
  const value = (id, v) => { document.getElementById(id).value = v; };
  value("sjName", job ? job.name : (place ? syncPlaceLabel(place) : ""));
  value("sjConnection", job ? job.connection : syncData.connections[0].id);
  value("sjRemote", job ? job.remote : "");
  if (job) value("sjTarget", job.target);
  value("sjEvery", String(job ? job.every_hours : 24));
  value("sjWindowStart", job ? job.window_start : "");
  value("sjWindowEnd", job ? job.window_end : "");
  value("sjInternet", job ? job.internet : "any");
  value("sjWifi", job ? job.wifi : "any");
  value("sjSsid", job ? job.ssid : "");
  value("sjPlayback", job ? job.playback : "idle");
  value("sjMode", job && job.mode ? job.mode : "copy");
  value("sjExtensions", job ? job.extensions.join(", ") : "");
  value("sjMaxSize", String(job ? job.max_size_mb : 500));
  document.getElementById("sjAtBoot").checked = job ? job.at_boot : true;
  document.getElementById("sjRecent").checked = job ? job.recent_list : true;
  document.getElementById("sjEnabled").checked = job ? job.enabled : true;
  document.getElementById("sjSsidRow").hidden = document.getElementById("sjWifi").value !== "ssid";
  document.getElementById("syncJobForm").hidden = false;
  document.getElementById("syncAdd").hidden = true;
  document.getElementById("sjName").focus();
}

function closeSyncJobForm() {
  syncEditing = null;
  document.getElementById("syncJobForm").hidden = true;
  document.getElementById("syncAdd").hidden = false;
}

document.getElementById("syncAdd").addEventListener("click", () => openSyncJobForm(null));
document.getElementById("sjCancel").addEventListener("click", closeSyncJobForm);
document.getElementById("sjPlace").addEventListener("change", syncPlaceChosen);
document.getElementById("sjWifi").addEventListener("change", (e) => {
  document.getElementById("sjSsidRow").hidden = e.target.value !== "ssid";
});

document.getElementById("syncJobForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!syncData) return;
  const get = (id) => document.getElementById(id).value;
  const place = syncData.places.find((p) => p.id === get("sjPlace"));
  const body = {
    name: get("sjName").trim(), connection: get("sjConnection"), remote: get("sjRemote").trim(),
    kind: place ? place.kind : "music", target: get("sjTarget").trim(),
    every_hours: Number(get("sjEvery")), window_start: get("sjWindowStart"), window_end: get("sjWindowEnd"),
    at_boot: document.getElementById("sjAtBoot").checked, internet: get("sjInternet"), wifi: get("sjWifi"),
    ssid: get("sjSsid").trim(), playback: get("sjPlayback"), mode: get("sjMode"), extensions: get("sjExtensions"),
    max_size_mb: Number(get("sjMaxSize") || 500), recent_list: document.getElementById("sjRecent").checked,
    enabled: document.getElementById("sjEnabled").checked,
  };
  const r = await apiPost("/api/sync/jobs" + (syncEditing ? "/" + encodeURIComponent(syncEditing) : ""), body);
  if (!r.ok) {
    showError(r.error);
    return;
  }
  syncData = r.data;
  closeSyncJobForm();
  renderSync();
  showToast(t("sync.saved", { name: body.name }));
});

async function deleteSyncJob(job) {
  if (!(await showConfirm(t("sync.confirm_delete", { name: job.name })))) return;
  const r = await apiDelete("/api/sync/jobs/" + encodeURIComponent(job.id));
  if (!r.ok) {
    showError(r.error);
    return;
  }
  syncData = r.data;
  renderSync();
}

async function runSync(job) {
  const r = await apiPost("/api/sync/jobs/" + encodeURIComponent(job.id) + "/run");
  if (!r.ok) {
    showError(r.error, job.name);
    return;
  }
  watchSync();
}

/* A mirror that would remove many files waits: the owner sees which, and lets it go once. */
async function reviewMirror(job, pending) {
  const body = document.createElement("div");
  const intro = document.createElement("p");
  intro.textContent = t("sync.mirror_body", { name: job.name, n: pending.count,
                                              days: (syncData.trash_days || 14) });
  const list = document.createElement("ul");
  list.className = "sync-removals";
  pending.names.forEach((name) => {
    const li = document.createElement("li");
    li.textContent = name;
    list.append(li);
  });
  body.append(intro, list);
  if (pending.count > pending.names.length) {
    const more = document.createElement("p");
    more.className = "hint";
    more.textContent = t("sync.mirror_more", { n: pending.count - pending.names.length });
    body.append(more);
  }
  const answer = await openModal({ title: t("sync.mirror_title"), bodyNode: body, confirm: true,
                                   choices: [{ label: t("sync.mirror_accept", { n: pending.count }), value: true, danger: true }] });
  if (answer !== true) return;
  const r = await apiPost("/api/sync/jobs/" + encodeURIComponent(job.id) + "/mirror_confirm");
  if (!r.ok) {
    showError(r.error, job.name);
    return;
  }
  syncData.status = r.data;
  renderSync();
}

document.getElementById("syncStop").addEventListener("click", async () => {
  await apiPost("/api/sync/stop");
  watchSync();
});

function syncConnTypeChosen() {
  const type = document.getElementById("scType").value;
  document.getElementById("scPort").placeholder = { sftp: "22", smb: "445", webdav: "443", ftp: "21" }[type];
  document.getElementById("scVendorRow").hidden = type !== "webdav";
  document.getElementById("scTlsRow").hidden = !["webdav", "ftp"].includes(type);
  const auth = document.getElementById("scAuth");
  auth.querySelector('option[value="key"]').disabled = type !== "sftp";
  if (type !== "sftp" && auth.value === "key") auth.value = "password";
  syncAuthChosen();
}

async function syncAuthChosen() {
  const auth = document.getElementById("scAuth").value;
  document.getElementById("scPasswordRow").hidden = auth !== "password";
  const box = document.getElementById("scKeyBox");
  box.hidden = auth !== "key";
  if (auth === "key" && !document.getElementById("scKeyText").textContent) {
    const r = await apiGet("/api/sync/ssh_key");
    if (r.ok) document.getElementById("scKeyText").textContent = r.data.public;
    else showError(r.error);
  }
}

function openSyncConnForm(conn) {
  syncConnEditing = conn ? conn.id : "";
  const value = (id, v) => { document.getElementById(id).value = v; };
  value("scName", conn ? conn.name : "");
  value("scType", conn ? conn.type : "smb");
  value("scHost", conn ? conn.host : "");
  value("scUser", conn ? conn.user : "");
  value("scPassword", "");
  value("scPath", conn ? conn.path : "");
  value("scPort", conn ? String(conn.port) : "");
  value("scAuth", conn ? conn.auth : "password");
  value("scVendor", conn && conn.vendor ? conn.vendor : "other");
  value("scTls", conn ? conn.tls : "verify");
  document.getElementById("scPassword").placeholder = conn && conn.secret_set ? t("sync.c_password_keep") : "";
  syncConnTypeChosen();
  document.getElementById("syncConnForm").hidden = false;
  document.getElementById("syncConnAdd").hidden = true;
  document.getElementById("scName").focus();
}

function closeSyncConnForm() {
  syncConnEditing = null;
  document.getElementById("syncConnForm").hidden = true;
  document.getElementById("syncConnAdd").hidden = false;
}

document.getElementById("syncConnAdd").addEventListener("click", () => openSyncConnForm(null));
document.getElementById("scCancel").addEventListener("click", closeSyncConnForm);
document.getElementById("scType").addEventListener("change", syncConnTypeChosen);
document.getElementById("scAuth").addEventListener("change", syncAuthChosen);

document.getElementById("syncConnForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!syncData) return;
  const get = (id) => document.getElementById(id).value.trim();
  const body = { name: get("scName"), type: get("scType"), host: get("scHost"), user: get("scUser"),
                 path: get("scPath"), auth: get("scAuth"), vendor: get("scVendor"), tls: get("scTls") };
  if (get("scPort")) body.port = Number(get("scPort"));
  const password = document.getElementById("scPassword").value;
  if (password) body.password = password;
  const r = await apiPost("/api/sync/connections" + (syncConnEditing ? "/" + encodeURIComponent(syncConnEditing) : ""), body);
  document.getElementById("scPassword").value = "";
  if (!r.ok) {
    showError(r.error);
    return;
  }
  syncData = r.data;
  closeSyncConnForm();
  renderSync();
  showToast(t("sync.conn_saved", { name: body.name }));
  const saved = syncData.connections.find((conn) => conn.name === body.name);
  // A new SFTP server is checked at once: nothing runs before its key is accepted.
  if (saved && saved.type === "sftp" && !saved.host_key) checkSyncHost(saved);
});

async function deleteSyncConn(conn) {
  if (!(await showConfirm(t("sync.conn_confirm_delete", { name: conn.name })))) return;
  const r = await apiDelete("/api/sync/connections/" + encodeURIComponent(conn.id));
  if (!r.ok) {
    showError(r.error);
    return;
  }
  syncData = r.data;
  renderSync();
}

async function testSyncConn(conn) {
  showToast(t("sync.testing", { name: conn.name }));
  const r = await apiPost("/api/sync/connections/" + encodeURIComponent(conn.id) + "/test");
  if (r.ok) showToast(t("sync.tested", { name: conn.name }));
  else showToast(t("sync.test_failed", { name: conn.name }), r.detail || errorLabel(r.error), { error: true });
}

async function checkSyncHost(conn) {
  const url = "/api/sync/connections/" + encodeURIComponent(conn.id) + "/host_key";
  const seen = await apiPost(url, {});
  if (!seen.ok) {
    showError(seen.error, conn.name);
    return;
  }
  const body = document.createElement("div");
  const intro = document.createElement("p");
  intro.textContent = t("sync.host_body", { host: conn.host });
  const list = document.createElement("ul");
  list.className = "sync-fingerprints";
  seen.data.fingerprints.forEach((line) => {
    const li = document.createElement("li");
    li.textContent = line;
    list.append(li);
  });
  body.append(intro, list);
  const answer = await openModal({ title: t("sync.host_title"), bodyNode: body, confirm: true,
                                   choices: [{ label: t("sync.host_accept"), value: true }] });
  if (answer !== true) return;
  const r = await apiPost(url, { fingerprints: seen.data.fingerprints });
  if (!r.ok) {
    showError(r.error, conn.name);
    return;
  }
  syncData = r.data;
  renderSync();
  showToast(t("sync.host_pinned", { name: conn.name }));
}

refreshSync();
watchSync();
refreshEvery(refreshSync, 30000, ["home/sync", "home/music"]);
refreshEvery(watchSync, 15000);
