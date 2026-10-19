const THEME_KEY = "rukebox_theme_choice";

function systemPrefersDark() {
  return !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
}

function effectiveTheme(choice) {
  return choice === "system" ? (systemPrefersDark() ? "dark" : "light") : choice;
}

function applyTheme(choice) {
  document.documentElement.setAttribute("data-theme", choice);
  const btn = document.getElementById("themeToggleBtn");
  if (btn) {
    const dark = effectiveTheme(choice) === "dark";
    btn.dataset.icon = dark ? "moon" : "sun";
    const key = dark ? "theme.to_light" : "theme.to_dark";
    btn.dataset.i18nTitle = key;
    btn.dataset.i18nAriaLabel = key;
    btn.title = t(key);
    btn.setAttribute("aria-label", t(key));
  }

  const meta = document.getElementById("themeColorMeta");
  if (meta) {
    const bg = getComputedStyle(document.documentElement).getPropertyValue("--bg").trim();
    if (bg) meta.setAttribute("content", bg);
  }
}

const THEME_REVEAL_MS = 420;

function switchThemeFrom(btn, next) {
  let committed = false;
  const commit = () => {
    if (committed) return;
    committed = true;
    localStorage.setItem(THEME_KEY, next);
    applyTheme(next);
  };

  const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduce || typeof document.startViewTransition !== "function") {
    commit();
    return;
  }

  const box = btn.getBoundingClientRect();
  const x = box.left + box.width / 2;
  const y = box.top + box.height / 2;

  const reach = Math.hypot(Math.max(x, window.innerWidth - x), Math.max(y, window.innerHeight - y));
  const safety = setTimeout(commit, THEME_REVEAL_MS + 200);

  let transition;
  try {
    transition = document.startViewTransition(commit);
  } catch (e) {
    clearTimeout(safety);
    commit();
    return;
  }
  transition.ready.then(() => {
    clearTimeout(safety);
    document.documentElement.animate(
      { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${reach}px at ${x}px ${y}px)`] },
      { duration: THEME_REVEAL_MS, easing: "ease-in-out", pseudoElement: "::view-transition-new(root)" },
    );
  }).catch(() => {
    clearTimeout(safety);
    commit();
  });
}

/* When the header's buttons do not fit beside the title, they live in a menu (.compact). */
function fitTopbar() {
  const bar = document.querySelector(".topbar");
  const pill = document.getElementById("connPill");
  const controls = document.getElementById("topbarControls");
  if (!bar || !pill || !controls) return;
  const was = bar.classList.contains("compact");
  bar.classList.remove("compact");
  const gap = parseFloat(getComputedStyle(bar).columnGap) || 0;
  const brand = bar.querySelector(".brand");
  const tight = controls.getBoundingClientRect().left < pill.getBoundingClientRect().right + gap - 0.5
    || bar.scrollWidth > bar.clientWidth
    || (brand && brand.scrollWidth > brand.clientWidth + 0.5);
  bar.classList.toggle("compact", tight);
  if (was && !tight) setTopbarMenu(false);
}
if (typeof ResizeObserver === "function") {
  // The pill's label, the language and the logout button change the width as much as the window.
  const fit = new ResizeObserver(() => fitTopbar());
  [".topbar", ".brand", "#connPill", "#topbarControls"].forEach((sel) => {
    const el = document.querySelector(sel);
    if (el) fit.observe(el);
  });
}

function setTopbarMenu(open) {
  const bar = document.querySelector(".topbar");
  const more = document.getElementById("topbarMoreBtn");
  if (!bar || !more) return;
  bar.classList.toggle("menu-open", open);
  more.setAttribute("aria-expanded", String(open));
}
document.getElementById("topbarMoreBtn").addEventListener("click", (event) => {
  event.stopPropagation();
  setTopbarMenu(!document.querySelector(".topbar").classList.contains("menu-open"));
});
document.getElementById("topbarControls").addEventListener("click", () => setTimeout(() => setTopbarMenu(false), 0));
document.addEventListener("click", (event) => {
  if (!event.target.closest("#topbarControls, #topbarMoreBtn")) setTopbarMenu(false);
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") setTopbarMenu(false);
});

document.getElementById("themeToggleBtn").addEventListener("click", (event) => {
  const current = localStorage.getItem(THEME_KEY) || "system";
  const next = effectiveTheme(current) === "dark" ? "light" : "dark";
  switchThemeFrom(event.currentTarget, next);
});

if (window.matchMedia) {
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (!localStorage.getItem(THEME_KEY)) applyTheme("system");
  });
}

applyTheme(localStorage.getItem(THEME_KEY) || "system");

/* A page IS a card: the URL is #area/page, so Back works and a page can be linked to. */
const DEFAULT_VIEW = { tab: "home", page: "player" };
// Below this the side menu is icons only: no room for page names, the grid stays the menu.
const RAIL_QUERY = "(min-width: 1024px)";
const railQuery = window.matchMedia ? window.matchMedia(RAIL_QUERY) : null;

function railMode() {
  return !guestMode && !!railQuery && railQuery.matches;
}

// The cards are the page's own markup, never added later: each area's list is read once
// (the menus ask for it on every card shown or hidden).
const areaCardLists = {};
function areaCards(tab) {
  if (!areaCardLists[tab]) {
    areaCardLists[tab] = Array.from(document.querySelectorAll('.card[data-tab="' + tab + '"][data-page]'));
  }
  return areaCardLists[tab];
}

function cardOfPage(tab, page) {
  return areaCards(tab).find((card) => card.dataset.page === page) || null;
}

/* Declared up here: pageIsAvailable() reads it before the first status answer. */
let capabilities = null;

function canDo(need) {
  if (!need) return true;
  if (!capabilities) return true;
  return need.split(/\s+/).every((name) => capabilities[name] !== false);
}

function capabilityHidden(card) {
  return !canDo(card.dataset.needs);
}

/* The pages each role is offered. A page is a place, not a right: the server
   refuses what a role may not call whatever the page shows. */
const ROLE_PAGES = {
  guest: ["player", "upnext", "recent", "today", "library", "suggest", "game"],
  member: ["player", "upnext", "recent", "today", "library", "lists", "suggest", "game"],
};
// The accounts and the password: the owner's own, even of an operator.
const OWNER_ONLY_PAGES = ["people", "security", "sync"];

function pageIsOffered(card) {
  const page = card.dataset.page;
  if (myRole === "operator" && OWNER_ONLY_PAGES.includes(page)) return false;
  if (myRole === "owner" || myRole === "operator") return true;
  const offered = ROLE_PAGES[myRole] || [];
  if (!offered.includes(page)) return false;
  return !(myRole === "guest" && guestPagesOff.includes(page));
}

function pageIsAvailable(card) {
  if (!card || card.hasAttribute("hidden")) return false;
  if (capabilityHidden(card)) return false;
  if (!pageIsOffered(card)) return false;
  return !(card.dataset.level === "detail" && currentView() === "simple");
}

function availablePages(tab) {
  return areaCards(tab).filter(pageIsAvailable);
}

function titleKeyOf(card) {
  const span = card.querySelector("h2 [data-i18n]");
  return span ? span.dataset.i18n : null;
}

function gridTabOf(tab) {
  return availablePages(tab).length > 1 ? tab : DEFAULT_VIEW.tab;
}

let pageGrids = {};
let railPages = {};
/* A page asked for before its card has data is kept until it can be opened. */
let pendingPage = null;
/* The device list is rebuilt on every refresh, so folds and drafts live here. */
const openClientFolds = new Set();
const clientNameDrafts = new Map();

const RAIL_GROUP_STARTS = {
  home: ["player", "likes", "library", "suggest"],
  settings: ["settings", "lights", "announcements"],
  network: ["accesspoint", "clients", "guest"],
  system: ["system", "security", "update"],
  stats: ["overview", "sessions"],
};

function buildPageMenus() {
  const main = document.getElementById("main");
  const tabbar = document.querySelector(".tabbar");
  areaCards(document.body.dataset.tab || DEFAULT_VIEW.tab);   // no-op, keeps order clear
  document.querySelectorAll(".card[data-tab][data-page]").forEach((card) => {
    const tab = card.dataset.tab;
    if (!pageGrids[tab]) {
      const grid = document.createElement("nav");
      grid.className = "page-grid";
      grid.dataset.tab = tab;
      grid.hidden = true;
      main.insertBefore(grid, areaCards(tab)[0]);
      pageGrids[tab] = grid;

      const list = document.createElement("div");
      list.className = "rail-pages";
      list.dataset.tab = tab;
      const button = tabbar && tabbar.querySelector('.tab-btn[data-tab="' + tab + '"]');
      if (button) button.after(list);
      railPages[tab] = list;
    }
    const icon = card.querySelector("h2[data-icon]");
    const page = card.dataset.page;

    const tile = document.createElement("button");
    tile.type = "button";
    tile.className = "page-tile";
    tile.dataset.page = page;
    const tileIcon = document.createElement("span");
    tileIcon.className = "page-tile-icon";
    tileIcon.dataset.icon = (icon && icon.dataset.icon) || "home";
    tileIcon.setAttribute("aria-hidden", "true");
    const tileTitle = document.createElement("span");
    tileTitle.className = "page-tile-title";
    tile.append(tileIcon, tileTitle);
    tile.addEventListener("click", () => setActiveView(tab, page));
    pageGrids[tab].appendChild(tile);

    if (railPages[tab]) {
      const entry = document.createElement("button");
      entry.type = "button";
      entry.className = "rail-page";
      if ((RAIL_GROUP_STARTS[tab] || []).includes(page)) entry.classList.add("is-group-start");
      entry.dataset.tab = tab;
      entry.dataset.page = page;
      entry.addEventListener("click", () => setActiveView(tab, page));
      railPages[tab].appendChild(entry);
    }
  });
}

function refreshPageMenus() {
  Object.keys(pageGrids).forEach((tab) => {
    pageGrids[tab].setAttribute("aria-label", t("tab." + tab));
    pageGrids[tab].querySelectorAll(".page-tile").forEach((tile) => {
      const card = cardOfPage(tab, tile.dataset.page);
      const key = card ? titleKeyOf(card) : null;
      tile.querySelector(".page-tile-title").textContent = key ? t(key) : "";
      const icon = card && card.querySelector("h2[data-icon]");
      if (icon) tile.querySelector(".page-tile-icon").dataset.icon = icon.dataset.icon;
      tile.hidden = !pageIsAvailable(card);
      tile.classList.toggle("is-attention",
                            !!card && card.dataset.page === DEFAULT_VIEW.page);
    });
    fillPageGrid(pageGrids[tab]);
    if (railPages[tab]) {
      railPages[tab].querySelectorAll(".rail-page").forEach((entry) => {
        const card = cardOfPage(tab, entry.dataset.page);
        const key = card ? titleKeyOf(card) : null;
        entry.textContent = key ? t(key) : "";
        entry.hidden = !pageIsAvailable(card);
        const on = entry.dataset.page === (document.body.dataset.page || null);
        entry.classList.toggle("active", on);
        if (on) entry.setAttribute("aria-current", "page");
        else entry.removeAttribute("aria-current");
      });
    }
  });
  paintAreaTabs();
}

/* An area with no page left (Network on a container) loses its tab. */
function paintAreaTabs() {
  document.querySelectorAll(".tab-btn[data-tab]").forEach((btn) => {
    const empty = availablePages(btn.dataset.tab).length === 0;
    btn.hidden = empty;
    btn.toggleAttribute("data-empty", empty);
  });
  const tab = document.body.dataset.tab;
  if (tab && availablePages(tab).length === 0) {
    setActiveView(DEFAULT_VIEW.tab, null, { hash: false });
  }
}

/* One cell per missing column (a spanning cell draws no line inside it). The count
   comes from the first row's tiles: the computed tracks lag while the layout settles. */
function fillPageGrid(grid) {
  if (grid.hidden || !grid.offsetParent) return;
  const tiles = Array.from(grid.querySelectorAll(".page-tile")).filter((t) => !t.hidden);
  const firstTop = tiles.length ? tiles[0].offsetTop : 0;
  const columns = tiles.filter((t) => t.offsetTop === firstTop).length;
  const want = tiles.length > columns ? (columns - (tiles.length % columns)) % columns : 0;
  grid.querySelectorAll(".page-filler").forEach((filler) => filler.remove());
  for (let i = 0; i < want; i++) {
    const filler = document.createElement("span");
    filler.className = "page-filler";
    filler.setAttribute("aria-hidden", "true");
    grid.appendChild(filler);
  }
}

/* The column count moves with the width, and a stale one leaves a hole or an extra row. */
let pageGridResize = null;
window.addEventListener("resize", () => {
  clearTimeout(pageGridResize);
  pageGridResize = setTimeout(() => {
    Object.keys(pageGrids).forEach((tab) => fillPageGrid(pageGrids[tab]));
  }, 150);
});

function scrollAppTop() {
  const main = document.getElementById("main");
  if (main) main.scrollTop = 0;
}

/* The top padding is the bar's own height, which carries the safe-area inset. */
function measureTopbar() {
  const bar = document.querySelector(".topbar");
  if (bar) document.documentElement.style.setProperty("--topbar-h", bar.offsetHeight + "px");
}
measureTopbar();
window.addEventListener("resize", measureTopbar);
if (window.ResizeObserver) new ResizeObserver(measureTopbar).observe(document.querySelector(".topbar"));

function setActiveView(tab, page, options) {
  const opts = options || {};
  if (!areaCards(tab).length) tab = DEFAULT_VIEW.tab;
  const available = availablePages(tab);
  if (page != null && !available.some((card) => card.dataset.page === page)) {
    // Kept until the card appears (see the observer below).
    pendingPage = cardOfPage(tab, page) ? page : null;
    page = null;
  } else if (page != null) {
    pendingPage = null;
  }
  // Where the rail is, its pages ARE the menu: open a page rather than a grid.
  if (page == null && available.length && (available.length === 1 || railMode())) {
    page = available[0].dataset.page;
  }
  page = page || null;

  document.body.dataset.tab = tab;
  document.documentElement.dataset.tab = tab;
  document.body.dataset.page = page || "";
  document.querySelectorAll(".card[data-tab]").forEach((card) => {
    const on = card.dataset.tab === tab && page !== null && card.dataset.page === page;
    card.classList.toggle("tab-hidden", !on);
  });
  Object.keys(pageGrids).forEach((key) => {
    pageGrids[key].hidden = !(key === tab && page === null);
  });
  placePortalButton();

  document.querySelectorAll(".tab-btn").forEach((btn) => {
    const on = btn.dataset.tab === tab;
    btn.classList.toggle("active", on);
    if (on) btn.setAttribute("aria-current", "page");
    else btn.removeAttribute("aria-current");
  });
  document.querySelectorAll(".rail-pages").forEach((list) => {
    list.hidden = list.dataset.tab !== tab;
  });

  const backTab = gridTabOf(tab);
  const back = document.getElementById("pageBack");
  if (back) {
    back.disabled = page === null;
    back.classList.toggle("is-back", page !== null);
    back.setAttribute("aria-label", t("nav.back_to", { name: t("tab." + backTab) }));
    back.title = t("nav.back_to", { name: t("tab." + backTab) });
  }

  const title = document.getElementById("pageTitle");
  if (title) {
    const open = page ? cardOfPage(tab, page) : null;
    const key = (open && titleKeyOf(open)) || "tab." + tab;
    title.dataset.i18n = key;
    title.textContent = t(key);
  }

  refreshPageMenus();
  document.dispatchEvent(new CustomEvent("page-shown", { detail: { tab, page } }));

  if (opts.hash !== false && !pendingPage) {
    const hash = "#" + tab + (page ? "/" + page : "");
    if (window.location.hash !== hash) {
      if (opts.replace) window.history.replaceState(null, "", hash);
      else window.location.hash = hash;
    }
  }
  if (opts.scroll !== false) scrollAppTop();
}

function viewFromHash() {
  const raw = (window.location.hash || "").replace(/^#/, "");
  if (!raw) return null;
  const parts = raw.split("/");
  return { tab: parts[0], page: parts[1] || null };
}

window.addEventListener("hashchange", () => {
  const view = viewFromHash();
  if (!view) return;
  if (document.body.dataset.tab === view.tab &&
      (document.body.dataset.page || "") === (view.page || "")) return;
  setActiveView(view.tab, view.page, { hash: false });
});

document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => setActiveView(btn.dataset.tab, null));
});

document.getElementById("pageBack").addEventListener("click", () => {
  setActiveView(gridTabOf(document.body.dataset.tab), null);
});

/* A grid that is no longer shown must hand over to a page, or the screen stays empty. */
if (railQuery && railQuery.addEventListener) {
  railQuery.addEventListener("change", () => {
    const tab = document.body.dataset.tab || DEFAULT_VIEW.tab;
    const page = document.body.dataset.page || null;
    if (page === null && railMode() && availablePages(tab).length) {
      setActiveView(tab, null);
    } else if (page !== null && !railMode() && !pageIsAvailable(cardOfPage(tab, page))) {
      setActiveView(tab, null);
    }
  });
}

window.LANG_CHANGE_LISTENERS.push(() => {
  setActiveView(document.body.dataset.tab, document.body.dataset.page || null,
                { hash: false, scroll: false });
});

const cardsObserver = new MutationObserver(() => {
  const tab = document.body.dataset.tab || DEFAULT_VIEW.tab;
  if (pendingPage) {
    const wanted = cardOfPage(tab, pendingPage);
    if (!wanted) pendingPage = null;
    else if (pageIsAvailable(wanted)) {
      pendingPage = null;
      setActiveView(tab, wanted.dataset.page, { hash: false, scroll: false });
      return;
    }
  }
  refreshPageMenus();
});

buildPageMenus();
document.querySelectorAll("#main > .card[data-tab]").forEach((card) => {
  cardsObserver.observe(card, { attributes: true, attributeFilter: ["hidden"] });
});
/* An arrival with no # shows the player - unless the portal still holds the device. */
const startView = viewFromHash() || DEFAULT_VIEW;
const arrivedWithoutHash = !window.location.hash;
setActiveView(startView.tab, startView.page, { replace: arrivedWithoutHash, scroll: false });

const VIEW_KEY = "rukebox_view";

function currentView() {
  return document.documentElement.dataset.view === "detailed" ? "detailed" : "simple";
}

function paintViewBar() {
  const simple = currentView() === "simple";
  const text = document.getElementById("viewBarText");
  const btn = document.getElementById("viewBarBtn");
  text.dataset.i18n = simple ? "view.simple_note" : "view.detailed_note";
  text.textContent = t(text.dataset.i18n);
  btn.dataset.i18n = simple ? "view.show_all" : "view.back_simple";
  btn.textContent = t(btn.dataset.i18n);
}

function setViewMode(view) {
  document.documentElement.dataset.view = view;
  try { localStorage.setItem(VIEW_KEY, view); } catch (e) {  }
  paintViewBar();
  const tab = document.body.dataset.tab || DEFAULT_VIEW.tab;
  const open = document.body.dataset.page ? cardOfPage(tab, document.body.dataset.page) : null;
  if (document.body.dataset.page && !pageIsAvailable(open)) setActiveView(tab, null, { hash: false });
  else refreshPageMenus();
}

function showDetailed() {
  if (currentView() === "detailed") return;
  setViewMode("detailed");
  showToast(t("view.switched"));
}

document.getElementById("viewBarBtn").addEventListener("click", () => {
  setViewMode(currentView() === "simple" ? "detailed" : "simple");

  document.getElementById("viewBar").scrollIntoView({ block: "end" });
});
paintViewBar();

renderKpis(null);
renderRecentStats(null);

function handleUnauthorized(res) {
  if (res.status !== 401) return false;

  // A guest legitimately gets 401s here: reloading would loop forever.
  if (!guestMode) location.reload();
  return true;
}

async function apiFetch(path, options) {
  if (path.startsWith("/api/") && !mayCall(path)) {
    return { ok: false, error: "auth_required" };
  }
  if (bootWaits(path)) await bootGate;
  noteRequestStarted();
  let res;
  try {
    res = await fetch(path, withDeviceHeader(options));
  } catch (e) {
    setConnectionState(false);
    return { ok: false, error: "unreachable" };
  } finally {
    noteRequestFinished();
  }
  if (handleUnauthorized(res)) return { ok: false, error: "auth_required" };
  try {
    const data = await res.json();
    if (res.status === 403 && data && data.error === "banned") showBanned();

    setConnectionState(!!data && typeof data === "object" && typeof data.ok === "boolean");
    return data;
  } catch (e) {
    setConnectionState(false);
    return { ok: false, error: "bad_response" };
  }
}

const DEVICE_KEY = "rukebox_device";
function withDeviceHeader(options) {
  let token = null;
  try { token = localStorage.getItem(DEVICE_KEY); } catch (e) {  }
  if (!token) return options;
  const opts = Object.assign({}, options || {});
  opts.headers = Object.assign({}, opts.headers || {}, { "X-Rukebox-Device": token });
  return opts;
}
async function rememberDevice() {
  const r = await apiGet("/api/device");
  if (r.ok && r.data && r.data.token) {
    try { localStorage.setItem(DEVICE_KEY, r.data.token); } catch (e) {  }
  }
}

function showBanned() {
  showBootOverlay("boot.banned");
}

function apiGet(path) {
  return apiFetch(path);
}

function apiPost(path, body) {
  return apiFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
}

function apiDelete(path, body) {
  const options = { method: "DELETE" };
  if (body) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(body);
  }
  return apiFetch(path, options);
}

function setConnDot(ok) {
  const dot = document.getElementById("connDot");
  dot.classList.toggle("ok", ok);
  dot.classList.toggle("error", !ok);

  const label = document.getElementById("connLabel");
  if (label) {
    const key = ok ? "conn.online" : (piGoingDown === "poweroff" ? "conn.powered_off" : "conn.offline");
    if (label.dataset.i18n !== key) {
      label.dataset.i18n = key;
      label.textContent = t(key);
    }
  }
}

const CONNECTION_LOST_AFTER = 2;
let connectionFailures = 0;
let overlayForConnection = false;

function showBootOverlay(key, subKey) {
  bootMessage(key, false, subKey);
  const overlay = document.getElementById("bootOverlay");
  overlay.classList.remove("boot-gone");
  overlay.hidden = false;
}

let piGoingDown = null;
let goingDownSince = 0;
let updateSeenDone = false;

function setGoingDown(kind, askedAt) {
  if (kind === piGoingDown) return;
  if (!kind && askedAt !== undefined && askedAt < goingDownSince) return;
  const wasUpdating = piGoingDown === "update";
  piGoingDown = kind;
  if (kind) goingDownSince = Date.now();
  if (kind) {
    overlayForConnection = true;
    showBootOverlay(kind === "reboot" ? "boot.rebooting"
      : kind === "update" ? "boot.updating" : "boot.powered_off",
    kind === "update" ? "boot.updating_note" : undefined);
  } else if (overlayForConnection) {
    overlayForConnection = false;
    connectionFailures = 0;
    hideBootOverlay();
    if (wasUpdating && !updateSeenDone) {
      updateSeenDone = true;
      setTimeout(() => window.location.reload(), BOOT_FADE_MS + 100);
    }
  }
}

function noteServerAnswer(reachable) {
  if (piGoingDown) return;
  if (reachable) {
    connectionFailures = 0;
    if (overlayForConnection) {
      overlayForConnection = false;
      hideBootOverlay();
    }
    return;
  }
  connectionFailures += 1;
  if (connectionFailures < CONNECTION_LOST_AFTER || overlayForConnection) return;
  overlayForConnection = true;
  showBootOverlay("boot.lost", "boot.lost_retrying");
}

function setConnectionState(reachable) {
  setConnDot(reachable);
  noteServerAnswer(reachable);
}

let modalResolve = null;

