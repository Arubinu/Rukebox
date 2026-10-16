function zoneOffsetLabel(zone) {
  try {
    const parts = new Intl.DateTimeFormat("en-US", { timeZone: zone, timeZoneName: "shortOffset" })
      .formatToParts(new Date());
    const name = (parts.find((p) => p.type === "timeZoneName") || {}).value || "";

    return name.replace(/^GMT/, "UTC");
  } catch (e) {
    return "";
  }
}

function zoneOption(zone, labelText) {
  const option = document.createElement("option");
  option.value = zone;
  option.textContent = labelText;
  return option;
}

function unsetOption() {
  const option = zoneOption("", t("clock.tz_unset"));
  option.dataset.unset = "1";
  return option;
}

function showCurrentTimezoneOnly(zone) {
  const select = document.getElementById("tzSelect");
  if (timezonesLoaded) return;
  select.innerHTML = "";
  select.appendChild(UNSET_ZONES.includes(zone) ? unsetOption() : zoneOption(zone, zone.replace(/_/g, " ")));
  select.value = UNSET_ZONES.includes(zone) ? "" : zone;
  timezoneShown = select.value;
}

function selectCurrentTimezone() {
  const select = document.getElementById("tzSelect");
  const unset = UNSET_ZONES.includes(timezoneCurrent || "");

  const existing = select.querySelector('option[data-unset="1"]');
  if (unset && !existing) select.insertBefore(unsetOption(), select.firstChild);
  if (!unset && existing) existing.remove();
  select.value = unset ? "" : timezoneCurrent;
  timezoneShown = select.value;
}

async function loadTimezones() {
  if (timezonesLoaded) return;
  timezonesLoaded = true;
  const result = await apiGet("/api/time/timezone");
  if (!result.ok) {
    timezonesLoaded = false;
    return;
  }
  timezoneCurrent = result.data.current || "";
  const mine = browserTimeZone();
  const groups = new Map();
  (result.data.zones || []).forEach((zone) => {
    if (UNSET_ZONES.includes(zone)) return;
    const cut = zone.indexOf("/");
    const region = cut > 0 ? zone.slice(0, cut) : t("clock.tz_other");
    const place = (cut > 0 ? zone.slice(cut + 1) : zone).replace(/_/g, " ");
    const offset = zoneOffsetLabel(zone);
    let text = place + (offset ? " (" + offset + ")" : "");
    if (zone === mine) text += " - " + t("clock.tz_this_device");
    if (!groups.has(region)) groups.set(region, []);
    groups.get(region).push(zoneOption(zone, text));
  });
  const select = document.getElementById("tzSelect");
  select.innerHTML = "";
  groups.forEach((options, region) => {
    const group = document.createElement("optgroup");
    group.label = region;
    options.forEach((o) => group.appendChild(o));
    select.appendChild(group);
  });
  selectCurrentTimezone();
}

document.getElementById("tzSection").addEventListener("toggle", (event) => {
  if (event.target.open) loadTimezones();
});

document.getElementById("tzSelect").addEventListener("focus", loadTimezones);

function syncTimezoneSelect(zone) {
  if (zone === undefined || zone === null) return;
  const select = document.getElementById("tzSelect");

  if (document.activeElement === select) return;
  if (timezoneShown !== null && select.value !== timezoneShown) return;
  timezoneCurrent = zone;
  if (timezonesLoaded) selectCurrentTimezone();
  else showCurrentTimezoneOnly(zone);
}

document.getElementById("btnSaveTz").addEventListener("click", async () => {
  const btn = document.getElementById("btnSaveTz");
  const value = document.getElementById("tzSelect").value;
  if (!value) {
    showError("missing_timezone");
    return;
  }
  if (value === timezoneCurrent) {
    showToast(t("alert.no_changes"));
    return;
  }
  btn.disabled = true;
  const result = await apiPost("/api/time/timezone", { timezone: value });
  btn.disabled = false;
  if (!result.ok) {
    showError(result.error, t("clock.timezone"));
    return;
  }
  timezoneCurrent = (result.data && result.data.current) || value;
  selectCurrentTimezone();
  showToast(t("clock.timezone_saved"), timezoneCurrent);

  refreshStatus();
});

const SCAN_POLL_MS = 900;
let scanPoller = null;
let scanPollInFlight = false;
let scanning = false;
let lastScanSignature = "";

function paintBattery(el, level, low) {
  el.hidden = level === null;
  if (level === null) return;
  const n = Math.max(0, Math.min(100, Math.round(level)));
  el.style.setProperty("--battery-level", n + "%");
  el.querySelector(".battery-gauge-pct").textContent = n + " %";
  el.dataset.state = low > 0 && n <= low ? "warn" : n <= 30 ? "mid" : "ok";
  el.setAttribute("aria-label", t("speaker.battery", { n }));
  el.title = t("speaker.battery", { n });
}

let lastKnownSpeakerMac = "";

function showToolError(title, result) {
  if (result.error === "quota_exceeded" && result.retry_after) {
    return showToast(title, t("err.quota_wait", { n: result.retry_after }), { error: true });
  }
  const reason = errorLabel(result.error) + (result.detail ? ` — ${result.detail}` : "");
  return showToast(title, reason, { error: true });
}

function setScanButton() {
  const btn = document.getElementById("btnBtScan");
  btn.dataset.i18n = scanning ? "speaker.scan_stop" : "speaker.scan";
  btn.textContent = t(btn.dataset.i18n);
}

function stopScanPolling() {
  if (scanPoller) {
    clearInterval(scanPoller);
    scanPoller = null;
  }
  scanning = false;
  setScanButton();
}

function openBtClockSection() {
  const section = document.getElementById("btClockSection");
  showDetailed();
  section.open = true;
  section.scrollIntoView({ behavior: "smooth", block: "center" });
}

function splitActions(container, left, right) {
  container.classList.add("btn-split");
  [left, right].forEach((buttons) => {
    const group = document.createElement("span");
    group.className = "btn-group";
    buttons.forEach((b) => group.appendChild(b));
    container.appendChild(group);
  });
}

function deviceRow(dev, running) {
  const li = document.createElement("li");
  if (running) li.classList.add("scanning");

  const label = document.createElement("span");
  const nameLine = document.createElement("span");
  nameLine.className = "device-name";
  nameLine.textContent = dev.name;
  label.appendChild(nameLine);

  const subLine = document.createElement("span");
  subLine.className = "device-sub";
  if (dev.name !== dev.mac) {
    const macLine = document.createElement("span");
    macLine.className = "device-mac";
    macLine.textContent = dev.mac;
    subLine.appendChild(macLine);
  }

  const states = [];
  if (dev.connected) states.push(["speaker.state_connected", "badge connected"]);
  else if (dev.paired) states.push(["speaker.state_paired", "badge"]);
  if (dev.kind === "other" || dev.kind === "input") states.push(["speaker.state_not_audio", "badge"]);
  if (remoteMacs.has(dev.mac.toUpperCase())) states.push(["remotes.badge", "badge connected"]);
  if (states.length) {
    const state = document.createElement("span");
    state.className = "device-state";
    states.forEach(([key, className]) => {
      const pill = document.createElement("span");
      pill.className = className;
      pill.textContent = t(key);
      state.appendChild(pill);
    });
    subLine.appendChild(state);
  }

  if (subLine.childNodes.length) label.appendChild(subLine);
  li.appendChild(label);

  const actions = document.createElement("span");
  actions.className = "device-actions";

  if (running) {
    li.classList.add("row-locked");
    li.addEventListener("click", () => {
      showToast(t("speaker.scan_running_title"), t("speaker.scan_running_hint"));
    });
    li.appendChild(actions);
    return li;
  }

  const pairBtn = document.createElement("button");
  pairBtn.className = dev.paired ? "btn btn-warning-outline" : "btn";
  pairBtn.textContent = t(dev.paired ? "speaker.forget" : "speaker.pair");

  pairBtn.addEventListener("click", async () => {
    if (dev.paired) {
      if (!await showConfirm(t("speaker.forget_confirm", { name: dev.name }))) return;
      pairBtn.disabled = true;
      const result = await apiPost("/api/bluetooth/forget", { mac: dev.mac });
      pairBtn.disabled = false;
      if (!result.ok) showToolError(t("common.failed"), result);
      else showToast(t("speaker.forgotten", { name: dev.name }), t("speaker.forgotten_hint"));
      lastScanSignature = "";
      refreshScanList();
      return;
    }
    pairBtn.disabled = true;

    const bubble = showToast(t("speaker.pair_wait_title"), t("speaker.pair_wait_hint"), { sticky: true });
    const result = await apiPost("/api/bluetooth/pair", { mac: dev.mac });
    dismissToast(bubble);
    pairBtn.disabled = false;
    if (!result.ok) showToolError(t("speaker.pair_failed"), result);
    else if (result.data && result.data.already) showToast(t("speaker.already_paired"));
    else showToast(t("speaker.paired_alert"));
    refreshScanList();
  });

  const speakerBtn = document.createElement("button");
  speakerBtn.className = "btn";
  speakerBtn.textContent = t("speaker.use_as_speaker");

  speakerBtn.disabled = dev.kind === "other" || dev.kind === "input";
  speakerBtn.addEventListener("click", async () => {
    speakerBtn.disabled = true;
    const result = await apiPost("/api/bluetooth/connect", { mac: dev.mac, set_as_speaker: true });
    speakerBtn.disabled = dev.kind === "other" || dev.kind === "input";
    if (result.ok) {
      showToast(t("speaker.connected_set_alert"));
    } else {
      showToolError(t("speaker.connect_failed"), result);
    }
    refreshStatus();
    refreshScanList();
  });

  const clockBtn = document.createElement("button");
  clockBtn.className = "btn";
  clockBtn.textContent = t("speaker.use_for_clock");

  clockBtn.addEventListener("click", () => {
    btClockMac.value = dev.mac;
    openBtClockSection();
  });

  const isRemote = remoteMacs.has(dev.mac.toUpperCase());
  const remoteBtn = document.createElement("button");
  remoteBtn.className = "btn";
  remoteBtn.textContent = t(isRemote ? "remotes.stop_using" : "remotes.use");
  remoteBtn.disabled = !dev.paired;
  remoteBtn.addEventListener("click", async () => {
    remoteBtn.disabled = true;
    const result = await apiPost("/api/bluetooth/remote", { mac: dev.mac, on: !isRemote });
    remoteBtn.disabled = !dev.paired;
    if (!result.ok) showError(result.error);
    else showToast(t(isRemote ? "remotes.removed" : "remotes.added"), isRemote ? "" : t("remotes.added_hint"));
    await refreshRemotes();
    refreshScanList();
  });

  // A remote is only offered where it can be one: an input device, or one BlueZ cannot classify.
  const roles = dev.kind === "input" ? [remoteBtn]
    : (dev.kind === "audio" || dev.kind === "other") && !isRemote ? [speakerBtn, clockBtn]
      : [speakerBtn, clockBtn, remoteBtn];
  splitActions(actions, [pairBtn], roles);
  li.appendChild(actions);
  return li;
}

let remoteMacs = new Set();

function remoteRow(remote) {
  const li = document.createElement("li");
  const label = document.createElement("span");
  const name = document.createElement("span");
  name.className = "device-name";
  name.textContent = remote.name;
  const sub = document.createElement("span");
  sub.className = "device-sub";
  const mac = document.createElement("span");
  mac.className = "device-mac";
  mac.textContent = remote.mac;
  const state = document.createElement("span");
  state.className = remote.connected ? "badge connected" : "badge";
  state.textContent = t(remote.connected ? "speaker.state_connected"
    : remote.paired ? "remotes.asleep" : "remotes.not_paired");
  sub.append(mac, state);
  label.append(name, sub);
  const actions = document.createElement("span");
  actions.className = "device-actions";
  const drop = document.createElement("button");
  drop.type = "button";
  drop.className = "btn btn-warning-outline";
  drop.textContent = t("remotes.remove");
  drop.addEventListener("click", async () => {
    drop.disabled = true;
    const result = await apiPost("/api/bluetooth/remote", { mac: remote.mac, on: false });
    drop.disabled = false;
    if (!result.ok) showError(result.error);
    refreshRemotes();
  });
  actions.appendChild(drop);
  li.append(label, actions);
  return li;
}

async function refreshRemotes() {
  const result = await apiGet("/api/bluetooth/remotes");
  if (!result.ok) return;
  const remotes = (result.data && result.data.remotes) || [];
  remoteMacs = new Set(remotes.map((one) => one.mac));
  const list = document.getElementById("remoteList");
  list.innerHTML = "";
  remotes.forEach((one) => list.appendChild(remoteRow(one)));
  list.hidden = !remotes.length;
  document.getElementById("remoteEmpty").hidden = remotes.length > 0;
  lastScanSignature = "";
}
refreshEvery(refreshRemotes, 15000, ["settings/buttons"]);

document.getElementById("remoteFind").addEventListener("click", () => {
  setActiveView("audio", "bluetooth");
  showToast(t("remotes.find_title"), t("remotes.find_hint"));
});

function renderDevices(devices, running, blocked) {
  const list = document.getElementById("deviceList");

  const shown = devices.filter((dev) => dev.named || dev.paired || dev.connected ||
    dev.mac === lastKnownSpeakerMac);

  const signature = (running ? "run|" : "stop|") + (blocked ? "blocked|" : "") +
    shown.map((d) => [d.mac, d.name, d.paired, d.connected, d.kind, d.named,
                      remoteMacs.has(d.mac.toUpperCase())].join("~")).join(",");
  if (signature === lastScanSignature) return;
  lastScanSignature = signature;

  list.innerHTML = "";
  if (!shown.length) {
    const li = document.createElement("li");
    li.textContent = running ? t("speaker.scanning")
      : (blocked ? t("speaker.bt_blocked") : t("speaker.none_found"));
    list.appendChild(li);
    return;
  }
  shown.forEach((dev) => list.appendChild(deviceRow(dev, running)));
}

async function refreshScanList() {
  if (scanPollInFlight) return;
  scanPollInFlight = true;
  try {
    const result = await apiGet("/api/bluetooth/scan/status");
    if (!result.ok) {
      stopScanPolling();
      return;
    }
    const data = result.data || {};
    renderDevices(data.devices || [], data.running === true, data.blocked === true);
    if (!data.running) stopScanPolling();
  } finally {
    scanPollInFlight = false;
  }
}

document.getElementById("btnSpeakerConnect").addEventListener("click", async () => {
  const status = await apiGet("/api/status");
  const mac = status.data && status.data.speaker_mac;
  if (!mac || mac === "XX:XX:XX:XX:XX:XX") {
    showToast(t("speaker.none_configured"));
    return;
  }
  const connected = status.data && status.data.speaker_connected === true;
  const result = await apiPost("/api/bluetooth/connect", { mac, reconnect: connected });
  if (result.ok) {
    showToast(connected ? t("speaker.reconnected_alert") : t("speaker.connected_alert"));
  } else {
    showToolError(t("speaker.connect_failed"), result);
  }
  refreshStatus();
});

document.getElementById("btnSpeakerDisconnect").addEventListener("click", async () => {
  const status = await apiGet("/api/status");
  const mac = status.data && status.data.speaker_mac;
  if (!mac) return;
  await apiPost("/api/bluetooth/disconnect", { mac });
  refreshStatus();
});

document.getElementById("btnAudioRestart").addEventListener("click", async () => {
  const btn = document.getElementById("btnAudioRestart");
  btn.disabled = true;
  btn.dataset.i18n = "audio.restarting";
  btn.textContent = t("audio.restarting");
  const result = await apiPost("/api/audio/restart");
  btn.disabled = false;
  btn.dataset.i18n = "audio.restart";
  btn.textContent = t("audio.restart");
  if (result.ok) {
    const sink = result.data && result.data.sink;
    showToast(t("audio.restarted"), sink ? t("audio.output_name", { name: sink }) : "");
  } else {
    showToolError(t("audio.restart_failed"), result);
  }
  refreshStatus();
});

document.getElementById("btnBtScan").addEventListener("click", async () => {
  if (scanning) {
    await apiPost("/api/bluetooth/scan/stop");
    await refreshScanList();
    return;
  }
  const result = await apiPost("/api/bluetooth/scan/start");
  if (!result.ok) {
    showError(result.error);
    return;
  }
  scanning = true;
  lastScanSignature = "";
  setScanButton();
  renderDevices([], true);
  refreshScanList();
  scanPoller = setInterval(refreshScanList, SCAN_POLL_MS);
});

/* The two daily times are what those folds are about, so each one carries its own in
   its summary - and hides it when the fold's own switch says it is not in use. */
function paintDailyTimes() {
  const cutoffOn = document.getElementById("cutoffEnabled").checked;
  const cutoff = document.getElementById("cutoffTime");
  const cutoffOut = document.getElementById("eveningTime");
  const start = document.getElementById("musicStartTime");
  const startOut = document.getElementById("morningTime");
  cutoffOut.textContent = cutoff.value;
  cutoffOut.hidden = !cutoffOn;
  startOut.textContent = start.value;
  startOut.hidden = document.getElementById("musicStartMode").value !== "scheduled";
}

function updateStartTimeVisibility() {
  const select = document.getElementById("musicStartMode");
  document.getElementById("musicStartTimeRow").hidden = select.value !== "scheduled";

  document.getElementById("volumeFadeRow").hidden = document.getElementById("volumeChange").value !== "fade";

  document.getElementById("transferLimitUsbRow").hidden = document.getElementById("transferLimitMode").value !== "auto";

  document.getElementById("audioFallbackRow").hidden = document.getElementById("audioOutputSelect").value !== "bluetooth";
  const snapcastChosen = document.getElementById("audioOutputSelect").value === "snapcast";
  document.getElementById("snapcastRow").hidden = !snapcastChosen;
  document.getElementById("snapcastCodecRow").hidden = !snapcastChosen;

  // The codecs are a Bluetooth question: another output has none to negotiate.
  document.getElementById("btCodecsRow").hidden =
    document.getElementById("audioOutputSelect").value !== "bluetooth" || !canDo("bluetooth");

  const cutoff = document.getElementById("cutoffEnabled").checked;
  ["cutoffTime", "cutoffWarning", "cutoffMode", "afterCutoff"].forEach((id) => {
    document.getElementById(id).closest(".field-row").hidden = !cutoff;
  });

  paintDailyTimes();
}
document.getElementById("volumeChange").addEventListener("change", updateStartTimeVisibility);
document.getElementById("cutoffTime").addEventListener("input", paintDailyTimes);
document.getElementById("musicStartTime").addEventListener("input", paintDailyTimes);
document.getElementById("transferLimitMode").addEventListener("change", updateStartTimeVisibility);
document.getElementById("audioOutputSelect").addEventListener("change", updateStartTimeVisibility);
document.getElementById("cutoffEnabled").addEventListener("change", updateStartTimeVisibility);

document.getElementById("musicStartMode").addEventListener("change", updateStartTimeVisibility);

function updateApPasswordRowVisibility() {
  const open = document.getElementById("apOpen").checked;
  document.getElementById("apPasswordRow").hidden = open;
  if (open) document.getElementById("apPassword").value = "";
}

document.getElementById("apOpen").addEventListener("change", updateApPasswordRowVisibility);

// What the access point holds now: a save that leaves it as it is must not restart it.
let apShown = null;

async function refreshApCard() {
  const result = await apiGet("/api/wifi/ap");
  const statusBox = document.getElementById("apStatus");
  if (!result.ok || !result.data.configured) {
    statusBox.textContent = t("ap.status_none");
    return;
  }
  apShown = { ssid: result.data.ssid, open: !result.data.password_set };
  const ssidField = document.getElementById("apSsid");

  if (document.activeElement !== ssidField) {
    ssidField.value = result.data.ssid;
  }

  const openBox = document.getElementById("apOpen");
  const pwField = document.getElementById("apPassword");
  if (document.activeElement !== openBox && document.activeElement !== pwField) {
    openBox.checked = !result.data.password_set;
    updateApPasswordRowVisibility();
  }
  statusBox.textContent = result.data.password_set
    ? t("ap.status_secured", { ssid: result.data.ssid })
    : t("ap.status_open", { ssid: result.data.ssid });
}

refreshApCard();
refreshEvery(refreshApCard, 10000, ["network/accesspoint"]);

document.getElementById("apForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const ssid = document.getElementById("apSsid").value.trim();
  const open = document.getElementById("apOpen").checked;
  const password = document.getElementById("apPassword").value;
  const btn = document.getElementById("apSaveBtn");

  if (btn.disabled) return;
  const updates = collectChangedSettings(e.target);
  const apChanged = !apShown || ssid !== apShown.ssid || open !== apShown.open || password !== "";
  if (!apChanged && Object.keys(updates).length === 0) {
    showToast(t("alert.no_changes"));
    return;
  }
  btn.disabled = true;
  try {
    // The settings first: saving the access point may cut this very connection.
    if (Object.keys(updates).length) {
      const saved = await apiPost("/api/settings", updates);
      if (!saved.ok) {
        showError(saved.error);
        return;
      }
      Object.assign(settingsBaseline, updates);
      if (!apChanged) {
        showToast(t("alert.settings_applied"));
        return;
      }
    }
    const result = await apiPost("/api/wifi/ap", { ssid, open, password });
    if (!result.ok) {
      const messages = {
        ssid_required: t("ap.ssid_required"),
        password_required: t("ap.password_required"),
        password_too_short: t("ap.password_too_short"),
        ap_save_in_progress: t("ap.save_in_progress"),
      };
      showToast(t("common.failed"), messages[result.error] || errorLabel(result.error), { error: true });
      return;
    }
    document.getElementById("apPassword").value = "";

    refreshApCard();
    showToast(result.password_set ? t("ap.saved_secured") : t("ap.saved_open"));
  } finally {
    btn.disabled = false;
  }
});

let homeWifiToggleBusy = false;
let homeWifiEdits = 0;
let homeWifiClientHere = false;

async function refreshHomeWifi() {
  if (homeWifiToggleBusy) return;
  const edits = homeWifiEdits;
  const result = await apiGet("/api/wifi/status");
  // Asked before a switch was touched: this answer is older than what the switch says.
  if (homeWifiToggleBusy || edits !== homeWifiEdits) return;
  const card = document.getElementById("homeWifiCard");
  if (!result.ok || !result.data.configured) {
    card.hidden = true;
    return;
  }
  card.hidden = false;

  const toggle = document.getElementById("homeWifiToggle");
  toggle.checked = !!result.data.active;

  const statusBox = document.getElementById("homeWifiStatus");
  statusBox.textContent = result.data.active
    ? t("wifi.connected") + (result.data.ip_address ? t("wifi.ip_label", { ip: result.data.ip_address }) : "")
    : t("wifi.disconnected");
  statusBox.dataset.state = result.data.active ? "ok" : "off";
  document.getElementById("homeWifiAuto").checked = !!result.data.autoconnect;

  homeWifiClientHere = !!result.data.client_here;
  document.getElementById("homeWifiHere").hidden = !homeWifiClientHere;
}

document.getElementById("homeWifiAuto").addEventListener("change", async (e) => {
  const on = e.target.checked;
  homeWifiEdits += 1;
  homeWifiToggleBusy = true;
  const result = await apiPost("/api/wifi/autoconnect", { on });
  homeWifiToggleBusy = false;
  if (!result.ok) {
    e.target.checked = !on;
    showToolError(t("common.failed"), result);
    return;
  }
  e.target.checked = on;
  showToast(t(on ? "wifi.autoconnect_on" : "wifi.autoconnect_off"));
});

refreshHomeWifi();
refreshEvery(refreshHomeWifi, 6000, ["network/homewifi"]);

document.getElementById("homeWifiToggle").addEventListener("change", async (e) => {
  if (!e.target.checked && homeWifiClientHere) {
    const go = await showConfirm(t("wifi.confirm_cut"));
    if (!go) {
      e.target.checked = true;
      return;
    }
  }
  homeWifiEdits += 1;
  homeWifiToggleBusy = true;
  const result = await apiPost("/api/wifi/toggle", { enabled: e.target.checked });
  if (!result.ok) {
    showToolError(t("common.failed"), result);
    e.target.checked = !e.target.checked;
  }
  homeWifiToggleBusy = false;
  refreshHomeWifi();
});

document.getElementById("sshToggle").addEventListener("change", async (e) => {
  const result = await apiPost("/api/ssh", { enabled: e.target.checked });
  if (!result.ok) {
    showError(result.error);
    e.target.checked = !e.target.checked;
  }
});

