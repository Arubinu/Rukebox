function duplicateCopy(copy, group) {
  const li = document.createElement("li");
  li.className = "duplicate-copy" + (copy.hidden ? " is-hidden" : "");
  const main = document.createElement("span");
  main.className = "recent-main";
  const title = document.createElement("span");
  title.className = "recent-title duplicate-name";
  title.setAttribute("role", "button");
  title.tabIndex = 0;
  title.dataset.name = copy.name;
  title.dataset.rel = copy.rel || copy.name;
  title.textContent = duplicatesShowPath ? title.dataset.rel : copy.name;
  // One tap on any name shows every copy's place in the music folder; another, the names again.
  const flip = () => {
    duplicatesShowPath = !duplicatesShowPath;
    document.querySelectorAll("#duplicatesList .duplicate-name").forEach((el) => {
      el.textContent = duplicatesShowPath ? el.dataset.rel : el.dataset.name;
    });
  };
  title.addEventListener("click", flip);
  title.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      flip();
    }
  });
  const facts = document.createElement("span");
  facts.className = "recent-artist";
  facts.textContent = duplicateFacts(copy);
  main.append(title, facts);
  li.append(main, libraryButton(copy, false), duplicateKeepButton(copy, group));
  return li;
}

function duplicateKeepButton(copy, group) {
  // Each copy on its own: several versions of a song may all be kept, never none of them.
  const kept = group.tracks.filter((one) => !one.hidden).length;
  const b = document.createElement("button");
  b.type = "button";
  b.className = "btn btn-small duplicate-keep" + (copy.hidden ? "" : " is-kept");
  b.dataset.icon = copy.hidden ? "x" : "check";
  const label = document.createElement("span");
  label.textContent = t(copy.hidden ? "duplicates.set_aside" : "duplicates.kept_state");
  b.append(label);
  b.title = t(copy.hidden ? "duplicates.restore_aria" : "duplicates.set_aside_aria", { name: copy.name });
  b.setAttribute("aria-label", b.title);
  b.setAttribute("aria-pressed", copy.hidden ? "false" : "true");
  b.dataset.costAction = "";
  if (!copy.hidden && kept <= 1) {
    b.disabled = true;
    b.title = t("duplicates.last_kept");
  }
  b.addEventListener("click", async () => {
    b.disabled = true;
    const asked = await apiPost("/api/library/hide", { key: copy.key, hidden: !copy.hidden, path: copy.path });
    b.disabled = false;
    if (!asked.ok) {
      showToolError(t("duplicates.failed"), asked);
      return;
    }
    refreshDuplicates();
  });
  return b;
}

function duplicateIgnoreButton(group) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "btn btn-small";
  b.dataset.icon = group.ignored ? "refresh" : "ban";
  b.textContent = t(group.ignored ? "duplicates.unignore" : "duplicates.ignore");
  b.addEventListener("click", async () => {
    b.disabled = true;
    const asked = await apiPost("/api/library/duplicates/ignore", { key: group.key, ignored: !group.ignored });
    b.disabled = false;
    if (!asked.ok) {
      showToolError(t("duplicates.failed"), asked);
      return;
    }
    refreshDuplicates();
  });
  return b;
}

const openDuplicateGroups = new Set();

function renderDuplicates() {
  const list = document.getElementById("duplicatesList");
  const summary = document.getElementById("duplicatesSummary");
  const empty = document.getElementById("duplicatesEmpty");
  empty.hidden = duplicateGroups.length > 0;
  summary.textContent = duplicateGroups.length ? t("duplicates.summary", {
    groups: duplicateGroups.length,
    tracks: duplicateGroups.reduce((total, group) => total + group.tracks.length, 0),
    size: formatBytes(duplicateGroups.reduce(
      (total, group) => total + (group.reclaimable || 0), 0)),
  }) : "";
  list.replaceChildren(...duplicateGroups.map((group) => {
    const li = document.createElement("li");
    li.className = "duplicate-group";
    const fold = document.createElement("details");
    fold.className = "duplicate-fold";
    fold.open = openDuplicateGroups.has(duplicateKey(group));
    fold.addEventListener("toggle", () => {
      if (fold.open) openDuplicateGroups.add(duplicateKey(group));
      else openDuplicateGroups.delete(duplicateKey(group));
    });
    const head = document.createElement("summary");
    head.className = "duplicate-head";
    const name = document.createElement("strong");
    name.textContent = [group.artist, group.title].filter(Boolean).join(" \u2014 ");
    const badge = document.createElement("span");
    // Green once looked at: said not to be duplicates, or a copy set aside.
    badge.className = "badge" + (group.done ? " duplicate-done" : group.same_file ? " duplicate-same" : "");
    badge.textContent = group.ignored ? t("duplicates.ignored_badge")
      : group.same_file ? t("duplicates.same_file")
        : t("duplicates.copies", { n: group.tracks.length });
    head.append(name, badge);
    const copies = document.createElement("ul");
    copies.className = "duplicate-copies";
    copies.replaceChildren(...group.tracks.map((copy) => duplicateCopy(copy, group)));
    const actions = document.createElement("div");
    actions.className = "form-actions duplicate-actions";
    actions.append(duplicateIgnoreButton(group));
    fold.append(head, copies, actions);
    li.append(fold);
    return li;
  }));
}

function duplicateKey(group) {
  return (group.artist || "") + "\u0000" + (group.title || "");
}

function duplicatesAvailable() {
  return !guestMode;
}

async function refreshDuplicates() {
  const card = document.getElementById("duplicatesCard");
  if (!duplicatesAvailable()) {
    card.hidden = true;
    return;
  }
  const result = await apiGet("/api/library/duplicates");
  if (!result.ok || !result.data) {
    card.hidden = true;
    return;
  }
  duplicateGroups = result.data.groups || [];
  duplicatesAsked = true;
  card.hidden = duplicateGroups.length === 0;
  renderDuplicates();
}

window.LANG_CHANGE_LISTENERS.push(() => {
  if (duplicatesAsked) renderDuplicates();
});

// Run once at startup: a card still hidden has no tile, so its data would never arrive.
refreshDuplicates();

let btControllersDirty = false;

function controllerLabel(c) {
  const kind = c.bus === "usb" ? t("btctl.kind_usb") : t("btctl.kind_builtin");
  return kind + (c.model ? " \u00b7 " + c.model : "") + " (" + c.name + ")";
}

async function refreshBtControllers() {
  const r = await apiGet("/api/bluetooth/controllers");
  if (!r.ok || !r.data) return;
  const d = r.data;
  document.getElementById("btControllerList").replaceChildren(...d.controllers.map((c) => {
    const li = document.createElement("li");
    const main = trackMain({ title: controllerLabel(c), artist: c.address || "" });
    li.append(main);
    const roles = [];
    if (c.address && c.address === d.speaker) roles.push(t("btctl.role_speaker"));
    if (c.address && c.address === d.flic && flicState && flicState.enabled) roles.push(t("btctl.role_flic"));
    if (roles.length) {
      const tag = document.createElement("span");
      tag.className = "upnext-tag";
      tag.textContent = roles.join(" \u00b7 ");
      li.append(tag);
    }
    return li;
  }));
  if (btControllersDirty) return;
  const speaker = document.getElementById("btSpeakerController");
  const flic = document.getElementById("btFlicController");
  const option = (value, label) => {
    const o = document.createElement("option");
    o.value = value;
    o.textContent = label;
    return o;
  };
  speaker.replaceChildren(option("", t("btctl.automatic")),
    ...d.controllers.filter((c) => c.address).map((c) => option(c.address, controllerLabel(c))));
  speaker.value = d.speaker_setting ? (d.speaker || "") : "";
  flic.replaceChildren(...d.controllers.filter((c) => c.address).map((c) => option(c.address, controllerLabel(c))));
  if (d.flic) flic.value = d.flic;
}

["btSpeakerController", "btFlicController"].forEach((id) => {
  document.getElementById(id).addEventListener("change", () => { btControllersDirty = true; });
});

document.getElementById("btControllersForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const speaker = document.getElementById("btSpeakerController").value;
  const flic = document.getElementById("btFlicController").value;
  if (flicState && flicState.enabled && flic && speaker === flic) {
    showError("bt_adapter_conflict");
    return;
  }
  const r = await apiPost("/api/settings", { "bluetooth.adapter": speaker, "bluetooth.flic_hci_device": flic });
  if (!r.ok) {
    showToolError(t("common.failed"), r);
    return;
  }
  btControllersDirty = false;
  settingsBaseline["bluetooth.adapter"] = speaker;
  settingsBaseline["bluetooth.flic_hci_device"] = flic;
  showToast(t("alert.settings_saved"), t("btctl.saved_detail"), {
    action: { label: t("settings.restart_now"), icon: "restart", run: restartDaemon },
  });
  refreshBtControllers();
});

let flicState = null;

async function refreshFlic() {
  const r = await apiGet("/api/flic/status");
  if (!r.ok || !r.data) return;
  const d = r.data;
  flicState = d;
  const line = document.getElementById("flicStatusLine");
  let text;
  const blocked = !d.usable;
  // On hold: on, but the radio stopped it to keep the only controller for the speaker.
  const held = !!d.held;
  if (held) text = t("btctl.flic_blocked_on");
  else if (blocked) text = t("btctl.flic_" + (d.unusable_reason || "single_controller"));
  else if (!d.sdk) text = t("btctl.flic_no_sdk");
  else if (!d.enabled) text = t("btctl.flic_off");
  else if (d.idle) text = t("btctl.flic_idle");
  else if (!d.active) text = t("btctl.flic_not_running");
  else text = t("btctl.flic_running", { n: (d.buttons || []).length });
  line.textContent = text;
  line.dataset.state = blocked ? "warn" : "";
  document.getElementById("btnFlicInstall").hidden = d.sdk || blocked;
  document.getElementById("flicEnabledRow").hidden = !d.sdk || (blocked && !d.enabled);
  document.getElementById("flicEnabledRow").classList.toggle("is-held", held);
  document.getElementById("flicEnabled").checked = !!d.enabled;
  document.getElementById("btnFlicPair").hidden = !(d.sdk && (d.active || d.idle)) || blocked;
  document.getElementById("btFlicController").disabled = blocked;
  document.getElementById("flicTitle").hidden = !(d.buttons || []).length;
  document.getElementById("flicButtonList").replaceChildren(...(d.buttons || []).map((address) => {
    const li = document.createElement("li");
    li.append(trackMain({ title: t("btctl.flic_button"), artist: address.toUpperCase() }));
    const forget = document.createElement("button");
    forget.type = "button";
    forget.className = "btn btn-small btn-danger-outline";
    forget.dataset.icon = "trash";
    forget.textContent = t("btctl.flic_forget");
    forget.addEventListener("click", async () => {
      if (!(await showConfirm(t("btctl.flic_forget_confirm", { address: address.toUpperCase() })))) return;
      const res = await apiPost("/api/flic/buttons/delete", { address });
      if (!res.ok) showToolError(t("common.failed"), res);
      refreshFlic();
    });
    li.append(forget);
    return li;
  }));
}

document.getElementById("btnFlicInstall").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true;
  btn.dataset.i18n = "btctl.flic_installing";
  btn.textContent = t("btctl.flic_installing");
  const r = await apiPost("/api/flic/install");
  btn.disabled = false;
  btn.dataset.i18n = "btctl.flic_install";
  btn.textContent = t("btctl.flic_install");
  if (!r.ok) showToolError(t("btctl.flic_install_failed"), r);
  else showToast(t("btctl.flic_installed"));
  refreshFlic();
});

document.getElementById("flicEnabled").addEventListener("change", async (e) => {
  const on = e.target.checked;
  if (on && flicState && flicState.takes_only_controller
      && !(await showConfirm(t("btctl.flic_only_controller"), t("btctl.flic_only_controller_title")))) {
    e.target.checked = false;
    return;
  }
  const r = await apiPost("/api/flic/enable", { on });
  if (!r.ok) {
    e.target.checked = !on;
    showToolError(t("common.failed"), r);
    return;
  }
  showToast(t(on ? "btctl.flic_enabled_on" : "btctl.flic_enabled_off"));
  setTimeout(() => { refreshFlic(); refreshBtControllers(); }, 2500);
});

document.getElementById("btnFlicPair").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  const r = await apiPost("/api/flic/pair/start");
  if (!r.ok) {
    showToolError(t("common.failed"), r);
    return;
  }
  btn.disabled = true;
  const hint = showToast(t("btctl.flic_pair_title"), t("btctl.flic_pair_hold"), {
    sticky: true,
    action: { label: t("common.cancel"), icon: "x", run: () => apiPost("/api/flic/pair/cancel") },
  });
  let finished = false;
  for (let i = 0; i < 90; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 1000));
    const s = await apiGet("/api/flic/pair/status");
    if (!s.ok || !s.data) continue;
    if (s.data.state === "private") {
      hint.querySelector(".toast-detail").textContent = t("btctl.flic_pair_private");
    } else if (s.data.state === "found" || s.data.state === "connected") {
      hint.querySelector(".toast-detail").textContent = t("btctl.flic_pair_found");
    } else if (s.data.state === "done") {
      finished = true;
      dismissToast(hint);
      if (s.data.result === "WizardSuccess") {
        showToast(t("btctl.flic_paired"), (s.data.address || "").toUpperCase());
      } else if (s.data.result !== "WizardCancelledByUser") {
        showToast(t("common.failed"), t("btctl.flic_pair_" + s.data.result) || s.data.result, { error: true });
      }
      break;
    }
  }
  btn.disabled = false;
  if (!finished) {
    dismissToast(hint);
    await apiPost("/api/flic/pair/cancel");
    showToast(t("common.failed"), t("btctl.flic_pair_WizardFailedTimeout"), { error: true });
  }
  refreshFlic();
});

document.getElementById("btControllersSection").addEventListener("toggle", (e) => {
  if (e.target.open) {
    btControllersDirty = false;
    refreshFlic().then(refreshBtControllers);
  }
});
setInterval(() => {
  if (document.getElementById("btControllersSection").open && !document.hidden) {
    refreshFlic().then(refreshBtControllers);
  }
}, 10000);

async function refreshToday() {
  const card = document.getElementById("todayCard");
  const r = await apiGet("/api/today");
  if (!r.ok || !r.data || !r.data.enabled) {
    card.hidden = true;
    return;
  }
  card.hidden = false;
  const d = r.data;
  const minutes = Math.round((d.seconds_music || 0) / 60);
  const figures = [
    ["today.music", minutes >= 60 ? Math.floor(minutes / 60) + " " + t("unit.hour") + " "
      + String(minutes % 60).padStart(2, "0") : minutes + " " + t("unit.min")],
    ["today.tracks", String(Math.round(d.tracks_played || 0))],
    ["today.sounds", String(Math.round(d.sounds_played || 0))],
    ["today.clicks", String(Math.round(d.clicks || 0))],
  ];
  document.getElementById("todayFigures").replaceChildren(...figures.map(([key, value]) => {
    const box = document.createElement("div");
    const dd = document.createElement("dd");
    dd.textContent = value;
    const dt = document.createElement("dt");
    dt.dataset.i18n = key;
    dt.textContent = t(key);
    box.append(dd, dt);
    return box;
  }));
  const top = d.top || [];
  document.getElementById("todayTopTitle").hidden = !top.length;
  document.getElementById("todayTop").replaceChildren(...top.map((item) => {
    const li = document.createElement("li");
    const title = document.createElement("span");
    title.textContent = item.title + (item.artist ? " - " + item.artist : "");
    const n = document.createElement("span");
    n.className = "recap-count";
    n.textContent = t(item.count === 1 ? "recap.plays_one" : "recap.plays", { n: item.count });
    li.append(title, n);
    return li;
  }));
}
refreshToday();
refreshEvery(refreshToday, 120000, ["home/today"]);

let upnextSignature;

function trackMain(item) {
  const main = document.createElement("span");
  main.className = "recent-main";
  const title = document.createElement("span");
  title.className = "recent-title";
  title.textContent = item.title || item.name || "\u2014";
  main.append(title);
  const sub = [item.artist, item.album].filter(Boolean).join(" \u00b7 ");
  if (sub) {
    const artist = document.createElement("span");
    artist.className = "recent-artist";
    artist.textContent = sub;
    main.append(artist);
  }
  return main;
}

