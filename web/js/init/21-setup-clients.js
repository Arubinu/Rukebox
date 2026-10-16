function goToCard(tab, titleId) {
  const title = titleId ? document.getElementById(titleId) : null;
  const card = title && title.closest(".card[data-page]");
  setActiveView(tab, card ? card.dataset.page : null);
  if (card) setTimeout(() => card.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
}

async function saveSetupHidden(list) {
  const result = await apiPost("/api/settings", { "network.setup_hidden": list.join(",") });
  if (!result.ok) showError(result.error);
  refreshSetup();
}

/* The version of the interface this page loaded, as the radio served it then.
   Another one later means the radio was updated while the page stayed open. */
let webVersionLoaded = null;

/* A reload revalidates every file on its own; this asks for fresh copies first and
   brings the service worker up to date, for a page kept open across an update. */
async function reloadWithFreshCache() {
  try {
    await Promise.all(["/", "/app.js", "/style.css", "/i18n.js", "/qr.js"]
      .map((name) => fetch(name, { cache: "reload" })));
    if ("serviceWorker" in navigator) {
      const registration = await navigator.serviceWorker.getRegistration();
      if (registration) await registration.update();
    }
    if (window.caches) {
      await Promise.all((await caches.keys()).map((name) => caches.delete(name)));
    }
  } catch (e) {  }
  location.reload();
}

function setupUpdateRow() {
  const li = document.createElement("li");
  li.id = "setupWebUpdate";
  li.dataset.icon = "refresh";
  const text = document.createElement("span");
  text.className = "setup-text";
  text.dataset.i18n = "setup.web_updated";
  text.textContent = t("setup.web_updated");
  const group = document.createElement("div");
  group.className = "btn-group";
  const again = document.createElement("button");
  again.type = "button";
  again.className = "btn btn-small btn-primary";
  again.dataset.i18n = "setup.web_updated_action";
  again.textContent = t("setup.web_updated_action");
  again.addEventListener("click", () => {
    again.disabled = true;
    reloadWithFreshCache();
  });
  group.append(again);
  li.append(text, group);
  return li;
}

async function refreshSetup() {
  const result = await apiGet("/api/setup/pending");
  const card = document.getElementById("setupCard");
  if (!result.ok || !result.data) {
    card.hidden = true;
    return;
  }
  const items = (result.data.items || []).filter((id) => SETUP_TARGETS[id]);
  setupHidden = result.data.hidden || [];
  const version = result.data.web_version || "";
  if (webVersionLoaded === null) webVersionLoaded = version;
  const rows = items.map((id) => {
    // Where the clock cannot be set (a container), the zone is read in the
    // health card: the Clock page is not offered there at all.
    const [tab, titleId, icon] = id === "timezone" && !canDo("set_clock")
      ? ["system", "healthTitle", "chip"] : SETUP_TARGETS[id];
    const li = document.createElement("li");
    li.dataset.icon = icon;
    const text = document.createElement("span");
    text.className = "setup-text";
    text.dataset.i18n = "setup." + id;
    text.textContent = t(text.dataset.i18n);
    const group = document.createElement("div");
    group.className = "btn-group";
    const hide = document.createElement("button");
    hide.type = "button";
    hide.className = "btn btn-small";
    hide.dataset.i18n = "setup.hide";
    hide.textContent = t("setup.hide");
    hide.addEventListener("click", () => saveSetupHidden(setupHidden.concat(id)));
    const go = document.createElement("button");
    go.type = "button";
    go.className = "btn btn-small btn-primary";
    go.dataset.i18n = "setup.go";
    go.textContent = t("setup.go");
    go.addEventListener("click", () => goToCard(tab, titleId));
    group.append(hide, go);
    li.append(text, group);
    return li;
  });
  // First: nothing else to finish matters until the page catches up with the radio.
  if (version && version !== webVersionLoaded) rows.unshift(setupUpdateRow());
  document.getElementById("setupList").replaceChildren(...rows);
  card.hidden = !rows.length;
  const line = document.getElementById("setupHiddenLine");
  line.hidden = !setupHidden.length;
  document.getElementById("setupHiddenText").textContent = t("setup.hidden_count", { n: setupHidden.length });
}
document.getElementById("setupShowHidden").addEventListener("click", () => saveSetupHidden([]));
refreshSetup();
refreshEvery(refreshSetup, 60000);

document.querySelectorAll(".cmd-copy").forEach((btn) => {
  btn.addEventListener("click", () => copyText(btn.parentElement.querySelector(".cmd-text").textContent, btn));
});

async function refreshShare() {
  const r = await apiGet("/api/wifi/ap/share");
  const card = document.getElementById("shareCard");
  if (!r.ok || !r.data) {
    card.hidden = true;
    return;
  }
  card.hidden = false;
  const d = r.data;

  const opts = { color: "#1e1b4b", background: "#ffffff" };
  const wifiBox = document.getElementById("shareWifiQr");
  if (d.configured) {
    wifiBox.innerHTML = RukeboxQR.svg(RukeboxQR.wifiText(d.ssid, d.open ? "" : d.password),
      Object.assign({ label: t("share.wifi") }, opts));
    document.getElementById("shareWifiText").textContent = d.open ? t("share.wifi_text_open", { ssid: d.ssid })
      : t("share.wifi_text", { ssid: d.ssid, password: d.password });
  } else {
    wifiBox.textContent = "";
    document.getElementById("shareWifiText").textContent = t("share.no_ap");
  }
  document.getElementById("shareUrlQr").innerHTML = RukeboxQR.svg(d.url, Object.assign({ label: t("share.open") }, opts));
  document.getElementById("shareUrlText").textContent = d.url + "\n" + d.local_url;
}
document.querySelectorAll(".install-btn").forEach((btn) => btn.addEventListener("click", () => {
  if (installPrompt) {
    installPrompt.prompt();
    installPrompt = null;
    return;
  }
  const standalone = window.matchMedia && window.matchMedia("(display-mode: standalone)").matches;
  openModal({ title: t("share.install"), body: t(standalone ? "share.installed" : installHowTo()) });
}));
document.getElementById("sharePrint").addEventListener("click", async () => {
  const which = await showChoice(t("share.print_which"), [
    { value: "wifi", label: t("share.wifi") },
    { value: "url", label: t("share.open") },
    { value: "both", label: t("share.print_both") },
    { value: "apart", label: t("share.print_apart") },
  ], t("share.print"));
  if (!which) return;
  document.body.dataset.print = which;
  document.body.classList.add("print-share");
  window.print();
});
window.addEventListener("afterprint", () => {
  document.body.classList.remove("print-share");
  delete document.body.dataset.print;
});
refreshShare();
document.querySelectorAll('.tab-btn[data-tab="network"]').forEach((btn) => btn.addEventListener("click", refreshShare));
window.LANG_CHANGE_LISTENERS.push(refreshShare);

const BAN_CHOICES = [[60, "clients.ban_1h"], [1440, "clients.ban_1d"], [10080, "clients.ban_7d"], [null, "clients.ban_forever"]];

function clientLabel(c) {
  return c.name || t("clients.unknown");
}

async function banDevice(target, label) {
  const choice = await showChoice(t("clients.ban_body", { name: label }),
    BAN_CHOICES.map(([minutes, key]) => ({ value: String(minutes), label: t(key), danger: minutes === null })),
    t("clients.ban"));
  if (choice === null || choice === false || choice === undefined) return;
  const r = await apiPost("/api/devices/ban", Object.assign({}, target, { minutes: choice === "null" ? null : Number(choice) }));
  if (!r.ok) showToolError(t("common.failed"), r);
  else showToast(t("clients.banned_done", { name: label }));
  refreshClients();
}

async function forgetDevice(target, label) {
  if (!await showConfirm(t("clients.forget_body", { name: label }), t("clients.forget"))) return;
  const r = await apiPost("/api/devices/forget", target);
  if (!r.ok) {
    showToolError(t("common.failed"), r);
    return;
  }
  showToast(t("clients.forgotten", { name: label }));
  reloadClientLists();
}

/* Every named device a list has shown, for the owner to link one to another. */
const knownPeople = new Map();

async function linkDevice(c) {
  const seen = new Set([c.person || c.device_id]);
  const choices = [];
  Array.from(knownPeople.values())
    .sort((a, b) => String(a.name).localeCompare(String(b.name)))
    .forEach((other) => {
      const person = other.person || other.device_id;
      if (seen.has(person)) return;
      seen.add(person);
      choices.push({ value: other.device_id, label: other.name });
    });
  if (!choices.length) {
    showToast(t("clients.link_nobody"));
    return;
  }
  const to = await showChoice(t("clients.link_body", { name: clientLabel(c) }), choices, t("clients.linked"));
  if (!to) return;
  const r = await apiPost("/api/devices/link", Object.assign(
    c.device_id ? { device_id: c.device_id } : { mac: c.mac }, { to }));
  if (!r.ok) {
    showError(r.error);
    return;
  }
  const target = knownPeople.get(to);
  showToast(t("clients.linked_done", { name: target ? target.name : "" }));
  reloadClientLists();
}

async function unlinkDevice(c) {
  if (!await showConfirm(t("clients.unlink_body", { name: clientLabel(c) }), t("clients.unlink_action"))) return;
  const r = await apiPost("/api/devices/unlink", { device_id: c.device_id });
  if (!r.ok) {
    showError(r.error);
    return;
  }
  showToast(t("clients.unlinked_done"));
  reloadClientLists();
}

function clientRow(c, options) {
  const li = document.createElement("li");
  const key = c.device_id || c.mac;
  if (c.device_id && c.name) knownPeople.set(c.device_id, { device_id: c.device_id, person: c.person, name: c.name });
  else if (c.device_id) knownPeople.delete(c.device_id);
  const linked = Array.isArray(c.linked) ? c.linked : [];
  // On the Rukebox's own access point, or seen talking to us from somewhere
  // else. The second case is the whole reason this list is not just `iw`.
  const onAp = c.on_ap === true;
  const main = document.createElement("span");
  main.className = "client-main";
  const name = document.createElement("span");
  name.className = "client-name";
  name.textContent = clientLabel(c) + (c.me ? " (" + t("clients.me") + ")" : "");
  const nameLine = document.createElement("span");
  nameLine.className = "client-name-line";
  nameLine.append(name);
  if (c.name_locked) {
    // Visible without opening the fold: the device may no longer rename itself.
    const glyph = document.createElement("span");
    glyph.className = "client-lock";
    glyph.dataset.icon = "lock";
    glyph.title = t("clients.name_locked");
    glyph.setAttribute("aria-hidden", "true");
    nameLine.append(glyph);
  }
  const meta = document.createElement("span");
  meta.className = "client-meta";
  const bits = [c.ip || null, c.mac];
  if (c.on_ap === false && c.seen_sec != null) bits.push(t("clients.via_home"));
  if (linked.length) bits.push(t("clients.linked_badge", { n: linked.length + 1 }));
  if (c.connected_sec != null) bits.push(t("clients.since", { t: formatUptime(c.connected_sec) }));
  if (c.signal != null) bits.push(c.signal + " dBm");
  const seenAgo = c.seen_sec != null ? c.seen_sec
    : (c.connected_sec == null && c.last_seen ? Math.max(0, Date.now() / 1000 - c.last_seen) : null);
  if (seenAgo != null) bits.push(t("clients.seen", { ago: formatAgo(seenAgo) }));
  meta.textContent = bits.filter(Boolean).join(" \u00b7 ");
  main.append(nameLine, meta);

  const fold = document.createElement("details");
  fold.className = "client-options";
  fold.open = openClientFolds.has(key);
  fold.addEventListener("toggle", () => {
    if (fold.open) openClientFolds.add(key);
    else openClientFolds.delete(key);
  });
  const summary = document.createElement("summary");
  summary.append(main);
  fold.append(summary);

  const nameRow = document.createElement("div");
  nameRow.className = "field-row";
  const nameText = document.createElement("div");
  nameText.className = "field-text";
  const nameLabel = document.createElement("span");
  nameLabel.className = "field-label";
  nameLabel.textContent = t("clients.rename");
  const nameDesc = document.createElement("p");
  nameDesc.className = "field-desc";
  nameDesc.textContent = t("clients.rename_hint");
  nameText.append(nameLabel, nameDesc);
  const nameField = document.createElement("div");
  nameField.className = "input-with-action";
  const nameInput = document.createElement("input");
  nameInput.type = "text";
  nameInput.className = "text-input client-name-input";
  nameInput.maxLength = 24;
  nameInput.value = clientNameDrafts.has(key) ? clientNameDrafts.get(key) : (c.name || "");
  nameInput.placeholder = clientLabel(c);
  nameInput.setAttribute("aria-label", t("clients.rename"));
  nameInput.addEventListener("input", () => clientNameDrafts.set(key, nameInput.value));
  const nameSave = document.createElement("button");
  nameSave.type = "button";
  nameSave.className = "btn btn-small";
  nameSave.textContent = t("clients.rename_action");
  const renameDevice = async () => {
    const value = nameInput.value.trim();
    if (!value) return;
    nameSave.disabled = true;
    const r = await apiPost("/api/devices/name", Object.assign(
      c.device_id ? { device_id: c.device_id } : { mac: c.mac }, { name: value }));
    nameSave.disabled = false;
    if (!r.ok) {
      showError(r.error);
      return;
    }
    c.name = r.data.name;
    clientNameDrafts.delete(key);
    showToast(t("clients.renamed", { name: r.data.name }));
    reloadClientLists();
  };
  nameSave.addEventListener("click", renameDevice);
  nameInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      renameDevice();
    }
  });
  nameField.append(nameInput, nameSave);
  nameRow.append(nameText, nameField);
  fold.append(nameRow);

  const lockRow = document.createElement("div");
  lockRow.className = "field-row";
  const lockText = document.createElement("div");
  lockText.className = "field-text";
  const lockLabel = document.createElement("span");
  lockLabel.className = "field-label";
  lockLabel.textContent = t("clients.name_locked");
  const lockDesc = document.createElement("p");
  lockDesc.className = "field-desc";
  lockDesc.textContent = t("clients.name_locked_hint");
  lockText.append(lockLabel, lockDesc);
  const lock = document.createElement("button");
  lock.type = "button";
  lock.className = "btn btn-small client-locked";
  lock.textContent = t(c.name_locked ? "common.disable" : "common.enable");
  lock.setAttribute("aria-pressed", c.name_locked ? "true" : "false");
  lock.addEventListener("click", async () => {
    const on = lock.getAttribute("aria-pressed") !== "true";
    lock.disabled = true;
    const r = await apiPost("/api/devices/name_locked", Object.assign(
      c.device_id ? { device_id: c.device_id } : { mac: c.mac }, { on }));
    lock.disabled = false;
    if (!r.ok) {
      showError(r.error);
      return;
    }
    c.name_locked = on;
    lock.setAttribute("aria-pressed", on ? "true" : "false");
    lock.textContent = t(on ? "common.disable" : "common.enable");
    reloadClientLists();
  });
  lockRow.append(lockText, lock);
  fold.append(lockRow);

  const linkRow = document.createElement("div");
  linkRow.className = "field-row";
  const linkText = document.createElement("div");
  linkText.className = "field-text";
  const linkLabel = document.createElement("span");
  linkLabel.className = "field-label";
  linkLabel.textContent = t("clients.linked");
  const linkDesc = document.createElement("p");
  linkDesc.className = "field-desc";
  linkDesc.textContent = linked.length
    ? t("clients.linked_with", { list: linked.map((d) => d.ip || d.mac || d.device_id).join(", ") })
    : t("clients.linked_none");
  linkText.append(linkLabel, linkDesc);
  const linkButtons = document.createElement("div");
  linkButtons.className = "client-link-buttons";
  const linkBtn = document.createElement("button");
  linkBtn.type = "button";
  linkBtn.className = "btn btn-small client-link";
  linkBtn.textContent = t("clients.link_action");
  linkBtn.addEventListener("click", () => linkDevice(c));
  linkButtons.append(linkBtn);
  if (linked.length) {
    const unlinkBtn = document.createElement("button");
    unlinkBtn.type = "button";
    unlinkBtn.className = "btn btn-small client-unlink";
    unlinkBtn.textContent = t("clients.unlink_action");
    unlinkBtn.addEventListener("click", () => unlinkDevice(c));
    linkButtons.append(unlinkBtn);
  }
  linkRow.append(linkText, linkButtons);
  fold.append(linkRow);

  const freeRow = document.createElement("div");
  freeRow.className = "field-row";
  const freeText = document.createElement("div");
  freeText.className = "field-text";
  const freeLabel = document.createElement("span");
  freeLabel.className = "field-label";
  freeLabel.textContent = t("clients.free_credits");
  const freeDesc = document.createElement("p");
  freeDesc.className = "field-desc";
  freeDesc.textContent = t("clients.free_credits_hint");
  freeText.append(freeLabel, freeDesc);
  const free = document.createElement("button");
  free.type = "button";
  free.className = "btn btn-small client-free";
  free.textContent = t(c.free_credits ? "common.disable" : "common.enable");
  free.setAttribute("aria-pressed", c.free_credits ? "true" : "false");
  free.addEventListener("click", async () => {
    const on = free.getAttribute("aria-pressed") !== "true";
    free.disabled = true;
    const r = await apiPost("/api/devices/free_credits", Object.assign(
      c.device_id ? { device_id: c.device_id } : { mac: c.mac }, { on }));
    free.disabled = false;
    if (!r.ok) {
      showToolError(t("common.failed"), r);
      return;
    }
    c.free_credits = on;
    free.setAttribute("aria-pressed", on ? "true" : "false");
    free.textContent = t(on ? "common.disable" : "common.enable");
    showToast(t(on ? "clients.free_on" : "clients.free_off", { name: clientLabel(c) }));
  });
  freeRow.append(freeText, free);
  fold.append(freeRow);

  if (c.device_id) {
    const voiceRow = document.createElement("div");
    voiceRow.className = "field-row";
    const voiceText = document.createElement("div");
    voiceText.className = "field-text";
    const voiceLabel = document.createElement("span");
    voiceLabel.className = "field-label";
    voiceLabel.textContent = t("clients.name_voice");
    const voiceDesc = document.createElement("p");
    voiceDesc.className = "field-desc";
    voiceDesc.textContent = t(c.name_voice ? "clients.name_voice_has" : "clients.name_voice_none");
    voiceText.append(voiceLabel, voiceDesc);
    const voiceBtn = document.createElement("button");
    voiceBtn.type = "button";
    voiceBtn.className = "btn btn-small client-name-voice";
    voiceBtn.textContent = t(c.name_voice ? "name_voice.edit" : "name_voice.add");
    voiceBtn.addEventListener("click", async () => {
      const changed = await editNameVoice({
        url: "/api/devices/name_voice?device_id=" + encodeURIComponent(c.device_id), has: !!c.name_voice,
        title: t("name_voice.owner_title", { name: clientLabel(c) }), hint: t("name_voice.owner_hint"), file: true,
      });
      if (changed) refreshClients();
    });
    voiceRow.append(voiceText, voiceBtn);
    fold.append(voiceRow);
  }

  const portalRow = document.createElement("div");
  portalRow.className = "field-row";
  const portalText = document.createElement("div");
  portalText.className = "field-text";
  const portalLabel = document.createElement("span");
  portalLabel.className = "field-label";
  portalLabel.textContent = t("clients.portal");
  const portalDesc = document.createElement("p");
  portalDesc.className = "field-desc";
  portalDesc.textContent = t(onAp ? "clients.portal_hint" : "clients.portal_hint_home");
  portalText.append(portalLabel, portalDesc);
  const portal = document.createElement("select");
  portal.setAttribute("aria-label", t("clients.portal"));
  [["auto", "clients.portal_auto"], ["always", "clients.portal_always"], ["never", "clients.portal_never"]].forEach(([v, k]) => {
    const o = document.createElement("option");
    o.value = v;
    o.textContent = t(k);
    portal.append(o);
  });
  portal.value = c.portal || "auto";
  portal.addEventListener("change", async () => {
    const r = await apiPost("/api/devices/portal", c.device_id ? { device_id: c.device_id, mode: portal.value }
      : { mac: c.mac, mode: portal.value });
    if (!r.ok) showToolError(t("common.failed"), r);
  });
  portalRow.append(portalText, portal);
  fold.append(portalRow);

  const againRow = document.createElement("div");
  againRow.className = "field-row";
  const againText = document.createElement("div");
  againText.className = "field-text";
  const againLabel = document.createElement("span");
  againLabel.className = "field-label";
  againLabel.textContent = t("clients.portal_again");
  const againDesc = document.createElement("p");
  againDesc.className = "field-desc";
  againDesc.textContent = t(c.portal_released ? "clients.portal_again_hint" : "clients.portal_again_none");
  againText.append(againLabel, againDesc);
  const again = document.createElement("button");
  again.type = "button";
  again.className = "btn btn-small";
  again.textContent = t("clients.portal_again_action");
  again.disabled = !c.portal_released;
  again.addEventListener("click", async () => {
    again.disabled = true;
    const r = await apiPost("/api/devices/portal_release",
      c.device_id ? { device_id: c.device_id } : { mac: c.mac });
    if (!r.ok) {
      again.disabled = false;
      showToolError(t("common.failed"), r);
      return;
    }
    c.portal_released = false;
    againDesc.textContent = t("clients.portal_again_none");
    showToast(t("clients.portal_again_done", { name: clientLabel(c) }));
    reloadClientLists();
  });
  againRow.append(againText, again);
  fold.append(againRow);

  if (!c.me) {
    // Disconnect is the access point's own list, so it is gone off the hotspot: Ban and
    // Forget share the line where it is absent.
    const buttons = document.createElement("div");
    const onALine = onAp || !!(options && options.forget);
    buttons.className = onALine ? "client-buttons" : "client-buttons is-single";
    if (onAp) {
      const kick = document.createElement("button");
      kick.type = "button";
      kick.className = "btn btn-small";
      kick.dataset.icon = "unlink";
      kick.textContent = t("clients.disconnect");
      kick.addEventListener("click", async () => {
        const r = await apiPost("/api/wifi/clients/disconnect", { mac: c.mac });
        if (!r.ok) showToolError(t("common.failed"), r);
        setTimeout(refreshClients, 1500);
      });
      buttons.append(kick);
    }
    const ban = document.createElement("button");
    ban.type = "button";
    ban.className = "btn btn-small btn-danger-outline";
    ban.dataset.icon = "ban";
    ban.textContent = t("clients.ban");
    ban.addEventListener("click", () => banDevice(c.device_id ? { device_id: c.device_id } : { mac: c.mac }, clientLabel(c)));
    buttons.append(ban);
    if (options && options.forget) {
      const forget = document.createElement("button");
      forget.type = "button";
      forget.className = "btn btn-small btn-danger-outline";
      forget.dataset.icon = "trash";
      forget.textContent = t("clients.forget");
      forget.addEventListener("click", () => forgetDevice(
        c.device_id ? { device_id: c.device_id } : { mac: c.mac }, clientLabel(c)));
      buttons.append(forget);
    }
    fold.append(buttons);
  }

  li.append(fold);
  return li;
}

function bannedRow(b) {
  const li = document.createElement("li");
  const main = document.createElement("div");
  main.className = "client-main";
  const name = document.createElement("span");
  name.className = "client-name";
  name.textContent = b.name || t("clients.unknown");
  const meta = document.createElement("span");
  meta.className = "client-meta";
  meta.textContent = (b.until === -1 ? t("clients.ban_forever")
    : t("clients.until", { time: formatDateTime(b.until) })) + " \u00b7 " + b.macs.join(", ")
    + (b.last_seen ? " \u00b7 " + t("clients.seen", { ago: formatAgo(Date.now() / 1000 - b.last_seen) }) : "");
  main.append(name, meta);
  const lift = document.createElement("button");
  lift.type = "button";
  lift.className = "btn btn-small";
  lift.textContent = t("clients.lift");
  lift.addEventListener("click", async () => {
    const r = await apiPost("/api/devices/ban", { device_id: b.device_id, lift: true });
    if (!r.ok) showToolError(t("common.failed"), r);
    reloadClientLists();
  });
  const actions = document.createElement("div");
  actions.className = "client-actions";
  actions.append(lift);
  li.append(main, actions);
  return li;
}

const PREVIOUS_FIRST_PAGE = 8;
const PREVIOUS_STEP = 20;
let nowDevices = [];
let nowReadable = true;
let previousDevices = [];
let previousShown = PREVIOUS_FIRST_PAGE;
let previousLoaded = false;
let bannedDevices = [];
let bannedLoaded = false;

/* One search box per list: it matches the name, the address and every MAC of the device. */
function searchNeedle(id) {
  return document.getElementById(id).value.trim().toLowerCase();
}

function matchesSearch(device, needle) {
  return !needle || [device.name, device.ip, device.mac].concat(device.macs || [])
    .some((value) => String(value || "").toLowerCase().includes(needle));
}

function searchSummary(id, matches, plain) {
  if (!searchNeedle(id)) return plain;
  return matches.length ? t("clients.search_found", { n: matches.length }) : t("clients.search_none");
}

async function refreshClients() {
  const r = await apiGet("/api/wifi/clients");
  if (!r.ok || !r.data) return;
  nowDevices = r.data.clients || [];
  nowReadable = r.data.readable !== false;
  renderClients();
}

function renderClients() {
  const matches = nowDevices.filter((d) => matchesSearch(d, searchNeedle("clientSearch")));
  document.getElementById("clientList").replaceChildren(...matches.map((d) => clientRow(d)));
  document.getElementById("clientsSummary").textContent = searchSummary("clientSearch", matches,
    nowDevices.length ? t("clients.count", { n: nowDevices.length })
      : t(nowReadable ? "clients.none" : "clients.unreadable"));
}

/* An action in one list refreshes the others the page has already loaded. */
function reloadClientLists() {
  refreshClients();
  if (previousLoaded) refreshPrevious();
  if (bannedLoaded) refreshBanned();
}

async function refreshPrevious() {
  const r = await apiGet("/api/devices/seen");
  if (!r.ok || !r.data) return;
  previousDevices = r.data.devices || [];
  previousShown = PREVIOUS_FIRST_PAGE;
  previousLoaded = true;
  renderPrevious();
}

function renderPrevious() {
  const needle = searchNeedle("previousSearch");
  const matches = previousDevices.filter((d) => matchesSearch(d, needle));
  const shown = needle ? matches : matches.slice(0, previousShown);
  document.getElementById("previousList").replaceChildren(...shown.map((d) => clientRow(d, { forget: true })));
  document.getElementById("previousSummary").textContent = !previousLoaded ? ""
    : searchSummary("previousSearch", matches, previousDevices.length
      ? t("clients.previous_count", { n: previousDevices.length }) : t("clients.previous_none"));
  document.getElementById("previousMore").hidden = !!needle || shown.length >= matches.length;
  const unnamed = previousDevices.filter((d) => !d.name).length;
  const sweep = document.getElementById("previousForgetUnnamed");
  sweep.hidden = !unnamed;
  sweep.dataset.count = String(unnamed);
}

document.getElementById("previousForgetUnnamed").addEventListener("click", async () => {
  const sweep = document.getElementById("previousForgetUnnamed");
  const count = Number(sweep.dataset.count || 0);
  if (!count) return;
  if (!await showConfirm(t("clients.forget_unnamed_body", { n: count }), t("clients.forget_unnamed"))) return;
  sweep.disabled = true;
  const r = await apiPost("/api/devices/forget", { unnamed: true });
  sweep.disabled = false;
  if (!r.ok) {
    showToolError(t("common.failed"), r);
    return;
  }
  showToast(t("clients.forgotten_many", { n: r.data.forgotten }));
  refreshPrevious();
});

async function refreshBanned() {
  const r = await apiGet("/api/devices/banned");
  if (!r.ok || !r.data) return;
  bannedDevices = r.data.banned || [];
  bannedLoaded = true;
  renderBanned();
}

function renderBanned() {
  const matches = bannedDevices.filter((d) => matchesSearch(d, searchNeedle("bannedSearch")));
  document.getElementById("bannedList").replaceChildren(...matches.map(bannedRow));
  document.getElementById("bannedSummary").textContent = searchSummary("bannedSearch", matches,
    bannedDevices.length ? t("clients.banned_count", { n: bannedDevices.length }) : t("clients.banned_none"));
}

document.getElementById("clientSearch").addEventListener("input", renderClients);
document.getElementById("previousSearch").addEventListener("input", renderPrevious);
document.getElementById("bannedSearch").addEventListener("input", renderBanned);
document.getElementById("previousMore").addEventListener("click", () => {
  previousShown += PREVIOUS_STEP;
  renderPrevious();
});
refreshClients();
setInterval(() => {
  // A rebuild would take the field out from under the keyboard, so the poll stands aside.
  if (document.body.dataset.tab === "network" && !document.hidden && !editingClientName()) refreshClients();
}, 15000);

function editingClientName() {
  const active = document.activeElement;
  return !!active && active.classList && active.classList.contains("client-name-input");
}
document.querySelectorAll('.tab-btn[data-tab="network"]').forEach((btn) => btn.addEventListener("click", refreshClients));
rememberDevice();

function formatUptime(seconds) {
  const s = Math.max(0, Math.round(Number(seconds) || 0));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d) return d + " " + t("unit.day") + " " + h + " " + t("unit.hour");
  if (h) return h + " " + t("unit.hour") + " " + String(m).padStart(2, "0") + " " + t("unit.min");
  if (m) return m + " " + t("unit.min");
  return s + " " + t("unit.sec");
}

function renderHealthInfo(info) {
  const rows = [];
  const add = (key, value, level) => { if (value !== null && value !== undefined && value !== "") rows.push([key, value, level]); };
  add("health.uptime", info.uptime != null ? formatUptime(info.uptime) : null);
  add("clock.timezone", info.timezone || null);
  add("health.temperature", info.temperature != null ? info.temperature.toLocaleString() + " \u00b0C" : null,
      info.temperature >= 75 ? "warn" : null);
  if (info.memory) {
    add("health.memory", t("health.used_of", {
      used: formatBytes(info.memory.total - info.memory.available), total: formatBytes(info.memory.total) }));
  }
  if (info.load) add("health.load", info.load.map((x) => x.toFixed(2)).join(" \u00b7 "));

  (info.storage || []).forEach((s) => {
    const lines = [t("storage.kind_" + (s.kind || "unknown")) + (s.model ? " · " + s.model : "")
      + (s.size ? " · " + formatBytes(s.size) : ""),
      t("health.free_of", { free: formatBytes(s.free), total: formatBytes(s.total) })];
    if (s.read_only) lines.push(t(s.read_only_on_purpose ? "storage.read_only_mounted" : "storage.read_only"));
    if (s.fs_errors) lines.push(t("storage.fs_errors", { n: s.fs_errors }));
    if (s.io_errors) lines.push(t("storage.io_errors", { n: s.io_errors }));
    if (s.status === "warn" && !s.io_errors) lines.push(t("storage.low_space"));
    add("storage.role_" + s.role, lines.join("\n"), s.status === "ok" ? null : "warn");
  });
  if (info.power) {
    const p = info.power;
    const words = [];
    if (p.undervoltage_now) words.push(t("health.power_low_now"));
    else if (p.undervoltage_since_boot) words.push(t("health.power_low_past"));
    if (p.throttled_now || p.throttled_since_boot) words.push(t("health.throttled"));
    add("health.power", words.length ? words.join(" \u00b7 ") : t("health.power_ok"), words.length ? "warn" : null);
  }
  if (Array.isArray(info.addresses) && info.addresses.length) {
    add("health.addresses", info.addresses.map((a) => a.address + " (" + a.interface + ")").join("\n"));
  }
  if (Array.isArray(info.usb_devices)) {
    add("health.usb", (info.usb_port_mode === "host" ? "" : t("health.usb_gadget_mode") + "\n")
      + (info.usb_devices.length ? info.usb_devices.join("\n") : t("health.usb_none")));
  }
  add("health.model", info.model);
  document.getElementById("healthInfo").replaceChildren(...rows.flatMap(([key, value, level]) => {
    const dt = document.createElement("dt");
    dt.dataset.i18n = key;
    dt.textContent = t(key);
    const dd = document.createElement("dd");
    dd.style.whiteSpace = "pre-line";
    dd.textContent = value;
    if (level) dd.dataset.level = level;
    return [dt, dd];
  }));
}

function serviceStateText(svc) {
  if (svc.state === "active") {
    if (svc.oneshot) return t("health.done");
    return svc.since != null ? t("health.active_since", { t: formatUptime(svc.since) }) : t("health.done");
  }
  if (svc.state === "failed") return t("health.failed");
  if (svc.state === "activating" || svc.state === "reloading") return t("health.activating");
  if (svc.oneshot && svc.result === "success") return t("health.done");
  return svc.enabled ? t("health.inactive") : t("health.not_used");
}

