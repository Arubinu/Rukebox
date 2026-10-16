const NAME_WORDS = {
  en: { animals: ["Fox", "Owl", "Otter", "Panda", "Lynx", "Robin", "Koala", "Heron", "Badger", "Dolphin", "Falcon", "Hedgehog"],
        colours: ["Blue", "Red", "Green", "Golden", "Silver", "Purple", "Orange", "Pink", "Amber", "Coral", "Jade", "Ivory"],
        order: "ca" },
  fr: { animals: ["Renard", "Hibou", "Loutre", "Panda", "Lynx", "Merle", "Koala", "Héron", "Blaireau", "Dauphin", "Faucon", "Hérisson"],
        colours: ["bleu", "rouge", "vert", "doré", "argenté", "violet", "orange", "rose", "ambré", "corail", "jade", "ivoire"],
        order: "ac" },
  de: { animals: ["Fuchs", "Uhu", "Otter", "Panda", "Luchs", "Rabe", "Koala", "Reiher", "Dachs", "Delfin", "Falke", "Igel"],
        colours: ["Blauer", "Roter", "Grüner", "Goldener", "Silberner", "Violetter", "Oranger", "Rosa", "Bernstein", "Korallen", "Jade", "Elfenbein"],
        order: "ca" },
  es: { animals: ["Zorro", "Búho", "Nutria", "Panda", "Lince", "Mirlo", "Koala", "Garza", "Tejón", "Delfín", "Halcón", "Erizo"],
        colours: ["azul", "rojo", "verde", "dorado", "plateado", "violeta", "naranja", "rosa", "ámbar", "coral", "jade", "marfil"],
        order: "ac" },
  it: { animals: ["Volpe", "Gufo", "Lontra", "Panda", "Lince", "Merlo", "Koala", "Airone", "Tasso", "Delfino", "Falco", "Riccio"],
        colours: ["blu", "rossa", "verde", "dorata", "argento", "viola", "arancio", "rosa", "ambra", "corallo", "giada", "avorio"],
        order: "ac" },
  nl: { animals: ["Vos", "Uil", "Otter", "Panda", "Lynx", "Merel", "Koala", "Reiger", "Das", "Dolfijn", "Valk", "Egel"],
        colours: ["Blauwe", "Rode", "Groene", "Gouden", "Zilveren", "Paarse", "Oranje", "Roze", "Amber", "Koraal", "Jade", "Ivoren"],
        order: "ca" },
};

function makeUpName(withNumber) {
  const words = NAME_WORDS[currentLang] || NAME_WORDS.en;
  const pick = (list) => list[Math.floor(Math.random() * list.length)];
  const animal = pick(words.animals);
  const colour = pick(words.colours);
  const name = words.order === "ac" ? animal + " " + colour : colour + " " + animal;
  return withNumber ? name + " " + (10 + Math.floor(Math.random() * 90)) : name;
}

async function giveGeneratedName() {
  for (let attempt = 0; attempt < 4; attempt++) {
    const r = await apiPost("/api/suggestions/name", { name: makeUpName(attempt > 0), generated: true });
    if (r.ok) return true;
    if (r.error !== "name_taken") return false;
  }
  return false;
}

async function refreshSuggestions() {
  const card = document.getElementById("suggestCard");
  const result = await apiGet("/api/suggestions");

  if (!result.ok || !result.data || !Array.isArray(result.data.items)) {
    card.hidden = true;
    return;
  }
  card.hidden = false;
  suggestState = result.data;
  paintSuggestTitle(suggestState.enabled !== false);
  if (suggestState.me && !suggestState.me.name && !nameGenerating) {
    nameGenerating = true;
    const r = await giveGeneratedName();
    nameGenerating = false;
    if (r) return refreshSuggestions();
  }
  renderSuggestMe();
  renderSuggestions();
  noteNewSuggestions();
  const names = document.getElementById("suggestNamesSection");
  names.hidden = !suggestState.owner;
  if (suggestState.owner && names.open) refreshSuggestNames();
}

// Per browser: each screen keeps what it has already shown.
const SUGGEST_SEEN_KEY = "rukebox_suggest_seen";
let suggestNewCount = 0;
let suggestNewLatest = null;
let suggestNoticeReady = false;

function suggestSeenId() {
  try {
    const value = localStorage.getItem(SUGGEST_SEEN_KEY);
    return value === null ? null : Number(value) || 0;
  } catch (e) {
    return null;
  }
}

function setSuggestSeenId(id) {
  try { localStorage.setItem(SUGGEST_SEEN_KEY, String(id)); } catch (e) { /* private window */ }
}

function suggestPageShown() {
  return document.body.dataset.tab === "home" && document.body.dataset.page === "suggest" && !document.hidden;
}

function noteNewSuggestions() {
  if (!suggestState || !suggestState.owner) {
    suggestNewCount = 0;
    paintSuggestBadges();
    return;
  }
  const items = suggestState.items || [];
  const newest = items.reduce((max, item) => Math.max(max, Number(item.id) || 0), 0);
  let seen = suggestSeenId();
  // A first visit starts from now: a badge counting the whole history says nothing.
  if (seen === null || suggestPageShown()) {
    setSuggestSeenId(newest);
    seen = newest;
  }
  const fresh = items.filter((item) => item.status === "open" && !item.mine && Number(item.id) > seen)
    .sort((a, b) => Number(b.id) - Number(a.id));
  const before = suggestNewCount;
  const latest = fresh.length ? fresh[0].id : null;
  suggestNewCount = fresh.length;
  // Never at the page's first answer: those were waiting before the page opened, the badge says it.
  if (suggestNoticeReady && fresh.length && latest !== suggestNewLatest && fresh.length > before) {
    const first = fresh[0];
    showToast(fresh.length > 1 ? t("suggest.new_many", { n: fresh.length })
                               : t("suggest.new_one", { name: first.author || "?" }),
              first.text, { action: { label: t("suggest.see"), run: () => setActiveView("home", "suggest") } });
  }
  suggestNewLatest = latest;
  suggestNoticeReady = true;
  paintSuggestBadges();
}

function paintSuggestBadges() {
  const count = suggestNewCount > 99 ? "99+" : String(suggestNewCount);
  const targets = [
    pageGrids.home && pageGrids.home.querySelector('.page-tile[data-page="suggest"] .page-tile-icon'),
    railPages.home && railPages.home.querySelector('.rail-page[data-page="suggest"]'),
    document.querySelector('.tabbar .tab-btn[data-tab="home"] .tab-icon'),
  ];
  targets.forEach((el) => {
    if (!el) return;
    if (suggestNewCount) el.dataset.badge = count;
    else delete el.dataset.badge;
  });
  const tile = pageGrids.home && pageGrids.home.querySelector('.page-tile[data-page="suggest"]');
  if (tile) {
    const title = tile.querySelector(".page-tile-title").textContent;
    if (suggestNewCount) tile.setAttribute("aria-label", title + " (" + t("suggest.new_count", { n: suggestNewCount }) + ")");
    else tile.removeAttribute("aria-label");
  }
}

document.getElementById("suggestVoiceBtn").addEventListener("click", async () => {
  const me = suggestState.me || {};
  if (await editNameVoice({ url: "/api/suggestions/name_voice", has: !!me.name_voice,
                            title: t("name_voice.title"), hint: t("name_voice.hint") })) {
    refreshSuggestions();
  }
});

/* With the suggestion box off, the page is only where one's name is chosen, and is named so. */
function paintSuggestTitle(enabled) {
  const title = document.querySelector("#suggestTitle [data-i18n]");
  const help = document.querySelector("#suggestCard > .help-text");
  const key = enabled ? "suggest.title" : "suggest.title_name";
  document.getElementById("suggestTitle").dataset.icon = enabled ? "bulb" : "user";
  help.dataset.i18n = enabled ? "suggest.help" : "suggest.help_name";
  help.textContent = t(help.dataset.i18n);
  if (title.dataset.i18n === key) return;
  title.dataset.i18n = key;
  title.textContent = t(key);
  if (document.body.dataset.page === "suggest") {
    const heading = document.getElementById("pageTitle");
    heading.dataset.i18n = key;
    heading.textContent = t(key);
  }
  refreshPageMenus();
}

function renderSuggestMe() {
  const name = suggestState.me && suggestState.me.name;
  const locked = !!(suggestState.me && suggestState.me.locked);
  const editing = !name || (suggestRenaming && !locked);
  document.getElementById("suggestMeLine").hidden = editing;
  document.getElementById("suggestMeName").textContent = name || "";
  document.getElementById("suggestRenameBtn").hidden = locked;
  document.getElementById("suggestVoiceBtn").textContent = t(suggestState.me && suggestState.me.name_voice
    ? "name_voice.button_has" : "name_voice.button");
  document.getElementById("suggestNameLocked").hidden = !(locked && name);
  document.getElementById("suggestNameForm").hidden = !editing;
  document.getElementById("suggestNameCancel").hidden = !name;

  document.getElementById("suggestForm").hidden = !name || suggestState.enabled === false;
}

function suggestKindLabel(kind) {
  return t(kind === "announcement" ? "suggest.kind_announcement_short" : "suggest.kind_music_short");
}

function suggestionRow(item) {
  const li = document.createElement("li");
  li.className = "suggest-item";
  li.dataset.status = item.status;

  const main = document.createElement("div");
  main.className = "suggest-main";
  const kind = document.createElement("span");
  kind.className = "suggest-kind";
  kind.textContent = suggestKindLabel(item.kind) +
    (item.status === "added" ? " \u00b7 " + t("suggest.status_added")
      : item.status === "declined" ? " \u00b7 " + t("suggest.status_declined") : "");
  const text = document.createElement("p");
  text.className = "suggest-text";
  text.textContent = item.text;
  const meta = document.createElement("p");
  meta.className = "suggest-meta";
  meta.textContent = t("suggest.by", { name: item.author }) + " \u00b7 " + formatDateTime(item.created_at) +
    (suggestState.owner && item.device ? " \u00b7 " + t("suggest.device", { id: item.device }) : "");
  main.append(kind, text, meta);

  if (item.in_library) {
    const found = document.createElement("p");
    found.className = "suggest-in-library";
    found.dataset.icon = "check";
    found.textContent = t("suggest.in_library", {
      title: [item.in_library.artist, item.in_library.title].filter(Boolean).join(" - "),
    });
    main.append(found);
  }

  const actions = document.createElement("div");
  actions.className = "suggest-actions";
  const open = item.status === "open";
  if (item.in_library && item.in_library.key) actions.append(libraryButton(item.in_library, true));
  for (const [value, cls, iconName, count, labelKey] of [
    [1, "vote-up", "thumb-up", item.up, "suggest.vote_up"],
    [-1, "vote-down", "thumb-down", item.down, "suggest.vote_down"],
  ]) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "btn btn-small vote-btn " + cls;
    b.dataset.icon = iconName;
    b.textContent = String(count);
    b.setAttribute("aria-pressed", item.my_vote === value ? "true" : "false");
    b.setAttribute("aria-label", t(labelKey, { n: count }));

    b.disabled = item.mine || !open;
    if (item.mine) b.title = t("suggest.own");
    b.addEventListener("click", () => voteSuggestion(item, value));
    actions.append(b);
  }
  if (suggestState.owner) {
    for (const [status, iconName, key] of open
      ? [["added", "check", "suggest.mark_added"], ["declined", "x", "suggest.mark_declined"]]
      : [["open", "restart", "suggest.reopen"]]) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "btn btn-small btn-icon";
      b.dataset.icon = iconName;
      b.setAttribute("aria-label", t(key));
      b.title = t(key);
      b.addEventListener("click", () => setSuggestionStatus(item, status));
      actions.append(b);
    }
  }
  if (item.mine || suggestState.owner) {
    const del = document.createElement("button");
    del.type = "button";
    del.className = "btn btn-small btn-icon btn-danger-outline";
    del.dataset.icon = "trash";
    del.setAttribute("aria-label", t("common.delete"));
    del.title = t("common.delete");
    del.addEventListener("click", () => deleteSuggestion(item));
    actions.append(del);
  }
  li.append(main, actions);
  return li;
}

function renderSuggestions() {
  const list = document.getElementById("suggestList");
  list.replaceChildren(...suggestState.items.map(suggestionRow));
  document.getElementById("suggestEmpty").hidden = suggestState.items.length > 0 || suggestState.enabled === false;
}

async function voteSuggestion(item, value) {
  const result = await apiPost("/api/suggestions/vote", { id: item.id, value: item.my_vote === value ? 0 : value });
  if (!result.ok) showError(result.error);
  refreshSuggestions();
}

async function setSuggestionStatus(item, status) {
  const result = await apiPost("/api/suggestions/status", { id: item.id, status });
  if (!result.ok) showError(result.error);
  refreshSuggestions();
}

async function deleteSuggestion(item) {
  if (!(await showConfirm(t("suggest.confirm_delete", { text: item.text })))) return;
  const result = await apiPost("/api/suggestions/delete", { id: item.id });
  if (!result.ok) showError(result.error);
  refreshSuggestions();
}

async function refreshSuggestNames() {
  const result = await apiGet("/api/suggestions/names");
  if (!result.ok || !result.data || !Array.isArray(result.data.people)) return;
  const list = document.getElementById("suggestPeople");
  list.replaceChildren(...result.data.people.map((p) => {
    const li = document.createElement("li");
    const name = document.createElement("span");
    name.className = "person-name";
    name.textContent = p.name || t("suggest.no_name");
    const tag = document.createElement("span");
    tag.className = "device-tag";
    tag.textContent = " " + [p.device_id, p.mac, p.ip].filter(Boolean).join(" \u00b7 ");
    const meta = document.createElement("p");
    meta.className = "suggest-meta";
    meta.textContent = t("suggest.person_meta", { seen: formatDateTime(p.last_seen), n: p.suggestions });
    const names = document.createElement("p");
    names.className = "person-names";
    names.textContent = t("suggest.person_names") + " " +
      p.names.map((n) => n.name + " (" + formatDateTime(n.at) + ")").join(" \u2192 ");
    li.append(name, tag, meta, names);

    const free = p.reserved.filter((r) => !r.in_use);
    if (free.length) {
      const row = document.createElement("div");
      row.className = "person-reserved";
      const label = document.createElement("span");
      label.className = "suggest-meta";
      label.textContent = t("suggest.reserved_label");
      row.append(label);
      for (const r of free) {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "btn btn-small";
        b.textContent = t("suggest.release_name", { name: r.display });
        b.addEventListener("click", async () => {
          const res = await apiPost("/api/suggestions/release_name", { key: r.key });
          if (!res.ok) showError(res.error);
          refreshSuggestNames();
        });
        row.append(b);
      }
      li.append(row);
    }
    return li;
  }));
}

document.getElementById("suggestNamesSection").addEventListener("toggle", (event) => {
  if (event.target.open) refreshSuggestNames();
});

function minutesLeft(seconds) {
  return Math.max(1, Math.ceil((Number(seconds) || 0) / 60));
}

document.getElementById("suggestRenameBtn").addEventListener("click", () => {
  const me = suggestState && suggestState.me ? suggestState.me : {};
  if (me.locked) {
    showToast(t("common.failed"), t("suggest.name_locked"), { error: true });
    return;
  }
  const wait = me.rename_wait || 0;
  if (wait > 0) {
    showToast(t("common.failed"), t("suggest.rename_wait", { min: minutesLeft(wait) }), { error: true });
    return;
  }
  suggestRenaming = true;
  renderSuggestMe();
  const input = document.getElementById("suggestNameInput");
  input.value = (suggestState.me && suggestState.me.name) || "";
  input.focus();
});
document.getElementById("suggestNameCancel").addEventListener("click", () => {
  suggestRenaming = false;
  renderSuggestMe();
});

/* The device that TYPES the code takes the identity of the one that SHOWS it. */
function linkDialogBody() {
  const box = document.createElement("div");
  box.className = "link-dialog";
  const intro = document.createElement("p");
  intro.className = "hint";
  intro.textContent = t("link.intro");
  box.append(intro);
  const linked = (suggestState && suggestState.me && suggestState.me.linked) || 0;
  if (linked) {
    const count = document.createElement("p");
    count.className = "hint";
    count.textContent = t("link.count", { n: linked });
    box.append(count);
  }

  const show = document.createElement("div");
  show.className = "link-part";
  const showBtn = document.createElement("button");
  showBtn.type = "button";
  showBtn.className = "btn";
  showBtn.dataset.icon = "key";
  showBtn.textContent = t("link.show_btn");
  const code = document.createElement("output");
  code.className = "link-code";
  code.hidden = true;
  const showHint = document.createElement("p");
  showHint.className = "field-desc";
  showHint.textContent = t("link.show_hint");
  showHint.hidden = true;
  showBtn.addEventListener("click", async () => {
    showBtn.disabled = true;
    const r = await apiPost("/api/devices/link_code");
    showBtn.disabled = false;
    if (!r.ok) {
      showError(r.error);
      return;
    }
    code.textContent = r.data.code.slice(0, 3) + "\u00a0" + r.data.code.slice(3);
    code.hidden = false;
    showHint.hidden = false;
  });
  show.append(showBtn, code, showHint);

  const join = document.createElement("form");
  join.className = "link-part";
  const label = document.createElement("label");
  label.className = "field-label";
  label.htmlFor = "linkCodeInput";
  label.textContent = t("link.join_label");
  const field = document.createElement("div");
  field.className = "input-with-action";
  const input = document.createElement("input");
  input.type = "text";
  input.id = "linkCodeInput";
  input.className = "text-input link-code-input";
  input.inputMode = "numeric";
  input.autocomplete = "one-time-code";
  input.maxLength = 7;
  input.placeholder = "000\u00a0000";
  const joinBtn = document.createElement("button");
  joinBtn.type = "submit";
  joinBtn.className = "btn btn-primary";
  joinBtn.textContent = t("link.join_btn");
  field.append(input, joinBtn);
  const joinHint = document.createElement("p");
  joinHint.className = "field-desc";
  joinHint.textContent = t("link.join_hint");
  join.append(label, field, joinHint);
  join.addEventListener("submit", async (event) => {
    event.preventDefault();
    const digits = input.value.replace(/\D/g, "");
    if (digits.length !== 6) {
      showError("link_code_bad");
      return;
    }
    joinBtn.disabled = true;
    const r = await apiPost("/api/devices/link_join", { code: digits });
    joinBtn.disabled = false;
    if (!r.ok) {
      if (r.error === "too_many_attempts") {
        showToast(t("common.failed"), t("login.locked", { s: r.retry_after || 0 }), { error: true });
      } else {
        showError(r.error);
      }
      return;
    }
    closeModal(true);
    showToast(t("link.joined", { name: r.data.name || "" }));
    refreshSuggestions();
  });

  box.append(show, join);
  if (linked) {
    const leave = document.createElement("button");
    leave.type = "button";
    leave.className = "btn btn-danger-outline link-leave";
    leave.dataset.icon = "unlink";
    leave.textContent = t("link.leave_btn");
    leave.addEventListener("click", async () => {
      closeModal(false);
      if (!await showConfirm(t("link.leave_confirm"), t("link.leave_btn"))) return;
      const r = await apiPost("/api/devices/link_leave");
      if (!r.ok) {
        showError(r.error);
        return;
      }
      showToast(t("link.left"));
      refreshSuggestions();
    });
    box.append(leave);
  }
  return box;
}
document.getElementById("suggestLinkBtn").addEventListener("click", () => {
  openModal({ title: t("link.title"), bodyNode: linkDialogBody(), actions: false });
});
document.getElementById("suggestNameForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = document.getElementById("suggestNameInput");
  const result = await apiPost("/api/suggestions/name", { name: input.value });
  if (!result.ok) {
    if (result.error === "rename_too_soon") {
      showToast(t("common.failed"), t("suggest.rename_wait", { min: minutesLeft(result.detail) }), { error: true });
    } else {
      showError(result.error);
    }
    return;
  }
  suggestRenaming = false;
  showToast(t("suggest.name_saved", { name: result.data.name }));
  refreshSuggestions();
});

function suggestField() {
  return document.getElementById(document.getElementById("suggestKind").value === "announcement"
    ? "suggestTextLong" : "suggestText");
}

function updateSuggestField() {
  const long = document.getElementById("suggestKind").value === "announcement";
  document.getElementById("suggestText").hidden = long;
  document.getElementById("suggestTextLong").hidden = !long;
  const count = document.getElementById("suggestCount");
  count.hidden = !long;
  const area = document.getElementById("suggestTextLong");
  count.textContent = area.value.length + " / " + area.maxLength;
}
document.getElementById("suggestKind").addEventListener("change", () => {
  updateSuggestField();
  suggestField().focus();
});
document.getElementById("suggestTextLong").addEventListener("input", updateSuggestField);
updateSuggestField();

document.getElementById("suggestForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = suggestField();
  if (!input.value.trim()) {
    input.focus();
    return;
  }
  const kind = document.getElementById("suggestKind").value;
  let result = await apiPost("/api/suggestions", { kind, text: input.value });

  if (!result.ok && result.error === "suggestion_in_library") {
    const ok = await showConfirm(t("suggest.in_library_confirm", { title: result.detail || "" }));
    if (!ok) return;
    result = await apiPost("/api/suggestions", { kind, text: input.value, force: true });
  }
  if (!result.ok) {
    showError(result.error);
    return;
  }
  input.value = "";
  updateSuggestField();
  showToast(t("suggest.sent"));
  refreshSuggestions();
});

refreshSuggestions();
refreshEvery(refreshSuggestions, 30000);

let editingAnnouncementId = null;
const annTrigger = document.getElementById("annTrigger");
const annRepeat = document.getElementById("annRepeat");

function updateAnnTimeVisibility() {
  const trigger = annTrigger.value;
  const delayed = trigger === "after_music" || trigger === "after_boot";
  document.getElementById("annTimeRow").hidden = trigger !== "time";
  document.getElementById("annDelayRow").hidden = !delayed;
  document.getElementById("annRepeatRow").hidden = !delayed;
  document.getElementById("annRepeatCountRow").hidden = !delayed || annRepeat.value !== "count";
  document.getElementById("annAutoChanceRow").hidden = trigger === "manual";
  document.getElementById("annAfterRow").hidden = trigger === "manual";
}
annTrigger.addEventListener("change", updateAnnTimeVisibility);
annRepeat.addEventListener("change", updateAnnTimeVisibility);
updateAnnTimeVisibility();

