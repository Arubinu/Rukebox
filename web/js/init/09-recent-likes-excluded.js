async function refreshRecent() {
  const card = document.getElementById("recentCard");
  const result = await apiGet("/api/recent");
  if (!result.ok || !result.data || !Array.isArray(result.data.items)) return;
  card.hidden = !result.data.enabled;
  if (!result.data.enabled) return;
  const items = result.data.items;
  document.getElementById("recentList").replaceChildren(...items.map((item) => {
    const li = document.createElement("li");
    const current = item.key && item.key === recentPlayingKey;
    if (current) li.classList.add("is-current");
    const main = document.createElement("span");
    main.className = "recent-main";
    const title = document.createElement("span");
    title.className = "recent-title";
    title.textContent = item.title || item.name;
    main.append(title);
    if (item.artist) {
      const artist = document.createElement("span");
      artist.className = "recent-artist";
      artist.textContent = item.artist;
      main.append(artist);
    }
    const when = document.createElement("span");
    when.className = "recent-when";
    when.textContent = current ? t("recent.now") : formatTimeOnly(item.at);
    li.append(main, when);
    const mark = excludedMark(item);
    if (mark) li.append(mark);
    if (!current && item.key) li.append(libraryButton(item, true));
    return li;
  }));
  document.getElementById("recentEmpty").hidden = items.length > 0;
  clearTimeout(recentRetry);
  if (result.data.pending) recentRetry = setTimeout(refreshRecent, 4000);
}

refreshRecent();
refreshEvery(refreshRecent, 60000, ["home/recent"]);

/* Liked tracks: the heart on the cover and its list. */
let likedKeys = new Set();
let likedTracks = [];

function likesAvailable() {
  return document.body.dataset.access !== "guest";
}

function currentTrackKey() {
  const d = playerStatus || {};
  if (!d.track_key || ["idle", "stopped"].includes(d.mode)) return null;
  return d.track_key;
}

function paintLikeButton() {
  const btn = document.getElementById("btnLike");
  const key = currentTrackKey();
  btn.hidden = !key || !likesAvailable();
  const on = !!key && likedKeys.has(key);
  btn.dataset.icon = on ? "heart-filled" : "heart";
  btn.classList.toggle("is-on", on);
  btn.setAttribute("aria-pressed", on ? "true" : "false");
  btn.dataset.i18n = on ? "likes.unlike" : "likes.like";
  btn.title = t(btn.dataset.i18n);
  btn.setAttribute("aria-label", btn.title);
}

/* The day a like, an exclusion or anything else dated happened. */
function shortDate(seconds) {
  if (!seconds) return "";
  try {
    return new Date(seconds * 1000).toLocaleDateString(currentLang,
      { year: "numeric", month: "short", day: "numeric" });
  } catch (e) {
    return "";
  }
}

function unlikeButton(item) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "btn btn-icon btn-small recent-play np-unlike";
  b.dataset.icon = "heart-filled";
  b.title = t("likes.unlike_title", { title: item.title || "" });
  b.setAttribute("aria-label", b.title);
  b.addEventListener("click", async () => {
    b.disabled = true;
    const r = await apiPost("/api/likes/toggle", { key: item.key, title: item.title });
    b.disabled = false;
    if (!r.ok) {
      showToolError(t("likes.failed"), r);
      return;
    }
    showToast(t("likes.removed", { title: item.title || "" }));
    refreshLikes();
  });
  return b;
}

function renderLikes() {
  const list = document.getElementById("likesList");
  document.getElementById("likesEmpty").hidden = likedTracks.length > 0;
  list.replaceChildren(...likedTracks.map((item) => {
    const li = document.createElement("li");
    li.append(trackMain(item));
    const when = document.createElement("span");
    when.className = "recent-when";
    when.textContent = shortDate(item.liked_at);
    li.append(when);
    if (item.key) li.append(libraryButton(item, true), unlikeButton(item));
    return li;
  }));
}

async function refreshLikes() {
  const card = document.getElementById("likesCard");
  if (!likesAvailable()) {
    card.hidden = true;
    paintLikeButton();
    return;
  }
  const result = await apiGet("/api/likes");
  if (!result.ok || !result.data) {
    card.hidden = true;
    return;
  }
  likedTracks = result.data.tracks || [];
  likedKeys = new Set(result.data.keys || []);
  card.hidden = false;
  renderLikes();
  paintLikeButton();
}

document.getElementById("btnLike").addEventListener("click", async () => {
  const key = currentTrackKey();
  if (!key) return;
  const btn = document.getElementById("btnLike");
  const title = (playerStatus || {}).track_title || "";
  btn.disabled = true;
  const result = await apiPost("/api/likes/toggle", {
    key, title, artist: (playerStatus || {}).track_artist || "",
  });
  btn.disabled = false;
  if (!result.ok) {
    showToolError(t("likes.failed"), result);
    return;
  }
  const liked = !!(result.data && result.data.liked);
  if (liked) likedKeys.add(key); else likedKeys.delete(key);
  paintLikeButton();
  showToast(t(liked ? "likes.added" : "likes.removed", { title }));
  refreshLikes();
});

refreshLikes();
refreshEvery(refreshLikes, 120000);

/* Excluded tracks: what the radio never picks on its own. The page is the only
   place that adds one - the library keeps the buttons it has. */
const EXCLUDED_PAGE = 30;
let excludedItems = [];
let excludedCount = 0;
let excludedOffset = 0;
let excludedMatches = 0;
let excludedSeq = 0;
let excludedTimer = null;
let excludedFacetsDone = false;

function excludedAvailable() {
  return document.body.dataset.access !== "guest";
}

function excludedBadge() {
  const badge = document.createElement("span");
  badge.className = "badge excluded-badge";
  badge.textContent = t("excluded.badge");
  return badge;
}

/* The mark on a track the radio will not pick: on a library row, a list's row,
   Up next or Recently played - so "it never plays" has an answer on the spot. */
function excludedMark(item) {
  return item && item.excluded ? excludedBadge() : null;
}

function excludedOrigin(origin) {
  const known = ["duplicate", "filter", "manual"].includes(origin) ? origin : "manual";
  return t("excluded.origin_" + known);
}

function excludedRestoreButton(item) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "btn btn-icon btn-small";
  b.dataset.icon = "refresh";
  const name = item.title || item.name || "";
  b.title = t("excluded.restore_aria", { title: name });
  b.setAttribute("aria-label", b.title);
  b.addEventListener("click", async () => {
    b.disabled = true;
    const r = await apiPost("/api/excluded/restore", { keys: [item.key] });
    b.disabled = false;
    if (!r.ok) {
      showToolError(t("excluded.failed"), r);
      return;
    }
    showToast(t("excluded.restored", { title: name }));
    refreshExcluded();
    refreshExcludedResults();
  });
  return b;
}

function excludedRow(item) {
  const li = document.createElement("li");
  if (item.missing) li.classList.add("is-off");
  li.append(trackMain(item));
  const when = document.createElement("span");
  when.className = "recent-when";
  when.textContent = [shortDate(item.excluded_at), excludedOrigin(item.origin)]
    .filter(Boolean).join(" \u00b7 ");
  li.append(when);
  // No key: the library lost the file, nothing to queue or give back.
  if (item.key && !item.missing) li.append(libraryButton(item, true), excludedRestoreButton(item));
  return li;
}

function renderExcluded() {
  const summary = document.getElementById("excludedSummary");
  document.getElementById("excludedEmpty").hidden = excludedCount > 0;
  document.getElementById("excludedRestoreAll").hidden = excludedCount === 0;
  summary.hidden = excludedCount === 0;
  if (excludedCount) {
    summary.dataset.i18n = "excluded.summary";
    summary.dataset.i18nVarN = String(excludedCount);
    summary.textContent = t("excluded.summary", { n: excludedCount });
  } else {
    delete summary.dataset.i18n;
    delete summary.dataset.i18nVarN;
    summary.textContent = "";
  }
  document.getElementById("excludedList").replaceChildren(...excludedItems.map(excludedRow));
  document.getElementById("excludedListMore").hidden = excludedItems.length >= excludedCount;
}

async function fillExcludedFacets() {
  if (excludedFacetsDone) return;
  const r = await apiGet("/api/library/facets");
  if (!r.ok || !r.data) return;
  excludedFacetsDone = true;
  fillFacet(excludedArtist, r.data.artists || [], "library.all_artists");
  fillFacet(excludedAlbum, r.data.albums || [], "library.all_albums");
  fillFacet(excludedGenre, r.data.genres || [], "library.all_genres");
}

/* `more` appends the next page; a plain refresh keeps what is already on
   screen, so the periodic one never pulls the list out from under a finger. */
async function refreshExcluded(more) {
  const card = document.getElementById("excludedCard");
  if (!excludedAvailable()) {
    card.hidden = true;
    return;
  }
  const offset = more ? excludedItems.length : 0;
  const limit = more ? EXCLUDED_PAGE : Math.max(EXCLUDED_PAGE, excludedItems.length);
  const r = await apiGet("/api/excluded?offset=" + offset + "&limit=" + limit);
  if (!r.ok || !r.data) {
    card.hidden = true;
    return;
  }
  excludedCount = r.data.count || 0;
  const items = r.data.items || [];
  excludedItems = more ? excludedItems.concat(items) : items;
  card.hidden = false;
  renderExcluded();
  fillExcludedFacets();
}

document.getElementById("excludedListMore").addEventListener("click", () => refreshExcluded(true));

document.getElementById("excludedRestoreAll").addEventListener("click", async () => {
  if (!excludedCount) return;
  const asked = await showConfirm(t("excluded.confirm_restore_all", { n: excludedCount }));
  if (!asked) return;
  const r = await apiPost("/api/excluded/restore", { all: true });
  if (!r.ok) {
    showToolError(t("excluded.failed"), r);
    return;
  }
  showToast(t("excluded.restored_all", { n: (r.data && r.data.count) || 0 }));
  refreshExcluded();
  refreshExcludedResults();
});

/* Adding: the same search as the library, with the same row buttons. A filter
   is acted on whole, not only on the thirty rows that fit on the page. */
const excludedSearch = document.getElementById("excludedSearch");
const excludedArtist = document.getElementById("excludedArtist");
const excludedAlbum = document.getElementById("excludedAlbum");
const excludedGenre = document.getElementById("excludedGenre");

function excludedAsked() {
  return excludedSearch.value.trim() || excludedArtist.value || excludedAlbum.value
    || excludedGenre.value;
}

function excludedFilterBody() {
  const body = {};
  const words = excludedSearch.value.trim();
  if (words) body.q = words;
  if (excludedArtist.value) body.artist = excludedArtist.value;
  if (excludedAlbum.value) body.album = excludedAlbum.value;
  if (excludedGenre.value) body.genre = excludedGenre.value;
  return body;
}

function excludedResultRow(item) {
  const li = document.createElement("li");
  li.append(trackMain(item));
  const actions = document.createElement("span");
  actions.className = "library-actions";
  const mark = excludedMark(item);
  if (mark) actions.append(mark);
  actions.append(item.excluded ? excludedRestoreButton(item) : excludedAddButton(item));
  actions.append(libraryButton(item, true));
  li.append(actions);
  return li;
}

function excludedAddButton(item) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "btn btn-icon btn-small";
  b.dataset.icon = "circle-minus";
  const name = item.title || item.name || "";
  b.title = t("excluded.add_one_aria", { title: name });
  b.setAttribute("aria-label", b.title);
  b.addEventListener("click", () => excludeTracks([item.key], name));
  return b;
}

async function excludeTracks(keys, name) {
  const r = await apiPost("/api/excluded", { keys: keys });
  if (!r.ok) {
    showToolError(t("excluded.failed"), r);
    return;
  }
  showToast(t("excluded.added", { title: name }));
  refreshExcluded();
  refreshExcludedResults();
}

async function refreshExcludedResults(more) {
  const seq = ++excludedSeq;
  const list = document.getElementById("excludedResults");
  const addAll = document.getElementById("excludedFilterAdd");
  const empty = document.getElementById("excludedResultsEmpty");
  if (!excludedAsked()) {
    list.replaceChildren();
    excludedMatches = 0;
    empty.hidden = true;
    addAll.hidden = true;
    document.getElementById("excludedMore").hidden = true;
    return;
  }
  const params = new URLSearchParams(excludedFilterBody());
  excludedOffset = more ? excludedOffset : 0;
  params.set("offset", String(excludedOffset));
  const r = await apiGet("/api/library?" + params.toString());
  if (seq !== excludedSeq) return;
  if (!r.ok || !r.data) {
    list.replaceChildren();
    empty.hidden = true;
    addAll.hidden = true;
    document.getElementById("excludedMore").hidden = true;
    return;
  }
  const d = r.data;
  const rows = d.items.map(excludedResultRow);
  if (more) list.append(...rows);
  else list.replaceChildren(...rows);
  excludedOffset += d.items.length;
  excludedMatches = d.total;
  empty.hidden = d.total > 0;
  addAll.hidden = d.total === 0;
  addAll.dataset.i18n = "excluded.add_filter";
  addAll.dataset.i18nVarN = String(d.total);
  addAll.textContent = t("excluded.add_filter", { n: d.total });
  document.getElementById("excludedMore").hidden = excludedOffset >= d.total;
}

document.getElementById("excludedFilterAdd").addEventListener("click", async () => {
  if (!excludedMatches) return;
  const asked = await showConfirm(t("excluded.confirm_filter", { n: excludedMatches }));
  if (!asked) return;
  const done = await apiPost("/api/excluded/filter", excludedFilterBody());
  if (!done.ok) {
    showToolError(t("excluded.failed"), done);
    return;
  }
  showToast(t("excluded.added_many", { n: (done.data && done.data.count) || 0 }));
  refreshExcluded();
  refreshExcludedResults();
});

document.getElementById("excludedMore").addEventListener("click", () => refreshExcludedResults(true));
excludedSearch.addEventListener("input", () => {
  clearTimeout(excludedTimer);
  excludedTimer = setTimeout(() => refreshExcludedResults(false), 300);
});
[excludedArtist, excludedAlbum, excludedGenre].forEach((select) => {
  select.addEventListener("change", () => refreshExcludedResults(false));
});

refreshExcluded();
refreshEvery(refreshExcluded, 120000);

let gameChoicesSignature = "";

