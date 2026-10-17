function libraryLink(text, filter, className) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "btn-link library-link" + (className ? " " + className : "");
  b.textContent = text;
  b.title = t("library.show_in");
  b.addEventListener("click", () => showInLibrary(filter));
  return b;
}

refreshEvery(() => {
  if (!libraryAsked()) refreshLibrary(false);
}, 30000, ["home/library"]);

/* ---------- Music lists ---------- */

const listsCard = document.getElementById("listsCard");
const listsList = document.getElementById("listsList");
const listsNowLine = document.getElementById("listsNow");
const listsOverview = document.getElementById("listsOverview");
const listsDetail = document.getElementById("listsDetail");
const listsDetailName = document.getElementById("listsDetailName");
const listsDetailMeta = document.getElementById("listsDetailMeta");
const listsDetailActions = document.getElementById("listsDetailActions");
const listsDetailBody = document.getElementById("listsDetailBody");
const listsNameInput = document.getElementById("listName");
const listsKindSelect = document.getElementById("listKind");
const listsGenresBox = document.getElementById("listsGenres");
const listsGenresRow = document.getElementById("listGenresRow");
const listsNewBox = document.getElementById("listsNew");
const libraryGenrePlay = document.getElementById("libraryGenrePlay");

let listsData = { lists: [], active: null, may_play: false };
let listsGenres = [];
let listsSeq = 0;
let detailListId = null;
let detailSignature = "";
let editingListId = null;
let writingList = false;

function tracksLabel(n) {
  return t(n === 1 ? "lists.track_one" : "lists.track_other", { n: n });
}

function listKindLabel(item) {
  if (item.kind !== "genre") return t("lists.kind_manual_short");
  return (item.genres || []).join(", ") || t("lists.kind_genre_short");
}

function listMeta(item) {
  return [listKindLabel(item), tracksLabel(item.count),
          item.owner_name ? t("lists.of", { name: item.owner_name }) : "",
          item.id === listsData.active ? t("lists.playing") : ""]
    .filter(Boolean).join(" \u00b7 ");
}

function listById(id) {
  return (listsData.lists || []).find((list) => list.id === id) || null;
}

function listActionButton(icon, key, onClick, variant) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "btn" + (variant ? " " + variant : "");
  b.dataset.icon = icon;
  b.textContent = t(key);
  b.addEventListener("click", onClick);
  return b;
}

function foldText(text) {
  return String(text || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();
}

const GENRE_OPTIONS_MAX = 8;

function pickedGenres(host) {
  // The pills above ARE the selection: the search can be changed or emptied without losing it.
  return Array.from(host.querySelectorAll(".genre-pill")).map((pill) => pill.dataset.genre);
}

function setPickedGenres(host, names) {
  const pills = host.querySelector(".genre-pills");
  pills.replaceChildren(...(names || []).map((name) => {
    const pill = document.createElement("span");
    pill.className = "genre-pill";
    pill.dataset.genre = name;
    const text = document.createElement("span");
    text.textContent = name;
    const off = document.createElement("button");
    off.type = "button";
    off.className = "genre-pill-x";
    off.dataset.icon = "x";
    off.title = t("lists.genre_remove", { name: name });
    off.setAttribute("aria-label", off.title);
    off.addEventListener("click", () => {
      setPickedGenres(host, pickedGenres(host).filter((kept) => kept !== name));
    });
    pill.append(text, off);
    return pill;
  }));
  paintGenreOptions(host);
}

function paintGenreOptions(host) {
  const options = host.querySelector(".genre-options");
  const query = foldText(host.querySelector(".genre-search").value);
  const picked = pickedGenres(host).map((name) => foldText(name));
  const matching = listsGenres.filter((genre) => !query || foldText(genre.name).includes(query));
  if (!matching.length) {
    const none = document.createElement("p");
    none.className = "hint genre-none";
    none.textContent = t(listsGenres.length ? "lists.genre_none" : "lists.genre_empty");
    options.replaceChildren(none);
    return;
  }
  // A short list: the search narrows it, and the page keeps the only scroll.
  const shown = matching.slice(0, GENRE_OPTIONS_MAX);
  const rows = shown.map((genre) => {
    const label = document.createElement("label");
    label.className = "genre-option";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.value = genre.name;
    box.checked = picked.includes(foldText(genre.name));
    const name = document.createElement("span");
    name.className = "genre-option-name";
    name.textContent = genre.name;
    const count = document.createElement("span");
    count.className = "genre-count";
    count.textContent = String(genre.count);
    box.addEventListener("change", () => {
      const kept = pickedGenres(host).filter((one) => foldText(one) !== foldText(genre.name));
      if (box.checked) kept.push(genre.name);
      setPickedGenres(host, kept);
    });
    label.append(box, name, count);
    return label;
  });
  if (matching.length > shown.length) {
    const more = document.createElement("p");
    more.className = "hint genre-none";
    more.textContent = t("lists.more_genres", { n: matching.length - shown.length });
    rows.push(more);
  }
  options.replaceChildren(...rows);
}

function genrePicker(host, selected) {
  if (!host.dataset.built) {
    host.dataset.built = "1";
    host.classList.add("genre-picker");
    const search = document.createElement("input");
    search.type = "search";
    search.className = "text-input genre-search";
    search.autocomplete = "off";
    search.dataset.i18nPlaceholder = "lists.genre_search";
    search.dataset.i18nAriaLabel = "lists.genre_search";
    search.placeholder = t("lists.genre_search");
    search.setAttribute("aria-label", t("lists.genre_search"));
    const pills = document.createElement("div");
    pills.className = "genre-pills";
    const options = document.createElement("div");
    options.className = "genre-options";
    search.addEventListener("input", () => paintGenreOptions(host));
    host.replaceChildren(search, pills, options);
  }
  setPickedGenres(host, selected || []);
}

function clearGenrePicker(host) {
  host.querySelector(".genre-search").value = "";
  setPickedGenres(host, []);
}

function paintGenrePlay() {
  // Looked up here, not in a const: this runs before the Lists card below is set up.
  const actions = document.getElementById("libraryGenreActions");
  if (actions) {
    actions.hidden = !libraryGenre.value || document.body.dataset.access === "guest"
      || !listsData.may_play;
  }
}

async function playGenre() {
  if (!libraryGenre.value) return;
  libraryGenrePlay.disabled = true;
  const r = await apiPost("/api/lists/from_genres",
                          { genres: [libraryGenre.value], start: true });
  libraryGenrePlay.disabled = false;
  if (!r.ok) {
    showError(r.error);
    return;
  }
  showToast(t("lists.now_playing", { name: r.data.name }), tracksLabel(r.data.tracks || 0));
  refreshStatus();
  refreshUpnext();
  refreshLists();
}

libraryGenrePlay.addEventListener("click", playGenre);

function openListsCard() {
  goToCard("home", "listsTitle");
  listsNewBox.open = true;
  listsNameInput.focus();
}

async function addToManualList(item) {
  // Only the lists this person may change: their own, or every one for the radio's keepers.
  const manual = (listsData.lists || []).filter((l) => l.kind === "manual" && l.editable);
  if (!manual.length) {
    showToast(t("lists.none_yet"), t("lists.none_yet_hint"),
              { action: { label: t("lists.open_card"), icon: "list", run: openListsCard } });
    return;
  }
  const name = item.title || item.name || "";
  const chosen = await showChoice("", manual.map((l) => ({ label: l.name, value: l.id })),
                                  t("lists.add_to", { title: name }));
  if (!chosen) return;
  const r = await apiPost("/api/lists/" + encodeURIComponent(chosen) + "/tracks", { key: item.key });
  if (!r.ok) {
    showError(r.error);
    return;
  }
  showToast(t("lists.added", { name: r.data.name }), tracksLabel(r.data.count));
  refreshLists();
}

async function addOrLike(item, button) {
  const name = item.title || item.name || "";
  const liked = !!(item.key && likedKeys.has(item.key));
  const chosen = await showChoice("", [
    { label: t(liked ? "likes.unlike" : "likes.like"), value: "like" },
    { label: t("lists.add_to", { title: name }), value: "list" },
  ], name || t("library.add_or_like"));
  if (!chosen) return;
  if (chosen === "list") {
    addToManualList(item);
    return;
  }
  const r = await apiPost("/api/likes/toggle", {
    key: item.key, title: item.title || item.name || "", artist: item.artist || "",
  });
  if (!r.ok) {
    showToolError(t("likes.failed"), r);
    return;
  }
  showToast(t(r.data.liked ? "likes.added" : "likes.removed", { title: name }));
  await refreshLikes();
  // No refetch for this: the button carries the state.
  if (button) button.classList.toggle("is-liked", !!r.data.liked);
}

function libraryListButton(item) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "btn btn-icon btn-small library-add";
  b.dataset.icon = "plus-heart";
  if (item.key && likedKeys.has(item.key)) b.classList.add("is-liked");
  b.title = t("library.add_or_like");
  b.setAttribute("aria-label", b.title);
  b.addEventListener("click", () => addOrLike(item, b));
  return b;
}

async function playList(item) {
  const r = await apiPost("/api/lists/active", { id: item.id, start: true });
  if (!r.ok) {
    showError(r.error);
    return;
  }
  showToast(t("lists.now_playing", { name: item.name }), tracksLabel(r.data.tracks || 0));
  refreshStatus();
  refreshUpnext();
  refreshLists();
}

async function deleteList(item) {
  if (!(await showConfirm(t("lists.confirm_delete", { name: item.name })))) return;
  const r = await apiDelete("/api/lists/" + encodeURIComponent(item.id));
  if (!r.ok) {
    showError(r.error);
    return;
  }
  if (detailListId === item.id) {
    detailListId = null;
    editingListId = null;
  }
  showToast(t("lists.deleted", { name: item.name }));
  refreshLists();
}

async function saveList(item) {
  if (writingList) return;
  const name = (listsDetailBody.querySelector(".list-name").value || "").trim();
  const body = { name: name };
  if (item.kind === "genre") {
    body.genres = pickedGenres(listsDetailBody.querySelector(".genre-picker"));
  }
  writingList = true;
  const r = await apiPost("/api/lists/" + encodeURIComponent(item.id), body);
  writingList = false;
  if (!r.ok) {
    showError(r.error);
    return;
  }
  editingListId = null;
  showToast(t("lists.saved", { name: r.data.name }), tracksLabel(r.data.count));
  refreshLists();
}

const LIST_TRACKS_MAX = 200;

function listNote(text) {
  const li = document.createElement("li");
  li.className = "list-note";
  li.textContent = text;
  return li;
}

async function loadListTracks(item, holder, editable) {
  const r = await apiGet("/api/lists/" + encodeURIComponent(item.id) + "/tracks");
  if (!r.ok || !r.data) {
    holder.replaceChildren(listNote(errorLabel(r.error)));
    return;
  }
  const items = r.data.items || [];
  if (!items.length) {
    holder.replaceChildren(listNote(t(item.kind === "genre"
      ? "lists.empty_genre" : "lists.empty_manual")));
    return;
  }
  const rows = items.slice(0, LIST_TRACKS_MAX).map((track) => {
    const li = document.createElement("li");
    if (track.missing) li.classList.add("is-off");
    li.append(trackMain(track));
    const mark = excludedMark(track);
    if (mark) li.append(mark);
    if (track.key && !track.missing) li.append(libraryButton(track, true));
    if (!editable) return li;

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "btn btn-icon btn-small";
    remove.dataset.icon = "x";
    const title = track.title || track.name || "";
    remove.title = t("lists.remove_track", { title: title });
    remove.setAttribute("aria-label", remove.title);
    remove.addEventListener("click", async () => {
      remove.disabled = true;
      const res = await apiDelete("/api/lists/" + encodeURIComponent(item.id) + "/tracks",
                                  { path: track.path, key: track.key });
      if (!res.ok) {
        remove.disabled = false;
        showError(res.error);
        return;
      }
      li.remove();
      refreshLists();
    });
    li.append(remove);
    return li;
  });
  if (items.length > rows.length) {
    rows.push(listNote(t("lists.more_tracks", { n: items.length - rows.length })));
  }
  if (r.data.missing) {
    rows.push(listNote(t("lists.missing", { n: r.data.missing })));
  }
  holder.replaceChildren(...rows);
}

function listRow(item) {
  const li = document.createElement("li");
  li.className = "ann-item";
  li.dataset.id = item.id;
  if (item.id === listsData.active) li.classList.add("is-playing");

  const head = document.createElement("button");
  head.type = "button";
  head.className = "ann-head";

  const text = document.createElement("span");
  text.className = "ann-text";
  const name = document.createElement("span");
  name.className = "ann-name";
  name.textContent = item.name;
  const meta = document.createElement("span");
  meta.className = "ann-meta";
  meta.textContent = listMeta(item);
  text.append(name, meta);

  const chevron = document.createElement("span");
  chevron.className = "ann-chevron";
  chevron.dataset.icon = "chevron";
  chevron.setAttribute("aria-hidden", "true");
  head.append(text, chevron);
  head.addEventListener("click", () => openList(item.id));

  li.append(head);
  return li;
}

function renderOverview() {
  listsList.replaceChildren(...(listsData.lists || []).map(listRow));
}

function renderDetailHeader(item) {
  listsDetailName.textContent = item.name;
  listsDetailMeta.textContent = listMeta(item);
}

function renderDetail(item) {
  // Rebuilt only on a change: every rebuild refetches the tracks, and the 20s refresh must not.
  const signature = [item.id, item.name, item.count, item.kind,
                     (item.genres || []).join(","), item.id === listsData.active,
                     editingListId === item.id, item.editable, listsData.may_play].join("|");
  if (signature === detailSignature) return;
  detailSignature = signature;

  const editing = editingListId === item.id && item.editable;
  const actions = editing ? [
    listActionButton("check", "common.save", () => saveList(item)),
    listActionButton("x", "common.cancel", () => {
      editingListId = null;
      renderDetail(item);
    }),
  ] : [
    listsData.may_play ? listActionButton("play", "lists.play", () => playList(item)) : null,
    item.editable ? listActionButton("pencil", "common.edit", () => {
      editingListId = item.id;
      renderDetail(item);
    }) : null,
    listActionButton("download", "lists.export", () => exportList(item)),
    item.editable ? listActionButton("trash", "common.delete", () => deleteList(item), "btn-danger-outline")
      : null,
  ].filter(Boolean);
  ["is-one", "is-two", "is-three", "is-four"].forEach((name, index) => {
    listsDetailActions.classList.toggle(name, actions.length === index + 1);
  });
  listsDetailActions.replaceChildren(...actions);

  const parts = [];
  if (editing) {
    const fields = document.createElement("div");
    fields.className = "list-fields";
    const nameRow = document.createElement("div");
    nameRow.className = "field-row";
    const nameLabel = document.createElement("label");
    nameLabel.textContent = t("lists.name");
    const nameInput = document.createElement("input");
    nameInput.type = "text";
    nameInput.className = "text-input list-name";
    nameInput.maxLength = 40;
    nameInput.value = item.name;
    nameRow.append(nameLabel, nameInput);
    fields.append(nameRow);
    if (item.kind === "genre") {
      const box = document.createElement("div");
      box.className = "genre-checks";
      genrePicker(box, item.genres);
      fields.append(box);
    }
    parts.push(fields);
  }

  // Only a manual list's tracks can be given a cross.
  const tracks = document.createElement("ol");
  tracks.className = "device-list recent-list list-tracks";
  parts.push(tracks);
  listsDetailBody.replaceChildren(...parts);
  loadListTracks(item, tracks, item.kind === "manual" && item.editable);
}

function renderLists() {
  const item = detailListId ? listById(detailListId) : null;
  if (!item) {
    detailListId = null;
    editingListId = null;
    detailSignature = "";
  }
  listsOverview.hidden = Boolean(item);
  listsDetail.hidden = !item;
  if (!item) {
    renderOverview();
    return;
  }
  renderDetailHeader(item);
  renderDetail(item);
}

function openList(id) {
  detailListId = id;
  editingListId = null;
  detailSignature = "";
  renderLists();
}

function closeList() {
  detailListId = null;
  editingListId = null;
  detailSignature = "";
  renderLists();
}

document.getElementById("listsBack").addEventListener("click", closeList);

/* M3U: a list handed to another player, or one of its lists taken in. */
function exportList(item) {
  const link = document.createElement("a");
  link.href = "/api/lists/" + encodeURIComponent(item.id) + "/m3u";
  link.download = "";
  document.body.append(link);
  link.click();
  link.remove();
}

document.getElementById("listsImport").addEventListener("click", () => {
  document.getElementById("listsImportInput").click();
});

document.getElementById("listsImportInput").addEventListener("change", async (event) => {
  const file = (event.target.files || [])[0];
  event.target.value = "";
  if (!file) return;
  const body = new FormData();
  body.append("file", file, file.name);
  const result = await apiFetch("/api/lists/import", { method: "POST", body });
  if (!result.ok) {
    showError(result.error);
    return;
  }
  const d = result.data;
  if (d.missing_count) {
    showToast(t("lists.imported", { name: d.list.name, n: d.found }),
              t("lists.imported_missing", { n: d.missing_count }) + " " + d.missing.slice(0, 5).join(", "));
  } else {
    showToast(t("lists.imported", { name: d.list.name, n: d.found }));
  }
  await refreshLists();
  openList(d.list.id);
});

async function refreshLists() {
  if (document.body.dataset.access === "guest" || editingListId) return;
  const seq = ++listsSeq;
  const results = await Promise.all([apiGet("/api/lists"), apiGet("/api/library/facets")]);
  if (seq !== listsSeq) return;
  const res = results[0];
  const facets = results[1];
  if (!res.ok || !res.data) {
    listsCard.hidden = true;
    return;
  }
  listsCard.hidden = false;
  listsData = res.data;
  document.getElementById("listsPlayAll").hidden = !listsData.may_play;
  paintGenrePlay();
  if (facets.ok && facets.data) listsGenres = facets.data.genres || [];

  const active = listById(listsData.active);
  listsNowLine.textContent = active
    ? t("lists.now_line", { name: active.name, tracks: tracksLabel(active.count) })
    : t("lists.now_all");
  renderLists();
  if (!detailListId) paintCreateGenres();
}

function paintCreateGenres() {
  genrePicker(listsGenresBox, pickedGenres(listsGenresBox));
}

async function playEverything() {
  const r = await apiPost("/api/lists/active", { id: null, start: true });
  if (!r.ok) {
    showError(r.error);
    return;
  }
  showToast(t("lists.now_all"), t("lists.now_all_hint"));
  refreshStatus();
  refreshUpnext();
  refreshLists();
}

document.getElementById("listsPlayAll").addEventListener("click", playEverything);

listsKindSelect.addEventListener("change", () => {
  const byGenre = listsKindSelect.value === "genre";
  listsGenresRow.hidden = !byGenre;
  listsGenresBox.hidden = !byGenre;
});

document.getElementById("listsCreate").addEventListener("click", async () => {
  const kind = listsKindSelect.value;
  const body = {
    name: listsNameInput.value.trim(),
    kind: kind,
    genres: pickedGenres(listsGenresBox),
  };
  const r = await apiPost("/api/lists", body);
  if (!r.ok) {
    showError(r.error);
    return;
  }
  listsNameInput.value = "";
  clearGenrePicker(listsGenresBox);
  listsNewBox.open = false;
  showToast(t("lists.created", { name: r.data.name }), tracksLabel(r.data.count));
  await refreshLists();
  openList(r.data.id);
});

refreshLists();
refreshEvery(refreshLists, 20000, ["home/lists", "home/library", "settings/settings"]);

const hapticsToggle = document.getElementById("hapticsToggle");
hapticsToggle.checked = hapticsOn();
if (typeof navigator.vibrate !== "function") {
  hapticsToggle.disabled = true;
  hapticsToggle.checked = false;
  const desc = document.getElementById("hapticsDesc");
  desc.dataset.i18n = "haptics.unsupported";
  desc.textContent = t("haptics.unsupported");
}
hapticsToggle.addEventListener("change", () => {
  try {
    localStorage.setItem(HAPTICS_KEY, hapticsToggle.checked ? "on" : "off");
  } catch (e) {  }
  if (hapticsToggle.checked) haptic(HAPTIC_TAP_MS);
});

let suggestState = null;
let suggestRenaming = false;
let nameGenerating = false;

