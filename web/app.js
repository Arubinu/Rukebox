"use strict";

function setLogoutVisible(on) {
  const btn = document.getElementById("logoutBtn");
  if (btn) btn.hidden = !on;
}

document.getElementById("logoutBtn").addEventListener("click", async () => {
  try {
    await fetch("/api/auth/logout", { method: "POST" });
  } catch (e) {  }

  location.reload();
});

function portalHolds(data) {
  return !!data.on_ap && !!data.enabled &&
         ["release", "new_only"].includes(data.mode) && !data.released;
}

/* A captive window cannot be closed by a page: once released, it is sent to the address its
   own system probes, which now answers "no portal" - the navigation is what makes it look again. */
const CAPTIVE_PROBE_APPLE = "http://captive.apple.com/hotspot-detect.html";
const CAPTIVE_PROBE_GOOGLE = "http://connectivitycheck.gstatic.com/generate_204";

function applePlatform(ua) {
  return /iPhone|iPad|iPod|Macintosh/.test(ua === undefined ? navigator.userAgent || "" : ua);
}

/* Apple's portal window is a bare web view: unlike every browser there, its name has no "Safari/". */
function appleCaptiveWindow(ua) {
  const name = ua === undefined ? navigator.userAgent || "" : ua;
  return applePlatform(name) && !/Safari\//.test(name);
}

/* Where the release sends this window, or null to leave it where it is: a real browser on an
   Apple device would only land on a blank page it has to come back from. */
function captiveExit(ua) {
  const name = ua === undefined ? navigator.userAgent || "" : ua;
  if (!applePlatform(name)) return CAPTIVE_PROBE_GOOGLE;
  return appleCaptiveWindow(name) ? CAPTIVE_PROBE_APPLE : null;
}

/* Done at once, never on a timer: the page is replaced as soon as this runs. assign(), not replace(). */
function leaveCaptiveWindow() {
  const target = captiveExit();
  if (target) window.location.assign(target);
}

async function offerPortalReleaseOnLogin() {
  const btn = document.getElementById("loginPortalRelease");
  try {
    const res = await fetch("/api/portal/status");
    const data = (await res.json()).data || {};
    btn.hidden = !portalHolds(data);
  } catch (e) {
    btn.hidden = true;
  }
}
document.getElementById("loginPortalRelease").addEventListener("click", async () => {
  const btn = document.getElementById("loginPortalRelease");
  btn.disabled = true;
  try {
    const res = await fetch("/api/portal/release", { method: "POST" });
    btn.hidden = true;
    // Without a navigation an iPhone never looks again, and its window stays on "Cancel".
    if (res.ok && appleCaptiveWindow()) leaveCaptiveWindow();
  } catch (e) {  }
  btn.disabled = false;
});

let installPrompt = null;
window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  installPrompt = e;
});
if ("serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("/sw.js").catch(() => {  });
}
function installHowTo() {
  const ua = navigator.userAgent || "";
  if (/iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1)) return "share.install_ios";
  if (/Android/.test(ua)) return "share.install_android";
  return "share.install_desktop";
}

function showLoginOverlay(errorMessage) {
  offerPortalReleaseOnLogin();
  const overlay = document.getElementById("loginOverlay");
  overlay.hidden = false;
  const errorBox = document.getElementById("loginError");
  errorBox.textContent = errorMessage || "";
  errorBox.hidden = !errorMessage;
  document.getElementById("loginPassword").focus();
}

function hideLoginOverlay() {
  document.getElementById("loginOverlay").hidden = true;
}

let loginLockTimer = null;

function lockLogin(seconds) {
  const btn = document.getElementById("loginSubmit");
  const errorBox = document.getElementById("loginError");
  clearInterval(loginLockTimer);
  const until = Date.now() + seconds * 1000;
  const tick = () => {
    const left = Math.ceil((until - Date.now()) / 1000);
    if (left <= 0) {
      clearInterval(loginLockTimer);
      btn.disabled = false;
      errorBox.hidden = true;
      return;
    }
    btn.disabled = true;
    errorBox.hidden = false;
    errorBox.textContent = t("login.locked", { s: left });
  };
  tick();
  loginLockTimer = setInterval(tick, 1000);
}

document.getElementById("loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const password = document.getElementById("loginPassword").value;
  const btn = document.getElementById("loginSubmit");
  let lockFor = 0;
  btn.disabled = true;
  try {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    const result = await res.json();
    if (result.ok) {
      hideLoginOverlay();

      if (guestMode) {
        location.reload();
        return;
      }
      initApp();
    } else {
      showLoginOverlay(result.error === "too_many_attempts" ? "" : t("login.wrong_password"));
      lockFor = Number(result.retry_after) || 0;
    }
  } catch (err) {
    showLoginOverlay(t("login.connection_error"));
  } finally {
    btn.disabled = false;
    document.getElementById("loginPassword").value = "";
    if (lockFor > 0) lockLogin(lockFor);
  }
});

let guestMode = false;

// Guest-allowed paths: keep in step with web_server.py's _GUEST_PATHS (a test compares them).
const GUEST_API_PATHS = [
  "/api/status",
  "/api/status/wait",
  "/api/volume",
  "/api/action/single_click",
  "/api/action/double_click",
  "/api/action/start_music",

  "/api/action/next_track",
  "/api/action/previous_track",
  "/api/action/toggle_pause",
  "/api/action/skip_sound",
  "/api/action/announce",
  "/api/announcements",
  "/api/portal/status",
  "/api/portal/release",

  "/api/now/cover",
  "/api/now/lyrics",
  "/api/recent",

  "/api/queue",
  "/api/today",
  "/api/library",
  "/api/library/facets",
  "/api/library/queue",
  "/api/library/play",
  "/api/audio/fallback",
  "/api/device",

  "/api/suggestions",
  "/api/suggestions/vote",
  "/api/suggestions/delete",
  "/api/suggestions/name",
  "/api/devices/link_code",
  "/api/devices/link_join",
  "/api/auth/",
];

function guestMayCall(path) {
  const clean = path.split("?")[0];
  return GUEST_API_PATHS.some((p) => clean === p || clean.startsWith("/api/auth/"));
}

const BOOT_ASK_TIMEOUT_MS = 6000;
const BOOT_RETRY_MS = 2500;

const BOOT_GIVE_UP_MS = 40000;

let booting = false;

const BOOT_QUIET_MS = 400;
const BOOT_POPULATE_TIMEOUT_MS = 6000;

let requestsInFlight = 0;
let quietTimer = null;
let quietResolve = null;
const bootQuiet = new Promise((resolve) => { quietResolve = resolve; });

function noteRequestStarted() {
  requestsInFlight += 1;
  if (quietTimer) {
    clearTimeout(quietTimer);
    quietTimer = null;
  }
}

function noteRequestFinished() {
  requestsInFlight -= 1;
  if (requestsInFlight > 0 || !quietResolve) return;
  if (quietTimer) clearTimeout(quietTimer);
  quietTimer = setTimeout(() => {
    quietTimer = null;
    if (quietResolve) quietResolve();
  }, BOOT_QUIET_MS);
}

function waitForCards() {
  return Promise.race([
    bootQuiet,
    new Promise((resolve) => setTimeout(resolve, BOOT_POPULATE_TIMEOUT_MS)),
  ]);
}

function bootMessage(key, showRetry, subKey) {
  const message = document.getElementById("bootMessage");
  message.dataset.i18n = key;
  message.textContent = t(key);
  const sub = document.getElementById("bootSubMessage");
  sub.hidden = !subKey;
  if (subKey) {
    sub.dataset.i18n = subKey;
    sub.textContent = t(subKey);
  } else {
    delete sub.dataset.i18n;
    sub.textContent = "";
  }
  document.getElementById("bootRetry").hidden = !showRetry;

  document.getElementById("bootOverlay").dataset.state =
    key === "boot.not_rukebox" || key === "boot.banned" ? "error"
      : key === "boot.connecting" ? "loading"
        : key === "boot.powered_off" ? "off"
          : "waiting";
}

const BOOT_FADE_MS = 340;

function hideBootOverlay() {
  const overlay = document.getElementById("bootOverlay");
  if (!overlay || overlay.hidden) return;
  overlay.classList.add("boot-gone");

  let done = false;
  const finish = () => {
    if (done) return;
    done = true;
    overlay.hidden = true;
    overlay.classList.remove("boot-gone");
  };
  overlay.addEventListener("transitionend", finish, { once: true });
  setTimeout(finish, BOOT_FADE_MS + 120);
}

async function askServer() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), BOOT_ASK_TIMEOUT_MS);
  try {
    const res = await fetch("/api/portal/status", { signal: controller.signal });
    let data;
    try {
      data = await res.json();
    } catch (parseError) {
      return "wrong_server";
    }
    if (!data || typeof data !== "object" || typeof data.ok !== "boolean") return "wrong_server";
    return { data };
  } catch (err) {
    return "no_answer";
  } finally {
    clearTimeout(timer);
  }
}

async function waitForServer() {
  const started = Date.now();
  for (;;) {
    const answer = await askServer();
    if (answer !== "no_answer") return answer;
    if (Date.now() - started > BOOT_GIVE_UP_MS) return "no_answer";
    bootMessage("boot.no_answer", true);
    await new Promise((resolve) => setTimeout(resolve, BOOT_RETRY_MS));
  }
}

async function boot() {
  if (booting) return;
  booting = true;
  try {
    const answer = await waitForServer();
    if (typeof answer === "string") {
      bootMessage(answer === "wrong_server" ? "boot.not_rukebox" : "boot.no_answer", true);
      return;
    }
    const result = answer.data;

    if (result.ok === false && result.error === "auth_required") {
      hideBootOverlay();
      showLoginOverlay();
      return;
    }
    setLogoutVisible(!!(result.data && result.data.auth_required && result.data.authenticated));
    if (result.data && result.data.auth_required && !result.data.authenticated) {
      if (result.data.guest_mode) {
        guestMode = true;
        document.body.dataset.access = "guest";
      } else {
        hideBootOverlay();
        showLoginOverlay();
        return;
      }
    }
    try {
      await initApp();

      await waitForCards();
    } finally {
      hideBootOverlay();
    }
  } finally {
    booting = false;
  }
}

document.getElementById("bootRetry").addEventListener("click", () => {
  bootMessage("boot.connecting", false);
  boot();
});

boot();

async function initApp() {
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
const RAIL_QUERY = "(min-width: 640px)";
const railQuery = window.matchMedia ? window.matchMedia(RAIL_QUERY) : null;

function railMode() {
  return !guestMode && !!railQuery && railQuery.matches;
}

function areaCards(tab) {
  return Array.from(document.querySelectorAll('.card[data-tab="' + tab + '"][data-page]'));
}

function cardOfPage(tab, page) {
  return areaCards(tab).find((card) => card.dataset.page === page) || null;
}

function pageIsAvailable(card) {
  if (!card || card.hasAttribute("hidden")) return false;
  if (guestMode && card.hasAttribute("data-owner")) return false;
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
  settings: ["settings", "buttons", "announcements"],
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
}

/* Empty cells fill the last row: the separators are the grid's own background showing through. */
function fillPageGrid(grid) {
  if (grid.hidden || !grid.offsetParent) return;
  let columns = 0;
  try {
    columns = String(getComputedStyle(grid).gridTemplateColumns || "").split(" ").filter(Boolean).length;
  } catch (error) {
    columns = 0;
  }
  if (columns < 2) return;
  const shown = Array.from(grid.querySelectorAll(".page-tile")).filter((t) => !t.hidden).length;
  const have = grid.querySelectorAll(".page-filler").length;
  const want = shown ? (columns - (shown % columns)) % columns : 0;
  for (let i = have; i < want; i++) {
    const filler = document.createElement("span");
    filler.className = "page-filler";
    filler.setAttribute("aria-hidden", "true");
    grid.appendChild(filler);
  }
  grid.querySelectorAll(".page-filler").forEach((filler, index) => {
    filler.hidden = index >= want;
  });
}

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
  if (guestMode && path.startsWith("/api/") && !guestMayCall(path)) {
    return { ok: false, error: "auth_required" };
  }
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

function closeModal(value) {
  if (!modalResolve) return;
  const resolve = modalResolve;
  modalResolve = null;
  document.getElementById("modalOverlay").hidden = true;
  resolve(value);
}

function openModal({ title, body, bodyNode, confirm, choices, actions, modalClass }) {
  closeModal(false);
  return new Promise((resolve) => {
    modalResolve = resolve;
    document.getElementById("modalTitle").textContent = title;
    document.querySelector("#modalOverlay .modal").className =
      "modal" + (modalClass ? " " + modalClass : "");

    const bodyBox = document.getElementById("modalBody");
    if (bodyNode) {
      bodyBox.replaceChildren(bodyNode);
    } else {
      bodyBox.replaceChildren();
      bodyBox.textContent = body || "";
    }
    bodyBox.hidden = !body && !bodyNode;

    document.getElementById("modalCancel").hidden = !confirm;
    // A dialog whose content carries its own ways out shows the close cross and nothing else.
    const actionsBox = document.querySelector("#modalOverlay .modal-actions");
    actionsBox.hidden = actions === false;

    const box = document.getElementById("modalChoices");
    box.textContent = "";
    box.hidden = !choices;
    document.getElementById("modalOk").hidden = !!choices;
    if (choices) {
      choices.forEach((choice) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "btn" + (choice.danger ? " btn-danger" : "");
        btn.textContent = choice.label;
        btn.addEventListener("click", () => closeModal(choice.value));
        box.appendChild(btn);
      });
    }

    const overlay = document.getElementById("modalOverlay");
    overlay.hidden = false;
    const target = choices ? box.firstChild
      : actions === false ? document.getElementById("modalClose")
        : document.getElementById("modalOk");
    target.focus();
  });
}

const TOAST_SECONDS = 5;

function errorLabel(code) {
  if (!code) return t("common.unknown_error");
  const key = "err." + code;
  const translated = t(key);
  return translated === key ? code : translated;
}

function dismissToast(toast) {
  if (toast.dataset.leaving) return;
  toast.dataset.leaving = "1";
  toast.classList.add("toast-leaving");

  setTimeout(() => toast.remove(), 200);
}

const HAPTICS_KEY = "rukebox_haptics";
const HAPTIC_TAP_MS = 30;
function hapticsOn() {
  try {
    return localStorage.getItem(HAPTICS_KEY) !== "off";
  } catch (e) {
    return true;
  }
}
function haptic(pattern) {
  if (!hapticsOn() || typeof navigator.vibrate !== "function") return;
  try {
    navigator.vibrate(pattern);
  } catch (e) {  }
}

document.addEventListener("click", (event) => {
  const el = event.target.closest && event.target.closest(
    "button, .btn, input[type=checkbox], summary, .tabbar a, .tabbar button");
  if (el && !el.disabled) haptic(HAPTIC_TAP_MS);
}, true);

function showToast(title, detail, options) {
  const opts = options || {};
  if (opts.error) haptic([30, 40, 30]);
  const toast = document.createElement("div");
  toast.className = "toast" + (opts.error ? " toast-error" : "");
  toast.setAttribute("role", opts.error ? "alert" : "status");

  const titleLine = document.createElement("div");
  titleLine.className = "toast-title";
  titleLine.textContent = title;
  toast.appendChild(titleLine);

  if (detail) {
    const detailLine = document.createElement("div");
    detailLine.className = "toast-detail";
    detailLine.textContent = detail;
    toast.appendChild(detailLine);
  }

  if (opts.action) {
    const actionBtn = document.createElement("button");
    actionBtn.type = "button";
    actionBtn.className = "toast-action";
    actionBtn.textContent = opts.action.label;
    if (opts.action.icon) actionBtn.dataset.icon = opts.action.icon;
    actionBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      dismissToast(toast);
      opts.action.run();
    });
    toast.appendChild(actionBtn);
  }

  toast.addEventListener("click", () => dismissToast(toast));
  document.getElementById("toastStack").appendChild(toast);

  const seconds = opts.action ? TOAST_SECONDS * 2 : TOAST_SECONDS;
  if (!opts.sticky) setTimeout(() => dismissToast(toast), seconds * 1000);
  return toast;
}

function showError(code, title) {
  return showToast(title || t("common.failed"), errorLabel(code), { error: true });
}

function showConfirm(body, title) {
  return openModal({ title: title || t("common.confirm"), body, confirm: true });
}

function showChoice(body, choices, title) {
  return openModal({
    title: title || t("common.confirm"),
    body,
    confirm: true,
    choices,
  });
}

document.getElementById("modalOk").addEventListener("click", () => closeModal(true));
document.getElementById("modalCancel").addEventListener("click", () => closeModal(false));
document.getElementById("modalClose").addEventListener("click", () => closeModal(false));

document.getElementById("modalOverlay").addEventListener("click", (e) => {
  if (e.target === e.currentTarget) closeModal(false);
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && modalResolve) closeModal(false);
});

let userIsDraggingVolume = false;

let volumePending = null;
const VOLUME_HOLD_MS = 5000;

function setVolumeFill(el) {
  el.style.setProperty("--pct", el.value + "%");
}

const MODE_KEYS = ["music", "idle", "stopped", "meme", "cutoff_announce", "shutting_down", "restarting"];

function modeLabel(mode) {
  if (MODE_KEYS.includes(mode)) return t("mode." + mode);

  if (/^(custom|button_announce):/.test(mode || "")) return t("mode.announcement");
  return mode;
}

const BRAND_CLOCK_SWAP_MS = 6000;
let piClockOffsetMs = null;
let piTimeZone;

function formatPiTime(date, options) {
  try {
    return new Intl.DateTimeFormat(undefined, { ...options, timeZone: piTimeZone }).format(date);
  } catch (e) {
    return new Intl.DateTimeFormat(undefined, options).format(date);
  }
}

function renderBrandClock() {
  if (piClockOffsetMs === null) return;
  const now = new Date(Date.now() + piClockOffsetMs);
  const time = formatPiTime(now, { hour: "2-digit", minute: "2-digit" });
  const date = formatPiTime(now, { weekday: "short", day: "numeric", month: "short" });
  const label = t("footer.pi_time", { time: date + " " + time });
  document.querySelectorAll(".brand-clock").forEach((box) => {
    box.querySelector(".bc-time").textContent = time;
    box.querySelector(".bc-date").textContent = date;
    box.title = label;
    box.classList.add("ready");
  });
}

function syncPiClock(d) {
  if (typeof d.epoch !== "number") return;
  piClockOffsetMs = d.epoch * 1000 - (d.sampledAt || Date.now());
  piTimeZone = d.timezone || undefined;
  renderBrandClock();
  renderClockSync();
  syncTimezoneSelect(d.timezone);
}

const CLOCK_SYNC_TOLERANCE_SEC = 30;

function formatDrift(seconds) {
  const s = Math.round(seconds);
  if (s < 90) return s + " s";
  if (s < 90 * 60) return Math.round(s / 60) + " min";
  if (s < 48 * 3600) {
    const h = Math.floor(s / 3600);
    const m = Math.round((s % 3600) / 60);
    return h + " h" + (m ? " " + m + " min" : "");
  }
  return t("clock.drift_days", { n: Math.round(s / 86400) });
}

function renderClockSync() {
  const row = document.getElementById("clockSync");
  if (!row || piClockOffsetMs === null) return;
  const drift = piClockOffsetMs / 1000;
  const off = Math.abs(drift) > CLOCK_SYNC_TOLERANCE_SEC;
  const text = !off ? t("clock.sync_ok")
    : t(drift > 0 ? "clock.sync_ahead" : "clock.sync_behind", { d: formatDrift(Math.abs(drift)) });
  document.getElementById("clockSyncText").textContent = text;
  row.dataset.state = off ? "warn" : "ok";

  document.getElementById("btnUseDeviceTime").classList.toggle("btn-primary", off);
  row.hidden = false;
}
window.LANG_CHANGE_LISTENERS.push(renderClockSync);

const ON_SHOW_REFRESHES = [];
function refreshEvery(callback, ms) {
  ON_SHOW_REFRESHES.push(callback);
  return setInterval(() => {
    if (!document.hidden) callback();
  }, ms);
}

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) ON_SHOW_REFRESHES.forEach((callback) => callback());
});

refreshEvery(renderBrandClock, 1000);
setInterval(() => {
  document.querySelectorAll(".brand-clock").forEach((box) => {
    box.dataset.show = box.dataset.show === "time" ? "date" : "time";
  });
}, BRAND_CLOCK_SWAP_MS);
window.LANG_CHANGE_LISTENERS.push(renderBrandClock);

let statusRequestSeq = 0;
let statusPaintedSeq = 0;

let nowPlayingShown = null;
function setNowPlayingText(title, artist) {
  const signature = title + "\u0000" + artist;
  if (signature === nowPlayingShown) return;
  const first = nowPlayingShown === null;
  nowPlayingShown = signature;
  const box = document.getElementById("npText");
  document.getElementById("npTrack").textContent = title;

  document.getElementById("npArtist").textContent = artist || "\u00a0";
  if (first) return;
  box.classList.remove("np-enter");
  void box.offsetWidth;
  box.classList.add("np-enter");
}

const SOUND_LINE_MIN_MS = 5000;
let soundClearTimer = null;
function soundText(file, announcement, meme) {
  if (announcement) return t("now.sound_announcement", { name: announcement, file });
  return t(meme ? "now.sound_meme" : "now.sound_other", { file });
}
function applySoundLine(d) {
  const line = document.getElementById("npSound");
  const last = d.last_sound;
  const playing = !!d.sound;
  const recent = last && typeof last.age === "number" && last.age * 1000 < SOUND_LINE_MIN_MS;
  clearTimeout(soundClearTimer);
  if (!playing && !recent) {
    line.classList.add("is-empty");
    return;
  }
  line.textContent = playing
    ? soundText(d.sound, d.sound_announcement, d.mode === "meme")
    : soundText(last.file, last.announcement, last.meme);
  line.classList.remove("is-empty");
  if (!playing) {
    soundClearTimer = setTimeout(() => line.classList.add("is-empty"),
                                 SOUND_LINE_MIN_MS - last.age * 1000);
  }
}

async function refreshStatus() {
  const seq = ++statusRequestSeq;

  const sentAt = Date.now();
  const result = await apiGet("/api/status");
  const sampledAt = (sentAt + Date.now()) / 2;
  if (!result.ok) {
    setConnDot(false);
    return;
  }
  setConnDot(true);
  if (seq < statusPaintedSeq) return;
  statusPaintedSeq = seq;
  const d = result.data;
  d.sampledAt = sampledAt;
  setGoingDown(d.going_down || (d.updating ? "update" : null), sentAt);

  document.getElementById("npMode").textContent = modeLabel(d.mode);

  const fileTitle = (d.current_track || "").replace(/\.[a-z0-9]{2,5}$/i, "");
  setNowPlayingText(d.track_title || fileTitle || "\u2014", d.track_artist || "");
  applySoundLine(d);
  applyTrackProgress(d);
  applyTrackMedia(d);

  const playingKey = d.mode === "music" ? (d.track_key || null) : null;
  if (playingKey !== recentPlayingKey) {
    recentPlayingKey = playingKey;
    refreshRecent();
    refreshUpnext();
    paintLikeButton();
  }

  const nextSignature = JSON.stringify(d.next_track || null);
  if (nextSignature !== upnextSignature) {
    upnextSignature = nextSignature;
    refreshUpnext();
  }

  if (volumePending && (Math.abs(Number(d.volume) - volumePending.value) <= 1 ||
                        Date.now() > volumePending.until)) {
    volumePending = null;
  }
  if (!userIsDraggingVolume && !volumePending) {
    const slider = document.getElementById("volumeSlider");
    slider.value = Math.round(d.volume);
    setVolumeFill(slider);
    document.getElementById("volumeValue").textContent = Math.round(d.volume);
    refreshAnnounceVolumeNotes();
  }

  // A guest has one button in that slot either way.
  const guest = document.body.dataset.access === "guest";
  document.getElementById("btnStart").hidden = d.mode !== "idle" && d.mode !== "stopped";
  document.getElementById("btnPause").hidden = guest && (d.mode === "idle" || d.mode === "stopped");
  document.getElementById("btnSingle").disabled = !["music", "idle", "stopped"].includes(d.mode);
  document.getElementById("btnDouble").disabled = d.mode !== "music";

  const clockBox = document.getElementById("clockStatus");

  if (d.has_rtc) {
    clockBox.textContent = t("clock.rtc_ok");
    clockBox.dataset.state = "ok";
  } else if (d.clock_ready) {
    clockBox.textContent = t("clock.rtc_fallback");
    clockBox.dataset.state = "warn";
  } else {
    clockBox.textContent = t("clock.rtc_waiting");
    clockBox.dataset.state = "pending";
  }

  syncPiClock(d);

  const badge = document.getElementById("speakerBadge");
  badge.textContent = d.speaker_connected ? t("speaker.connected") : t("speaker.not_connected");
  badge.classList.toggle("connected", d.speaker_connected);
  document.getElementById("speakerMacDisplay").textContent = d.speaker_mac || "—";

  // Connected, the button takes the link back: that is what cures a silent speaker.
  const connectBtn = document.getElementById("btnSpeakerConnect");
  const connectKey = d.speaker_connected === true ? "speaker.reconnect" : "speaker.connect";
  connectBtn.dataset.i18n = connectKey;
  connectBtn.textContent = t(connectKey);

  lastKnownSpeakerMac = d.speaker_mac || "";

  const audioLine = document.getElementById("audioOutputLine");
  const output = d.audio_output || {};

  const broken = output.server === false || (!output.sink && !output.missing);
  if (output.missing) {
    audioLine.textContent = t("audioout.missing_" + output.output);
    audioLine.classList.add("warning");
  } else if (output.sink) {
    audioLine.textContent = t("audio.output_name", { name: output.sink });
    audioLine.classList.remove("warning");
  } else if (output.server === false) {
    audioLine.textContent = t("audio.no_server");
    audioLine.classList.add("warning");
  } else {
    audioLine.textContent = t("audio.no_sink");
    audioLine.classList.add("warning");
  }

  document.getElementById("audioRepairRow").hidden = !broken;

  document.getElementById("sshToggle").checked = !!d.ssh_active;
  if (restartPending !== !!d.restart_pending || restartDirect !== !!d.restart_direct) {
    restartPending = !!d.restart_pending;
    restartDirect = !!d.restart_direct;
    paintRestartAfterSong();
  }

  const flicBadge = document.getElementById("flicBadge");
  flicBadge.textContent = d.flic_active ? t("system.flic_active") : t("system.flic_not_used");
  flicBadge.classList.toggle("connected", d.flic_active);
}

async function watchStatusChanges() {
  const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  let since = -1;
  for (;;) {
    const askedAt = Date.now();
    let answer = null;
    try {
      const res = await fetch("/api/status/wait?since=" + since, { cache: "no-store" });
      if (res.ok) answer = await res.json();
    } catch (e) {  }
    if (answer && answer.ok && answer.data && answer.data.going_down) {
      setGoingDown(answer.data.going_down);
      await pause(5000);
      continue;
    }
    const version = answer && answer.ok && answer.data ? answer.data.version : undefined;

    if (typeof version !== "number") {
      await pause(5000);
      continue;
    }
    if (version !== since) {
      if (since !== -1) refreshStatus();
      since = version;
    } else if (Date.now() - askedAt < 250) {
      await pause(2000);
    }
  }
}
watchStatusChanges();

let trackClock = null;

function formatClock(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = h > 0 ? String(m).padStart(2, "0") : String(m);
  return (h > 0 ? h + ":" : "") + mm + ":" + String(s).padStart(2, "0");
}

function applyTrackProgress(d) {
  trackClock = {
    mode: d.mode,
    position: Number(d.position) || 0,
    duration: Number(d.duration) || 0,
    paused: !!d.paused,
    at: d.sampledAt || Date.now(),
  };
  paintTrackProgress();

  const btn = document.getElementById("btnPause");
  const skippable = d.mode === "meme" || /^(custom:|button_announce:)/.test(d.mode || "");
  const playing = d.mode === "music" && !d.paused;
  btn.dataset.action = skippable ? "skip" : "pause";
  btn.dataset.icon = skippable ? "skip" : (playing ? "pause" : "play");
  btn.dataset.i18n = skippable ? "home.skip"
    : (playing ? "home.pause" : (d.mode === "music" ? "home.resume" : "home.play"));
  btn.textContent = t(btn.dataset.i18n);
  applyLoopAndTimers(d);
  applyNowMeta(d);
  applyNotices(d);
  applyGuestCredits(d);
  applyMute(d);
  applyFallback(d);
}

let guestQuota = null;

function applyMute(d) {
  const btn = document.getElementById("btnMute");
  const muted = !!d.muted;
  btn.setAttribute("aria-pressed", muted ? "true" : "false");
  btn.dataset.icon = muted ? "volume-x" : "volume-low";
  const key = muted ? "home.unmute" : "home.mute";
  btn.dataset.i18nAriaLabel = key;
  btn.setAttribute("aria-label", t(key));
  btn.dataset.i18nTitle = key;
  btn.title = t(key);
  btn.closest(".volume-row").classList.toggle("is-muted", muted);
}
document.getElementById("btnMute").addEventListener("click", async () => {
  if (document.body.dataset.access === "guest") return;
  const r = await apiPost("/api/mute", { on: "toggle" });
  if (!r.ok) showToolError(t("common.failed"), r);
  else applyMute({ muted: r.data && r.data.muted });
});

function applyGuestCredits(d) {
  const line = document.getElementById("guestCredits");
  const q = d.quota;
  guestQuota = q || null;
  line.hidden = !q;
  paintCosts();
  if (!q) return;
  line.textContent = t("quota.line", { n: q.tokens, max: q.max });
  line.toggleAttribute("data-low", q.tokens < 2);
}

function paintCost(el) {
  const q = guestQuota;
  const cost = q && q.costs ? Number(q.costs[el.dataset.costAction]) || 0 : 0;
  if (!cost) {
    el.removeAttribute("data-cost");
    el.removeAttribute("data-cost-short");
    el.removeAttribute("aria-description");
    return;
  }
  el.dataset.cost = String(cost);
  el.toggleAttribute("data-cost-short", cost > q.tokens);
  el.setAttribute("aria-description", t("quota.cost_badge", { n: cost }));
}
function paintCosts() {
  document.querySelectorAll("[data-cost-action]").forEach(paintCost);
}

let fallbackAsked = 0;
let fallbackSignature = "";
async function applyFallback(d) {
  const box = document.getElementById("npFallback");
  const ao = d.audio_output || {};
  const planned = ao.output || "bluetooth";
  const missing = ao.missing || (planned === "bluetooth" && d.speaker_mac && d.speaker_connected === false);
  if (!missing && !d.output_override) {
    box.hidden = true;
    fallbackSignature = "";
    return;
  }
  if (Date.now() - fallbackAsked < 15000 && fallbackSignature) return;
  fallbackAsked = Date.now();
  const r = await apiGet("/api/audio/fallback");
  if (!r.ok || !r.data) return;
  const state = r.data;
  const buttons = [];
  if (state.override) {
    const back = document.createElement("button");
    back.type = "button";
    back.className = "btn btn-small";
    back.dataset.icon = "restart";
    back.textContent = t("fallback.back", { output: t("audioout." + state.planned) });
    back.addEventListener("click", () => chooseFallback(null));
    buttons.push(back);
  } else if (state.missing) {
    state.outputs.forEach((kind) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn btn-small";
      btn.dataset.icon = "speaker";
      btn.dataset.costAction = "output";
      paintCost(btn);
      btn.textContent = t("fallback.use", { output: t("audioout." + kind) });
      btn.addEventListener("click", () => chooseFallback(kind));
      buttons.push(btn);
    });
  }
  const signature = JSON.stringify([state.override, state.missing, state.outputs]);
  if (signature !== fallbackSignature) {
    fallbackSignature = signature;
    box.replaceChildren(...buttons);
  }
  box.hidden = !buttons.length;
}

async function chooseFallback(kind) {
  const r = await apiPost("/api/audio/fallback", { output: kind });
  if (!r.ok) showToolError(t("common.failed"), r);
  fallbackSignature = "";
  fallbackAsked = 0;
  refreshStatus();
}

function applyNowMeta(d) {
  const next = document.getElementById("npNext");
  if (d.next_track) {
    const n = d.next_track;
    const label = n.title ? n.title + (n.artist ? " \u2014 " + n.artist : "") : n.name;
    next.textContent = t("np.next", { t: label });
    next.title = label;
  } else {
    next.textContent = d.mode === "music" && !d.music_loop ? t("np.next_end") : "";
    next.title = "";
  }
}

function piMinutes(d) {
  const m = /\s(\d{2}):(\d{2})/.exec(d.system_time || "");
  return m ? Number(m[1]) * 60 + Number(m[2]) : null;
}
const pad2 = (n) => String(n).padStart(2, "0");

let noticesSignature = "";
function controllerKindLabel(kind, address) {
  if (kind === "usb") return t("btctl.kind_usb");
  if (kind === "builtin") return t("btctl.kind_builtin");
  return address || "";
}

function applyNotices(d) {
  const items = [];
  const add = (level, icon, key, vars) => items.push({ level, icon, text: t(key, vars) });
  if (d.mode === "idle") {
    const mode = d.music_start_mode;
    if (mode === "scheduled" && d.music_start_time) {
      add("info", "clock", d.music_started_today ? "notice.start_tomorrow" : "notice.start_scheduled",
          { time: d.music_start_time });
    } else if (mode === "bluetooth" && !d.speaker_connected) {
      add("info", "bluetooth", "notice.start_bluetooth");
    } else {
      add("info", "play", "notice.start_action");
    }
  } else if (d.mode === "stopped") {
    add("info", "play", "notice.stopped");
  }
  if (d.schedule) {
    add("info", "calendar", "notice.schedule", { name: d.schedule.name, time: d.schedule.until });
  } else if (["idle", "stopped"].includes(d.mode) && d.schedule_next) {
    add("info", "calendar", "notice.schedule_next",
        { name: d.schedule_next.name, when: scheduleNextWhen(d) });
  }
  paintScheduleRunning(d.schedule ? d.schedule.id : null);
  if (d.restart_pending) add("info", "restart", "notice.restart");
  const now = piMinutes(d);
  if (now !== null && typeof d.cutoff_hour === "number" && !["shutting_down"].includes(d.mode)) {
    const cutoff = d.cutoff_hour * 60 + (d.cutoff_minute || 0);
    const inMin = (cutoff - now + 1440) % 1440;
    if (inMin > 0 && inMin <= 60) {
      add("info", "power", "notice.cutoff_soon", { time: pad2(d.cutoff_hour) + ":" + pad2(d.cutoff_minute || 0) });
    }
  }
  const ao = d.audio_output || {};
  if (ao.missing) {
    add("warn", "speaker", "notice.output_missing", { output: t("audioout." + (ao.output || "usb")) });
  } else if (ao.server === false) {
    add("warn", "alert", "notice.no_audio_server");
  } else if (!d.output_override && (!ao.output || ao.output === "bluetooth")) {
    if (!d.speaker_mac) add("warn", "bluetooth", "notice.no_speaker");
    else if (d.speaker_connected === false) add("warn", "bluetooth", "notice.speaker_off");
  }
  if (d.speaker_connected && d.speaker_controller && d.speaker_expected &&
      d.speaker_controller !== d.speaker_expected) {
    add("warn", "bluetooth", "notice.speaker_other_controller", {
      found: controllerKindLabel(d.speaker_controller_kind, d.speaker_controller),
      expected: controllerKindLabel(d.speaker_expected_kind, d.speaker_expected),
    });
  }
  if (d.output_override) add("info", "speaker", "notice.override", { output: t("audioout." + d.output_override) });
  if (d.clock_ready === false) add("warn", "clock", "notice.clock");
  if (d.track_count === 0) add("warn", "music", "notice.no_tracks");
  if ((d.consecutive_play_errors || 0) >= 3) add("warn", "alert", "notice.errors", { n: d.consecutive_play_errors });

  const signature = JSON.stringify(items);
  if (signature === noticesSignature) return;
  noticesSignature = signature;
  const list = document.getElementById("npNotices");
  list.replaceChildren(...items.map((item) => {
    const li = document.createElement("li");
    li.dataset.level = item.level;
    li.dataset.icon = item.icon;
    li.textContent = item.text;
    return li;
  }));
  list.hidden = !items.length;
}
window.LANG_CHANGE_LISTENERS.push(() => {
  noticesSignature = "";
  if (playerStatus) {
    applyNowMeta(playerStatus);
    applyNotices(playerStatus);
  }
});

let playerStatus = null;

let timerEnds = { resume: null, sleep: null };
let timerTick = null;

/* Previous follows the daemon's own threshold (previous_restart_sec) without waiting for a status. */
let previousAction = null;

function paintPrevious() {
  const btn = document.getElementById("btnPrev");
  const position = currentTrackPosition();
  const threshold = (playerStatus && playerStatus.previous_restart_sec) || 5;
  const action = position !== null && position > threshold ? "restart" : "previous";
  if (action === previousAction) return;
  previousAction = action;
  btn.dataset.icon = action === "restart" ? "restart" : "prev";
  btn.dataset.i18n = action === "restart" ? "home.restart_track" : "home.previous";
  btn.textContent = t(btn.dataset.i18n);
}

function applyLoopAndTimers(d) {
  playerStatus = d;
  const loop = ["track", "album"].includes(d.loop_mode) ? d.loop_mode : "off";
  const btn = document.getElementById("btnLoop");
  btn.dataset.loop = loop;
  btn.setAttribute("aria-pressed", loop === "off" ? "false" : "true");
  const label = btn.querySelector("span");
  label.dataset.i18n = "home.loop_" + loop;
  label.textContent = t(label.dataset.i18n);
  document.getElementById("btnPrev").disabled = d.mode !== "music";
  paintPrevious();

  const timers = d.timers || {};
  const at = d.sampledAt || Date.now();
  ["resume", "sleep"].forEach((name) => {
    const left = timers[name];
    timerEnds[name] = typeof left === "number" ? at + left * 1000 : null;
  });
  paintTimers();
  const running = timerEnds.resume || timerEnds.sleep;
  if (running && !timerTick) timerTick = setInterval(paintTimers, 1000);
  if (!running && timerTick) {
    clearInterval(timerTick);
    timerTick = null;
  }
}

function formatLeft(ms) {
  const s = Math.max(0, Math.round(ms / 1000));
  return s >= 60 ? Math.ceil(s / 60) + " min" : s + " s";
}

function paintTimers() {
  const now = Date.now();
  const parts = [];
  if (timerEnds.resume) parts.push(t("timer.resume_in", { t: formatLeft(timerEnds.resume - now) }));
  if (timerEnds.sleep) parts.push(t("timer.sleep_in", { t: formatLeft(timerEnds.sleep - now) }));
  const line = document.getElementById("npTimers");
  line.textContent = parts.join(" \u00b7 ");
  line.hidden = !parts.length;
}

document.getElementById("btnPrev").addEventListener("click", async () => {
  const btn = document.getElementById("btnPrev");
  btn.disabled = true;
  const result = await apiPost("/api/action/previous_track");
  btn.disabled = false;
  if (!result.ok) showToolError(t("home.transport_failed"), result);
  refreshStatus();
});

document.getElementById("btnLoop").addEventListener("click", async () => {
  const result = await apiPost("/api/loop", { mode: "cycle" });
  if (!result.ok) showToolError(t("home.transport_failed"), result);
  refreshStatus();
});

function timerSymbol(name, small) {
  const mark = document.createElement("span");
  mark.className = "timer-symbol" + (small ? " timer-symbol-small" : "");
  mark.dataset.icon = name;
  mark.setAttribute("aria-hidden", "true");
  return mark;
}

function timerChoiceBody(groups, intro) {
  const wrap = document.createElement("div");
  wrap.className = "timer-groups";

  groups.forEach((group) => {
    const box = document.createElement("div");
    box.className = "timer-group";
    const head = document.createElement("div");
    head.className = "timer-group-head";
    const title = document.createElement("span");
    title.className = "timer-group-title";
    title.textContent = group.title;
    head.append(timerSymbol(group.icon, false), title);
    box.appendChild(head);
    group.options.forEach((option) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn timer-option" + (option.quiet ? " timer-option-cancel" : "");
      btn.textContent = option.label;
      btn.addEventListener("click", () => closeModal(option.value));
      box.appendChild(btn);
    });
    wrap.appendChild(box);
  });

  // Drawn icons, not the unicode pause/moon: many phone fonts have no glyph.
  const legend = document.createElement("div");
  legend.className = "timer-legend";
  [["pause", t("timer.legend_pause")], ["moon", t("timer.legend_sleep")]].forEach(([icon, text]) => {
    const line = document.createElement("p");
    line.className = "timer-legend-line";
    const label = document.createElement("span");
    label.textContent = text;
    line.append(timerSymbol(icon, true), label);
    legend.appendChild(line);
  });
  wrap.appendChild(legend);

  if (intro) {
    const line = document.createElement("p");
    line.className = "timer-intro";
    line.textContent = intro;
    wrap.prepend(line);
  }
  return wrap;
}

document.getElementById("btnTimer").addEventListener("click", async () => {
  const d = playerStatus || {};
  const durations = Array.isArray(d.pause_durations) && d.pause_durations.length
    ? d.pause_durations : [5, 15, 30, 60];
  const sleeps = Array.isArray(d.sleep_durations) && d.sleep_durations.length
    ? d.sleep_durations : [30, 60, 90, 120];

  const pauseOptions = durations.map((n) => ({ value: "pause:" + n, label: t("timer.minutes", { n }) }));
  if (timerEnds.resume) {
    pauseOptions.push({ value: "resume", label: t("timer.pause_cancel"), quiet: true });
  }
  const sleepOptions = sleeps.map((n) => ({ value: "sleep:" + n, label: t("timer.minutes", { n }) }));
  if (timerEnds.sleep) {
    sleepOptions.push({ value: "sleep_off", label: t("timer.sleep_cancel"), quiet: true });
  }

  const choice = await openModal({
    title: t("timer.title"),
    bodyNode: timerChoiceBody([
      { icon: "pause", title: t("timer.group_pause"), options: pauseOptions },
      { icon: "moon", title: t("timer.group_sleep"), options: sleepOptions },
    ], t("timer.body")),
    actions: false,
  });
  if (!choice) return;
  let result;
  if (choice.startsWith("pause:")) {
    result = await apiPost("/api/action/timed_pause", { minutes: Number(choice.slice(6)) });
  } else if (choice.startsWith("sleep:")) {
    result = await apiPost("/api/action/sleep_timer", { on: true, minutes: Number(choice.slice(6)) });
  } else if (choice === "resume") {
    result = await apiPost("/api/action/toggle_pause");
  } else {
    result = await apiPost("/api/action/sleep_timer", { on: false });
  }
  if (!result.ok) showToolError(t("home.transport_failed"), result);
  refreshStatus();
});

function currentTrackPosition() {
  if (!trackClock || !trackClock.duration || trackClock.mode !== "music") return null;
  let position = trackClock.position;
  if (!trackClock.paused) position += (Date.now() - trackClock.at) / 1000;
  return Math.min(Math.max(position, 0), trackClock.duration);
}

function paintTrackProgress() {
  const box = document.getElementById("trackProgress");
  const position = currentTrackPosition();
  if (position === null) {
    box.classList.add("is-idle");
    if (!trackClock || ["idle", "stopped"].includes(trackClock.mode)) {
      document.getElementById("trackProgressBar").style.width = "0%";
      document.getElementById("trackPosition").textContent = formatClock(0);
      document.getElementById("trackDuration").textContent = formatClock(0);
    }
    return;
  }
  box.classList.remove("is-idle");

  document.getElementById("trackProgressBar").style.width =
    ((position / trackClock.duration) * 100).toFixed(3) + "%";
  document.getElementById("trackPosition").textContent = formatClock(position);
  document.getElementById("trackDuration").textContent = formatClock(trackClock.duration);
}

const nextFrame = window.requestAnimationFrame
  ? (fn) => window.requestAnimationFrame(fn)
  : (fn) => setTimeout(fn, 100);
function progressFrame() {
  paintTrackProgress();
  paintPrevious();
  paintLyrics(false);
  nextFrame(progressFrame);
}
nextFrame(progressFrame);

const LYRICS_OPEN_KEY = "rukebox_lyrics_open";

const LYRICS_LEAD_SEC = 0.25;

const LYRICS_MANUAL_SCROLL_MS = 5000;

let trackMediaKey = null;
let lyricsData = null;
let lyricsActive = -1;
let lyricsManualUntil = 0;
let lyricsOpen = false;
try { lyricsOpen = localStorage.getItem(LYRICS_OPEN_KEY) === "1"; } catch (e) {  }

function applyTrackMedia(d) {
  const key = d.track_key && !["idle", "stopped"].includes(d.mode) ? d.track_key : null;
  if (key === trackMediaKey) return;
  trackMediaKey = key;

  const coverBox = document.getElementById("npCover");
  const img = document.getElementById("npCoverImg");
  const showEmpty = () => {
    img.hidden = true;
    coverBox.classList.add("is-empty");
  };
  if (key) {
    img.onload = () => {
      if (trackMediaKey !== key) return;
      img.hidden = false;
      coverBox.classList.remove("is-empty");
      img.classList.remove("np-enter");
      void img.offsetWidth;
      img.classList.add("np-enter");
    };
    img.onerror = () => { if (trackMediaKey === key) showEmpty(); };
    img.src = "/api/now/cover?k=" + encodeURIComponent(key);
  } else {
    img.removeAttribute("src");
    showEmpty();
  }

  lyricsData = null;
  renderLyrics();
  if (key) loadLyrics(key);
}

async function loadLyrics(key) {
  const result = await apiGet("/api/now/lyrics?k=" + encodeURIComponent(key));

  if (key !== trackMediaKey) return;
  lyricsData = result.ok ? result.data : null;
  renderLyrics();
}

function renderLyrics() {
  const btn = document.getElementById("btnLyrics");
  const panel = document.getElementById("lyricsPanel");
  const list = document.getElementById("lyricsLines");
  const plain = document.getElementById("lyricsPlain");
  const scroll = document.getElementById("lyricsScroll");
  const has = !!lyricsData;
  const open = has && lyricsOpen;
  btn.hidden = !has;
  btn.setAttribute("aria-pressed", open ? "true" : "false");
  btn.classList.toggle("is-on", open);
  panel.hidden = !open;
  if (!open && panel.classList.contains("is-fullscreen")) setLyricsFullscreen(false);
  list.innerHTML = "";
  plain.textContent = "";
  lyricsActive = -1;
  if (!has) return;

  const synced = !!lyricsData.synced;
  document.getElementById("lyricsSource").textContent =
    t(synced ? "lyrics.synced" : "lyrics.plain");
  scroll.classList.toggle("is-plain", !synced);
  list.hidden = !synced;
  plain.hidden = synced;
  if (synced) {
    lyricsData.lines.forEach((line) => {
      const li = document.createElement("li");

      li.textContent = line.text || "♪";
      list.appendChild(li);
    });
    scroll.scrollTop = 0;
    paintLyrics(true);
  } else {
    plain.textContent = lyricsData.text;
  }
}

function paintLyrics(instant) {
  if (!lyricsData || !lyricsData.synced) return;
  const panel = document.getElementById("lyricsPanel");
  if (panel.hidden) return;
  const position = currentTrackPosition();
  if (position === null) return;
  const lines = lyricsData.lines;
  let index = -1;
  for (let i = 0; i < lines.length && lines[i].t <= position + LYRICS_LEAD_SEC; i++) index = i;
  if (index === lyricsActive) return;
  lyricsActive = index;

  const items = document.getElementById("lyricsLines").children;
  for (let i = 0; i < items.length; i++) {
    items[i].classList.toggle("active", i === index);
    items[i].classList.toggle("past", i < index);
  }
  if (index < 0 || Date.now() < lyricsManualUntil) return;

  const scroll = document.getElementById("lyricsScroll");
  const li = items[index];
  const top = li.offsetTop - scroll.clientHeight / 2 + li.offsetHeight / 2;
  const reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  scroll.scrollTo({ top: Math.max(0, top), behavior: instant || reduced ? "auto" : "smooth" });
}

["wheel", "touchstart", "keydown"].forEach((type) => {
  document.getElementById("lyricsScroll").addEventListener(type, () => {
    lyricsManualUntil = Date.now() + LYRICS_MANUAL_SCROLL_MS;
  }, { passive: true });
});

let wakeLock = null;
let wakeVideo = null;

async function keepScreenAwake() {
  try {
    if (navigator.wakeLock && window.isSecureContext) {
      wakeLock = await navigator.wakeLock.request("screen");
      return;
    }
  } catch (e) {  }
  if (!wakeVideo) {
    wakeVideo = document.createElement("video");
    wakeVideo.src = "/keepawake.mp4";
    wakeVideo.muted = true;
    wakeVideo.loop = true;
    wakeVideo.playsInline = true;
    wakeVideo.setAttribute("muted", "");
    wakeVideo.setAttribute("playsinline", "");
    wakeVideo.setAttribute("aria-hidden", "true");
    wakeVideo.className = "keep-awake";
    document.body.appendChild(wakeVideo);
  }
  wakeVideo.play().catch(() => {  });
}

function letScreenSleep() {
  if (wakeLock) {
    wakeLock.release().catch(() => {});
    wakeLock = null;
  }
  if (wakeVideo) wakeVideo.pause();
}

document.addEventListener("visibilitychange", () => {
  if (!document.hidden && document.getElementById("lyricsPanel").classList.contains("is-fullscreen")) {
    keepScreenAwake();
  }
});

function setLyricsFullscreen(on) {
  const panel = document.getElementById("lyricsPanel");
  const btn = document.getElementById("btnLyricsFullscreen");
  panel.classList.toggle("is-fullscreen", on);
  document.body.classList.toggle("lyrics-fullscreen", on);
  btn.dataset.icon = on ? "shrink" : "expand";
  btn.setAttribute("aria-pressed", on ? "true" : "false");
  const key = on ? "lyrics.exit_fullscreen" : "lyrics.fullscreen";
  btn.dataset.i18nAriaLabel = key;
  btn.dataset.i18nTitle = key;
  btn.setAttribute("aria-label", t(key));
  btn.title = t(key);
  document.getElementById("lyricsTrack").textContent =
    on ? document.getElementById("npTrack").textContent : "";
  const root = document.documentElement;
  try {
    if (on && root.requestFullscreen && !document.fullscreenElement) {
      root.requestFullscreen().catch(() => {  });
    } else if (!on && document.fullscreenElement && document.exitFullscreen) {
      document.exitFullscreen().catch(() => {});
    }
  } catch (e) {  }
  if (on) keepScreenAwake(); else letScreenSleep();
  lyricsActive = -1;
  lyricsManualUntil = 0;
  paintLyrics(true);
}

document.getElementById("btnLyricsFullscreen").addEventListener("click", () => {
  setLyricsFullscreen(!document.getElementById("lyricsPanel").classList.contains("is-fullscreen"));
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && document.getElementById("lyricsPanel").classList.contains("is-fullscreen")) {
    setLyricsFullscreen(false);
  }
});

document.addEventListener("fullscreenchange", () => {
  if (!document.fullscreenElement && document.getElementById("lyricsPanel").classList.contains("is-fullscreen")) {
    setLyricsFullscreen(false);
  }
});

document.getElementById("btnLyrics").addEventListener("click", () => {
  lyricsOpen = !lyricsOpen;
  try { localStorage.setItem(LYRICS_OPEN_KEY, lyricsOpen ? "1" : "0"); } catch (e) {  }
  lyricsManualUntil = 0;
  renderLyrics();

  const panel = document.getElementById("lyricsPanel");
  if (lyricsOpen && !panel.hidden) {
    const reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    panel.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" });
  }
});

window.LANG_CHANGE_LISTENERS.push(() => {
  if (lyricsData) {
    document.getElementById("lyricsSource").textContent =
      t(lyricsData.synced ? "lyrics.synced" : "lyrics.plain");
  }
});

document.getElementById("btnPause").addEventListener("click", async () => {
  const btn = document.getElementById("btnPause");
  btn.disabled = true;
  const result = await apiPost(btn.dataset.action === "skip" ? "/api/action/skip_sound" : "/api/action/toggle_pause");
  btn.disabled = false;
  if (!result.ok) showToolError(t("home.transport_failed"), result);
  refreshStatus();
});

document.getElementById("btnNext").addEventListener("click", async () => {
  const btn = document.getElementById("btnNext");
  btn.disabled = true;

  const result = await apiPost("/api/action/next_track");
  btn.disabled = false;
  if (!result.ok) showToolError(t("home.transport_failed"), result);
  refreshStatus();
});

refreshStatus();
refreshEvery(refreshStatus, 4000);

const volumeSlider = document.getElementById("volumeSlider");
let volumeDebounce = null;
setVolumeFill(volumeSlider);

volumeSlider.addEventListener("input", () => {
  userIsDraggingVolume = true;
  volumePending = { value: Number(volumeSlider.value), until: Date.now() + VOLUME_HOLD_MS };
  document.getElementById("volumeValue").textContent = volumeSlider.value;
  setVolumeFill(volumeSlider);
  refreshAnnounceVolumeNotes();
  clearTimeout(volumeDebounce);
  volumeDebounce = setTimeout(() => {
    apiPost("/api/volume", { value: Number(volumeSlider.value) });
  }, 150);
});
volumeSlider.addEventListener("change", () => {
  userIsDraggingVolume = false;
});

document.getElementById("btnSingle").addEventListener("click", () => apiPost("/api/action/single_click"));

document.getElementById("btnDouble").addEventListener("click", async () => {
  const choices = [];
  customAnnouncementsCache.forEach((item) => {
    choices.push({
      value: "custom:" + item.id,
      label: item.name,
    });
  });
  choices.push({ value: "cutoff", label: t("source.cutoff") },
               { value: "meme", label: t("source.meme") });

  const source = await showChoice(t("home.announce_which"), choices,
                                  t("home.announcement"));
  if (!source) return;

  const result = await apiPost("/api/action/announce", { source });
  if (result.ok && result.data && result.data.skipped) {
    showToast(t("chance.not_this_time"), chanceLabel(result.data.chance));
    return;
  }
  if (result.ok) return;
  const messages = {
    not_playing_music: t("home.announce_not_playing"),
    no_announcement: t("home.announce_empty"),
    unknown_source: t("home.announce_unknown"),
  };
  showToast(t("common.failed"), messages[result.error] || errorLabel(result.error), { error: true });
});
document.getElementById("btnStart").addEventListener("click", () => apiPost("/api/action/start_music"));

document.getElementById("btnLongPress").addEventListener("click", async () => {
  const choice = await showChoice(t("home.stop_body"), [
    { value: "standby", label: t("home.standby") },
    { value: "poweroff", label: t("home.poweroff"), danger: true },
  ], t("home.stop_title"));
  if (choice === "standby") {
    const r = await apiPost("/api/action/standby");
    if (!r.ok) showToolError(t("common.failed"), r);
    else showToast(t("home.standby_done"));
  } else if (choice === "poweroff") {
    apiPost("/api/action/poweroff");
  }
});

document.getElementById("btnRescan").addEventListener("click", async () => {
  const r = await apiPost("/api/rescan_music");
  if (r.ok) showToast(t("system.rescan_scheduled")); else showError(r.error);
});

const btClockMac = document.getElementById("btClockMac");

function macDigits(value) {
  return String(value || "").toUpperCase().replace(/[^0-9A-F]/g, "").slice(0, 12);
}

function formatMac(value) {
  return macDigits(value).replace(/(..)(?=.)/g, "$1:");
}

function isCompleteMac(value) {
  return macDigits(value).length === 12;
}

btClockMac.addEventListener("input", () => {
  const raw = btClockMac.value;

  const digits = macDigits(raw);
  const wantedSeparator = /[:\-\s]$/.test(raw) && digits.length % 2 === 0 && digits.length < 12;
  const caret = btClockMac.selectionStart == null ? raw.length : btClockMac.selectionStart;
  const digitsBefore = macDigits(raw.slice(0, caret)).length;

  btClockMac.value = formatMac(raw) + (wantedSeparator ? ":" : "");

  const position = Math.min(digitsBefore + Math.floor(digitsBefore / 2), btClockMac.value.length);
  try {
    btClockMac.setSelectionRange(position, position);
  } catch (e) {
  }
});


// Only the changed fields are sent, so a stale tab cannot overwrite a setting.
let settingsBaseline = {};

const DURATION_MAX_MIN = 600;

function durationValues(el) {
  return String(el.value || "").split(/[,;]/)
    .map((part) => part.trim())
    .filter((part) => part.length > 0);
}

function saveDurations(el, values) {
  el.value = values.join(",");
}

function addDuration(el) {
  const input = el.querySelector(".duration-new");
  const typed = input.value.trim();
  if (!typed) return;
  const value = Math.round(Number(typed));
  if (!Number.isFinite(value) || value < 1 || value > DURATION_MAX_MIN) {
    showError("invalid_value");
    return;
  }
  const values = durationValues(el);
  input.value = "";
  if (values.includes(String(value))) {
    showToast(t("settings.duration_exists", { n: value }));
    return;
  }
  values.push(String(value));
  values.sort((a, b) => Number(a) - Number(b));
  saveDurations(el, values);
  showToast(t("settings.duration_added", { n: value }));
  input.focus();
}

// The row stays the height of its neighbours; this is where the values can be read.
function durationList(el) {
  const values = durationValues(el);
  const list = document.createElement("div");
  list.className = "duration-list";
  if (!values.length) {
    const empty = document.createElement("p");
    empty.className = "hint";
    empty.textContent = t("settings.duration_none");
    list.appendChild(empty);
    return list;
  }
  values.forEach((value) => {
    const row = document.createElement("div");
    row.className = "duration-row";
    const label = document.createElement("span");
    label.textContent = t("settings.duration_min", { n: value });
    const off = document.createElement("button");
    off.type = "button";
    // A trash bin, in red: a cross would read as another way out of the dialog.
    off.className = "btn btn-icon duration-row-x btn-danger-outline";
    off.dataset.icon = "trash";
    off.title = t("settings.duration_remove", { n: value });
    off.setAttribute("aria-label", off.title);
    off.disabled = values.length < 2;
    off.addEventListener("click", () => {
      const kept = durationValues(el);
      if (kept.length < 2) return;
      saveDurations(el, kept.filter((one) => one !== value));
      list.replaceWith(durationList(el));
    });
    row.append(label, off);
    list.appendChild(row);
  });
  return list;
}

function openDurationList(el) {
  const label = el.closest(".field-row").querySelector("label");
  return openModal({
    title: label ? label.textContent : t("settings.duration_manage"),
    bodyNode: durationList(el),
    actions: false,
  });
}

function durationField(el) {
  el.value = "";
  const input = document.createElement("input");
  input.type = "number";
  input.className = "num-input duration-new";
  input.id = el.id + "New";
  input.min = "1";
  input.max = String(DURATION_MAX_MIN);
  input.step = "1";
  input.inputMode = "numeric";
  input.placeholder = "15";
  input.autocomplete = "off";

  const plus = document.createElement("button");
  plus.type = "button";
  plus.className = "btn btn-icon duration-add-btn";
  plus.dataset.icon = "plus";
  plus.title = t("common.add");
  plus.setAttribute("aria-label", t("common.add"));
  plus.addEventListener("click", () => addDuration(el));

  const list = document.createElement("button");
  list.type = "button";
  list.className = "btn btn-icon duration-open";
  list.dataset.icon = "list";
  list.title = t("settings.duration_manage");
  list.setAttribute("aria-label", list.title);
  list.addEventListener("click", () => openDurationList(el));

  // `change` fires on leaving the field, so a value typed then left is read before Save.
  input.addEventListener("change", () => addDuration(el));
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();  // otherwise the settings form is submitted
      addDuration(el);
    }
  });
  el.replaceChildren(input, plus, list);
}

document.querySelectorAll(".duration-field").forEach(durationField);

/* One checkbox per codec: in a dialog - the eight names in the row wrapped and doubled its height. */
const BT_CODEC_LABELS = {
  ldac: "LDAC", aptx_hd: "aptX HD", aptx: "aptX", aac: "AAC",
  sbc_xq: "SBC-XQ", sbc: "SBC", faststream: "FastStream", opus: "Opus",
};

function codecValues(el) {
  return String(el.value || "").split(/[,;]/)
    .map((part) => part.trim().toLowerCase())
    .filter((part) => part.length > 0);
}

function codecSummary(el) {
  const chosen = codecValues(el);
  return chosen.length
    ? chosen.map((codec) => BT_CODEC_LABELS[codec] || codec).join(", ")
    : t("audioout.codecs_none");
}

function paintCodecField(el) {
  const open = el.querySelector(".codec-open");
  if (open) open.textContent = codecSummary(el);
}

function codecList(el) {
  const chosen = codecValues(el);
  const list = document.createElement("div");
  list.className = "codec-list";
  Object.keys(BT_CODEC_LABELS).forEach((codec) => {
    const label = document.createElement("label");
    label.className = "codec-box";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.value = codec;
    box.checked = chosen.includes(codec);
    box.disabled = chosen.length < 2 && box.checked;
    if (box.disabled) label.classList.add("is-disabled");
    const text = document.createElement("span");
    text.textContent = BT_CODEC_LABELS[codec];
    box.addEventListener("change", () => {
      const kept = codecValues(el).filter((one) => one !== codec);
      if (box.checked) kept.push(codec);
      el.value = kept.join(",");
      paintCodecField(el);
      list.replaceWith(codecList(el));
    });
    label.append(box, text);
    list.appendChild(label);
  });
  return list;
}

function openCodecList(el) {
  const row = el.closest(".field-row");
  const label = row ? row.querySelector("label") : null;
  return openModal({
    title: label ? label.textContent : t("audioout.codecs"),
    bodyNode: codecList(el),
    actions: false,
  });
}

function codecField(el) {
  el.value = el.value || "";
  const open = document.createElement("button");
  open.type = "button";
  open.className = "btn codec-open";
  open.addEventListener("click", () => openCodecList(el));
  el.replaceChildren(open);
  paintCodecField(el);
}

document.querySelectorAll(".codec-field").forEach(codecField);

function collectFieldValue(el) {
  if (el.type === "checkbox") return el.checked ? "true" : "false";
  return el.value;
}

function timeFieldParts(el) {
  const parts = String(el.value || "").split(":");
  const hour = Number(parts[0]);
  const minute = Number(parts[1]);
  return [
    String(hour >= 0 && hour <= 23 ? hour : 0),
    String(minute >= 0 && minute <= 59 ? minute : 0),
  ];
}

function setTimeField(el, values) {
  const hour = String(Number(values[el.dataset.key]) || 0).padStart(2, "0");
  const minute = String(Number(values[el.dataset.keyMinute]) || 0).padStart(2, "0");
  el.value = hour + ":" + minute;
}

function timeFieldChanged(el) {
  const [hour, minute] = timeFieldParts(el);
  return hour !== settingsBaseline[el.dataset.key]
    || minute !== settingsBaseline[el.dataset.keyMinute];
}

function setFieldValue(el, rawValue) {
  if (el.type === "checkbox") {
    el.checked = String(rawValue).toLowerCase() === "true";
  } else {
    // Options that do not hold the stored value come back empty on the next save; keep one.
    if (el.tagName === "SELECT"
        && !Array.from(el.options).some((one) => one.value === String(rawValue))) {
      const kept = document.createElement("option");
      kept.value = String(rawValue);
      kept.textContent = String(rawValue);
      el.appendChild(kept);
    }
    el.value = rawValue;
    if (el.classList.contains("codec-field")) paintCodecField(el);
  }
}

function collectChangedSettings(container) {
  const updates = {};
  container.querySelectorAll("[data-key]").forEach((el) => {
    const key = el.dataset.key;
    if (el.dataset.keyMinute) {
      const [hour, minute] = timeFieldParts(el);
      if (hour !== settingsBaseline[key]) updates[key] = hour;
      if (minute !== settingsBaseline[el.dataset.keyMinute]) updates[el.dataset.keyMinute] = minute;
      return;
    }
    const value = collectFieldValue(el);
    if (value !== settingsBaseline[key]) updates[key] = value;
  });
  return updates;
}

async function loadSettingsIntoForm() {
  const result = await apiGet("/api/settings");
  if (!result.ok) return;
  document.querySelectorAll("[data-key]").forEach((el) => {
    const key = el.dataset.key;
    if (el.dataset.keyMinute) {
      if (key in result.data) setTimeField(el, result.data);
      return;
    }
    if (key in result.data) setFieldValue(el, result.data[key]);
  });
  settingsBaseline = { ...result.data };

  updateStartTimeVisibility();
  updateSpeakerFadeVisibility();
  updateClickSoundRows();

  if ("ACT_LED" in result.data) {
    document.getElementById("actLedToggle").checked = result.data.ACT_LED !== "off";
    if (result.data.USB_PORT_MODE) document.getElementById("usbPortMode").value = result.data.USB_PORT_MODE;
  }
}

function updateSpeakerFadeVisibility() {
  document.getElementById("speakerResumeFadeRow").hidden =
    !document.getElementById("speakerLossPause").checked;
}
document.getElementById("speakerLossPause").addEventListener("change", updateSpeakerFadeVisibility);

async function offerReboot(title) {
  showToast(title, t("system.reboot_needed"), {
    action: {
      label: t("system.reboot_now"),
      icon: "restart",
      run: async () => {
        if (!(await showConfirm(t("system.reboot_confirm")))) return;
        const r = await apiPost("/api/system/reboot");
        if (!r.ok) showToolError(t("common.failed"), r);
      },
    },
  });
}
document.getElementById("usbPortMode").addEventListener("change", async (e) => {
  const select = e.target;
  const value = select.value;
  const previous = settingsBaseline.USB_PORT_MODE || "gadget";
  const ok = await showConfirm(t(value === "host" ? "system.usb_host_confirm" : "system.usb_gadget_confirm"));
  if (!ok) {
    select.value = previous;
    return;
  }
  const r = await apiPost("/api/settings", { USB_PORT_MODE: value });
  if (!r.ok) {
    select.value = previous;
    showError(r.error);
    return;
  }
  settingsBaseline.USB_PORT_MODE = value;
  offerReboot(t(value === "host" ? "system.usb_host_done" : "system.usb_gadget_done"));
});

document.getElementById("actLedToggle").addEventListener("change", async (e) => {
  const value = e.target.checked ? "default" : "off";
  const r = await apiPost("/api/settings", { ACT_LED: value });
  if (!r.ok) {
    e.target.checked = !e.target.checked;
    showError(r.error);
    return;
  }
  settingsBaseline.ACT_LED = value;
  showToast(t(value === "off" ? "system.act_led_off" : "system.act_led_on"));
});

document.getElementById("updateAllowWeb").addEventListener("change", async (e) => {
  const value = e.target.checked ? "true" : "false";
  const r = await apiPost("/api/settings", { UPDATE_ALLOW_WEB: value });
  if (!r.ok) {
    e.target.checked = !e.target.checked;
    showError(r.error);
    return;
  }
  settingsBaseline.UPDATE_ALLOW_WEB = value;
  showToast(t(value === "true" ? "update.allow_web_on" : "update.allow_web_off"));
  checkRelease(false);
});

loadSettingsIntoForm();

async function refreshSettingsIfIdle() {
  const result = await apiGet("/api/settings");
  if (!result.ok) return;
  document.querySelectorAll("[data-key]").forEach((el) => {
    const key = el.dataset.key;
    if (el.dataset.keyMinute) {
      if (!(key in result.data)) return;
      if (!timeFieldChanged(el)) setTimeField(el, result.data);
      settingsBaseline[key] = result.data[key];
      settingsBaseline[el.dataset.keyMinute] = result.data[el.dataset.keyMinute];
      return;
    }
    if (!(key in result.data)) return;
    if (collectFieldValue(el) === settingsBaseline[key]) {
      setFieldValue(el, result.data[key]);
    }
    settingsBaseline[key] = result.data[key];
  });
  updateStartTimeVisibility();
}

refreshEvery(refreshSettingsIfIdle, 20000);

let restartPending = false;
let restartDirect = false;
function paintRestartAfterSong() {
  const btn = document.getElementById("btnRestartAfterSong");
  // Nothing playing: the daemon restarts at once, and the button says so.
  btn.dataset.i18n = restartPending ? "system.restart_cancel"
    : (restartDirect ? "system.restart_now" : "system.restart_after_song");
  btn.dataset.icon = restartDirect && !restartPending ? "restart" : "clock";
  btn.textContent = t(btn.dataset.i18n);
  btn.classList.toggle("is-on", restartPending);
}
document.getElementById("btnRestartAfterSong").addEventListener("click", async () => {
  const want = !restartPending;
  const r = await apiPost("/api/daemon/restart_after_song", { on: want });
  if (!r.ok) {
    showError(r.error);
    return;
  }
  restartPending = !!(r.data && r.data.pending);
  paintRestartAfterSong();
  showToast(t(want ? (restartPending ? "system.restart_planned" : "system.restart_now_idle")
                   : "system.restart_cancelled"));
});

async function restartDaemon() {
  const result = await apiPost("/api/daemon/restart");
  if (result.ok) showToast(t("alert.service_restarted")); else showError(result.error);
}

const SETTINGS_FORMS = ["settingsForm", "playbackForm", "volumeForm", "fadesForm", "buttonsForm", "speakerForm", "audioOutputForm", "releaseRepoForm"]
  .map((id) => document.getElementById(id))
  .filter(Boolean);

SETTINGS_FORMS.forEach((form) => {
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const updates = collectChangedSettings(form);
    if (Object.keys(updates).length === 0) {
      showToast(t("alert.no_changes"));
      return;
    }
    const result = await apiPost("/api/settings", updates);
    if (!result.ok) {
      showError(result.error);
      return;
    }
    Object.assign(settingsBaseline, updates);

    const d = result.data || {};
    if (d.restart_needed) {
      showToast(t("alert.settings_saved"), t("alert.restart_reminder"), {
        action: { label: t("settings.restart_now"), icon: "restart", run: restartDaemon },
      });
    } else if ("BT_AUDIO_CODECS" in updates) {
      showToast(t("alert.settings_applied"),
        t(d.audio_reloaded === false ? "alert.audio_later" : "alert.audio_reloaded"));
    } else if (d.applied_live === false) {
      showToast(t("alert.settings_saved_later"));
    } else {
      showToast(t("alert.settings_applied"));
    }
  });
});

document.getElementById("btnRestartDaemon").addEventListener("click", async () => {
  if (!(await showConfirm(t("confirm.restart_daemon")))) return;
  restartDaemon();
});

let folderPickerTarget = null;
let folderPickerPath = "";

function closeFolderPicker() {
  document.getElementById("folderOverlay").hidden = true;
  folderPickerTarget = null;
}

async function showFolder(path) {
  const list = document.getElementById("folderList");
  const status = document.getElementById("folderStatus");
  status.textContent = "";
  const result = await apiGet("/api/browse" + (path ? "?path=" + encodeURIComponent(path) : ""));
  if (!result.ok) {
    if (path) return showFolder("");
    status.textContent = errorLabel(result.error);
    return;
  }
  const data = result.data;
  folderPickerPath = data.path;
  document.getElementById("folderPath").textContent = data.path;
  document.getElementById("folderUp").disabled = !data.parent;
  document.getElementById("folderUp").dataset.parent = data.parent || "";

  const roots = document.getElementById("folderRoots");
  roots.innerHTML = "";
  (data.roots || []).forEach((root) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "breadcrumb-item";
    btn.textContent = root;
    btn.classList.toggle("active", folderPickerPath === root);
    btn.addEventListener("click", () => showFolder(root));
    roots.appendChild(btn);
  });

  list.innerHTML = "";
  if (!data.dirs.length) {
    const li = document.createElement("li");
    li.textContent = t("browse.empty");
    list.appendChild(li);
    return;
  }

  data.dirs.forEach((dir) => {
    const li = document.createElement("li");
    const row = document.createElement("button");
    row.type = "button";
    row.className = "folder-row";
    const name = document.createElement("span");
    name.className = "folder-name";
    name.dataset.icon = "folder";
    name.textContent = dir.name;
    row.appendChild(name);
    if (dir.audio_files) {
      const count = document.createElement("span");
      count.className = "folder-count";
      count.dataset.icon = "music";
      count.textContent = dir.audio_files;
      count.title = t("browse.audio_count", { n: dir.audio_files });
      row.appendChild(count);
      row.setAttribute("aria-label", dir.name + " - " + t("browse.audio_count", { n: dir.audio_files }));
    }
    row.addEventListener("click", () => showFolder(dir.path));
    li.appendChild(row);
    list.appendChild(li);
  });
}

document.getElementById("btnAnnFolderBrowse").addEventListener("click", () => {
  const input = document.getElementById("annFolder");
  folderPickerTarget = input;
  document.getElementById("folderOverlay").hidden = false;
  showFolder(input.value.trim());
});

document.getElementById("folderUp").addEventListener("click", () => {
  const parent = document.getElementById("folderUp").dataset.parent;
  if (parent) showFolder(parent);
});

document.getElementById("folderChoose").addEventListener("click", () => {
  if (!folderPickerTarget) return;
  folderPickerTarget.value = folderPickerPath;

  folderPickerTarget.dispatchEvent(new Event("input", { bubbles: true }));
  folderPickerTarget.dispatchEvent(new Event("change", { bubbles: true }));
  closeFolderPicker();
});

document.getElementById("folderCancel").addEventListener("click", closeFolderPicker);

function formatHM(hour, minute) {
  return String(hour).padStart(2, "0") + ":" + String(minute).padStart(2, "0");
}

let customAnnouncementsCache = [];

function syncClickSourceOptions(customItems) {
  document.querySelectorAll(".click-source-select").forEach((select) => {
    const key = select.dataset.key;

    const current = select.value;
    select.querySelectorAll('option[data-custom="1"]').forEach((opt) => opt.remove());
    customItems.forEach((item) => {
      const opt = document.createElement("option");
      opt.value = "custom:" + item.id;
      opt.textContent = item.name;
      opt.dataset.custom = "1";
      select.appendChild(opt);
    });
    if (key && key in settingsBaseline) {
      setFieldValue(select, settingsBaseline[key]);
    } else if ([...select.options].some((o) => o.value === current)) {
      select.value = current;
    } else if (select.id === "trackOrderSource" && current !== select.value) {
      loadTrackOrder();
    }
  });
}

const CLICK_SOUND_ACTIONS = ["next", "previous", "sound"];
function updateClickSoundRows() {
  document.querySelectorAll(".click-sound-row").forEach((row) => {
    const action = document.querySelector('.click-action-select[data-click="' + row.dataset.click + '"]');
    row.hidden = action && !CLICK_SOUND_ACTIONS.includes(action.value);
  });
}
document.querySelectorAll(".click-action-select").forEach((select) => {
  select.addEventListener("change", updateClickSoundRows);
});

document.querySelectorAll(".click-test-btn").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const field = btn.dataset.click;

    if (document.getElementById(field + "ClickAction").value === "poweroff"
        && !(await showConfirm(t("home.confirm_shutdown")))) return;
    const r = await apiPost("/api/action/test_click", {
      kind: btn.dataset.kind || field,
      action: document.getElementById(field + "ClickAction").value,
      sound: document.getElementById(field + "ClickSource").value,
    });
    if (!r.ok) {
      showToast(t("common.failed"),
                r.error === "not_playing_music" ? t("click.test_needs_music") : errorLabel(r.error),
                { error: true });
    }
  });
});

let announceVolumes = {};

async function refreshAnnouncementVolumes() {
  const result = await apiGet("/api/announcement_volumes");
  if (result.ok && result.data && typeof result.data === "object") announceVolumes = result.data;
  return announceVolumes;
}

function announceVolume(key) {
  const entry = announceVolumes[key] || {};
  const volume = Number(entry.volume);
  return { on: !!entry.on, volume: Number.isFinite(volume) ? volume : null };
}

async function saveAnnounceVolume(key, on, volume) {
  const result = await apiPost("/api/announcement_volumes/" + encodeURIComponent(key),
                               { on, volume });
  if (!result.ok) {
    showError(result.error);
    return false;
  }
  announceVolumes[key] = result.data || { on, volume };
  return true;
}

function defaultAnnounceVolume() {
  const slider = document.getElementById("volumeSlider");
  const value = slider ? Number(slider.value) : NaN;
  return Number.isFinite(value) ? value : 50;
}

function announceVolumeWarning(entry, music) {
  /* 4x the music is about 12 dB: past that the music is covered. */
  if (!entry.on || entry.volume === null || !music) return "";
  if (entry.volume < music * 4) return "";
  return t("annvol.louder", { music: Math.round(music) });
}

function refreshAnnounceVolumeNotes() {
  const music = defaultAnnounceVolume();
  document.querySelectorAll(".ann-volume-note").forEach((note) => {
    const text = announceVolumeWarning(announceVolume(note.dataset.key), music);
    note.textContent = text;
    note.hidden = !text;
  });
}

let volumeControlSeq = 0;

function volumeField(labelKey, control, describedBy) {
  const row = document.createElement("div");
  row.className = "field-row";
  const text = document.createElement("div");
  text.className = "field-text";
  const label = document.createElement("label");
  label.htmlFor = control.id;
  label.textContent = t(labelKey);
  text.appendChild(label);
  if (describedBy) {
    text.appendChild(describedBy);
    label.setAttribute("aria-describedby", describedBy.id);
  }
  row.append(text, control);
  return row;
}

function volumeControl(key, options) {
  const opts = options || {};
  const current = announceVolume(key);
  const uid = "annvol" + (++volumeControlSeq);

  const toggle = document.createElement("input");
  toggle.type = "checkbox";
  toggle.id = uid + "-on";
  toggle.className = "switch-input";
  toggle.setAttribute("role", "switch");
  toggle.checked = current.on;

  const value = document.createElement("input");
  value.type = "number";
  value.id = uid + "-value";
  value.className = "num-input ann-volume-value";
  value.min = "0";
  value.max = "100";
  value.step = "1";
  value.inputMode = "numeric";
  value.value = String(current.volume === null ? defaultAnnounceVolume() : current.volume);
  value.setAttribute("aria-label", t("annvol.label"));

  let hint = null;
  if (opts.hint) {
    hint = document.createElement("p");
    hint.className = "field-desc";
    hint.id = uid + "-desc";
    hint.textContent = t("annvol.desc");
  }

  const holder = document.createElement("div");
  holder.className = "ann-volume" + (opts.inline ? " is-inline" : "");

  const note = document.createElement("p");
  note.className = "hint ann-volume-note";
  note.dataset.key = key;
  // Painted here, not left to refreshAnnounceVolumeNotes(): the holder is not in the page yet.
  const paintNote = () => {
    const text = announceVolumeWarning(announceVolume(key), defaultAnnounceVolume());
    note.textContent = text;
    note.hidden = !text;
  };

  const paint = () => {
    value.disabled = !toggle.checked;
    holder.classList.toggle("is-on", toggle.checked);
    paintNote();
  };
  const store = async () => {
    const volume = Math.max(0, Math.min(100, Math.round(Number(value.value) || 0)));
    value.value = String(volume);
    if (await saveAnnounceVolume(key, toggle.checked, volume)) paint();
    paintNote();
  };
  toggle.addEventListener("change", store);
  value.addEventListener("change", store);

  paint();
  holder.append(volumeField("annvol.own", toggle, hint), volumeField("annvol.label", value),
                note);
  return holder;
}

async function refreshAnnouncements() {
  // The list is emptied only once the answer is in: clearing it first made the card blink.
  const [result] = await Promise.all([apiGet("/api/announcements"),
                                      refreshAnnouncementVolumes()]);
  const list = document.getElementById("announcementList");
  list.innerHTML = "";

  const items = result.ok && Array.isArray(result.data) ? result.data : [];

  customAnnouncementsCache = items;
  syncClickSourceOptions(items);
  if (!items.length) {
    const li = document.createElement("li");
    li.textContent = t("announcements.none_yet");
    list.appendChild(li);
    return;
  }

  items.forEach((item) => list.appendChild(announcementRow(item)));
}

let openAnnouncementId = null;

function announcementWhen(item) {
  const trigger = item.trigger || "time";
  if (trigger === "manual") return t("announcements.on_demand_cap");
  if (trigger === "time") return formatHM(item.hour, item.minute);
  const key = trigger === "after_music" ? "when.after_music" : "when.after_boot";
  const n = Number(item.repeat_times);
  const repeat = n === 0 ? t("when.forever") : n > 1 ? t("when.times", { n }) : "";
  return t(key, { min: item.delay_min }) + (repeat ? " \u00b7 " + repeat : "");
}

function setAnnouncementOpen(id) {
  openAnnouncementId = id;
  document.querySelectorAll("#announcementList .ann-item").forEach((li) => {
    const open = li.dataset.id === id;
    li.classList.toggle("is-open", open);
    li.querySelector(".ann-head").setAttribute("aria-expanded", open ? "true" : "false");
    li.querySelector(".ann-body").hidden = !open;
  });
}

function announcementRow(item) {
  const li = document.createElement("li");
  li.className = "ann-item";
  li.dataset.id = item.id;

  if (!item.enabled) li.classList.add("is-off");
  const open = item.id === openAnnouncementId;
  if (open) li.classList.add("is-open");

  const bodyId = "ann-body-" + item.id;
  const head = document.createElement("button");
  head.type = "button";
  head.className = "ann-head";
  head.setAttribute("aria-expanded", open ? "true" : "false");
  head.setAttribute("aria-controls", bodyId);

  const text = document.createElement("span");
  text.className = "ann-text";
  const name = document.createElement("span");
  name.className = "ann-name";
  name.textContent = item.name;
  const meta = document.createElement("span");
  meta.className = "ann-meta";

  const when = announcementWhen(item);
  const chance = item.trigger !== "manual" && item.auto_chance && item.auto_chance !== "1/1"
    ? " \u00b7 " + chanceLabel(item.auto_chance) : "";
  meta.textContent = when + chance + " \u00b7 " + item.file_count + " " +
    t(item.file_count === 1 ? "common.file_one" : "common.file_other");
  if (!item.enabled) {
    const off = document.createElement("span");
    off.className = "sr-only";
    off.textContent = ", " + t("announcements.state_off");
    meta.appendChild(off);
  }
  text.append(name, meta);
  const chevron = document.createElement("span");
  chevron.className = "ann-chevron";
  chevron.dataset.icon = "chevron";
  chevron.setAttribute("aria-hidden", "true");
  head.append(text, chevron);
  head.addEventListener("click", () => {
    setAnnouncementOpen(li.classList.contains("is-open") ? null : item.id);
  });

  const body = document.createElement("div");
  body.className = "ann-body";
  body.id = bodyId;
  body.hidden = !open;

  const actions = document.createElement("div");
  actions.className = "ann-actions";
  const button = (icon, key, onClick) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "btn";
    b.dataset.icon = icon;
    b.textContent = t(key);
    b.addEventListener("click", onClick);
    return b;
  };
  actions.append(
    button("play", "announcements.play", async () => {
      const r = await apiPost("/api/announcements/" + item.id + "/play");
      if (!r.ok) {
        showToast(t("common.failed"),
                  r.error === "not_playing_music"
                    ? t("announcements.not_playing")
                    : errorLabel(r.error), { error: true });
      }
    }),

    button("folder", "annfiles.files_btn", () => openAnnounceFiles("custom:" + item.id)),

    button("pencil", "common.edit", () => startEditAnnouncement(item)),
  );

  const manage = document.createElement("div");
  manage.className = "ann-manage";
  const switchLabel = document.createElement("label");
  switchLabel.className = "ann-switch";
  const toggle = document.createElement("input");
  toggle.type = "checkbox";
  toggle.className = "switch-input";
  toggle.setAttribute("role", "switch");
  toggle.checked = !!item.enabled;
  const switchText = document.createElement("span");
  switchText.textContent = t("announcements.active");
  switchLabel.append(toggle, switchText);
  toggle.addEventListener("change", async () => {
    const wanted = toggle.checked;
    li.classList.toggle("is-off", !wanted);
    const r = await apiPost("/api/announcements/" + item.id, { enabled: wanted });
    if (!r.ok) {
      toggle.checked = !wanted;
      li.classList.toggle("is-off", wanted);
      showError(r.error);
      return;
    }
    refreshAnnouncements();
  });

  const del = button("trash", "common.delete", async () => {
    if (!(await showConfirm(t("confirm.delete_announcement", { name: item.name })))) return;
    const r = await apiDelete("/api/announcements/" + item.id);
    if (!r.ok) showError(r.error);
    if (openAnnouncementId === item.id) openAnnouncementId = null;
    refreshAnnouncements();
  });
  del.classList.add("btn-danger-outline", "ann-delete");
  manage.append(switchLabel, del);

  body.append(actions, manage);
  li.append(head, body);
  return li;
}

refreshAnnouncements();
refreshEvery(refreshAnnouncements, 20000);

/* Schedules: a row per schedule, and one form for a new one or the one being edited. */
let schedulesData = [];
let schedulesAllowed = [];
let openScheduleId = null;
let editingScheduleId = null;
let runningScheduleId = null;

function weekdayNames() {
  const format = new Intl.DateTimeFormat(currentLang, { weekday: "short", timeZone: "UTC" });
  // 1 January 2024 was a Monday, which is day 0 here as on the Pi.
  return [0, 1, 2, 3, 4, 5, 6].map((i) => format.format(new Date(Date.UTC(2024, 0, 1 + i))).replace(/\.$/, ""));
}

function scheduleDaysLabel(item) {
  if (item.date) {
    return new Intl.DateTimeFormat(currentLang, { dateStyle: "medium", timeZone: "UTC" })
      .format(new Date(item.date + "T00:00:00Z"));
  }
  const days = item.days || [];
  if (!days.length || days.length === 7) return t("schedules.when_daily");
  const names = weekdayNames();
  return days.map((day) => names[day]).join(" ");
}

function scheduleMeta(item) {
  const bits = [scheduleDaysLabel(item)];
  if (item.start && item.stop) bits.push(item.start + " → " + item.stop);
  else if (item.start) bits.push(item.start);
  else bits.push("→ " + item.stop);
  if (item.stop) bits.push(t("schedules.action_" + item.stop_action));
  const extra = Object.keys(item.settings || {}).length;
  if (extra) bits.push(t("schedules.settings_count", { n: extra }));
  return bits.join(" · ");
}

function scheduleNextWhen(d) {
  const next = d.schedule_next;
  if (String(d.system_time || "").slice(0, 10) === next.date) return next.at;
  const day = new Intl.DateTimeFormat(currentLang, { weekday: "long", timeZone: "UTC" })
    .format(new Date(next.date + "T00:00:00Z"));
  return day + " " + next.at;
}

function paintScheduleRunning(id) {
  runningScheduleId = id;
  document.querySelectorAll("#scheduleList .ann-item").forEach((li) => {
    li.classList.toggle("is-playing", li.dataset.id === id);
  });
}

function setScheduleOpen(id) {
  openScheduleId = id;
  document.querySelectorAll("#scheduleList .ann-item").forEach((li) => {
    const open = li.dataset.id === id;
    li.classList.toggle("is-open", open);
    li.querySelector(".ann-head").setAttribute("aria-expanded", open ? "true" : "false");
    li.querySelector(".ann-body").hidden = !open;
  });
}

function scheduleRow(item) {
  const li = document.createElement("li");
  li.className = "ann-item";
  li.dataset.id = item.id;
  if (!item.enabled) li.classList.add("is-off");
  if (item.id === runningScheduleId) li.classList.add("is-playing");
  const open = item.id === openScheduleId;
  if (open) li.classList.add("is-open");

  const bodyId = "sched-body-" + item.id;
  const head = document.createElement("button");
  head.type = "button";
  head.className = "ann-head";
  head.setAttribute("aria-expanded", open ? "true" : "false");
  head.setAttribute("aria-controls", bodyId);
  const text = document.createElement("span");
  text.className = "ann-text";
  const name = document.createElement("span");
  name.className = "ann-name";
  name.textContent = item.name;
  const meta = document.createElement("span");
  meta.className = "ann-meta";
  meta.textContent = scheduleMeta(item);
  if (!item.enabled) {
    const off = document.createElement("span");
    off.className = "sr-only";
    off.textContent = ", " + t("announcements.state_off");
    meta.appendChild(off);
  }
  text.append(name, meta);
  const chevron = document.createElement("span");
  chevron.className = "ann-chevron";
  chevron.dataset.icon = "chevron";
  chevron.setAttribute("aria-hidden", "true");
  head.append(text, chevron);
  head.addEventListener("click", () => setScheduleOpen(li.classList.contains("is-open") ? null : item.id));

  const body = document.createElement("div");
  body.className = "ann-body";
  body.id = bodyId;
  body.hidden = !open;

  const actions = document.createElement("div");
  actions.className = "ann-actions is-two";
  const edit = document.createElement("button");
  edit.type = "button";
  edit.className = "btn sched-edit";
  edit.dataset.icon = "pencil";
  edit.textContent = t("common.edit");
  edit.addEventListener("click", () => startEditSchedule(item));
  const del = document.createElement("button");
  del.type = "button";
  del.className = "btn btn-danger-outline sched-delete";
  del.dataset.icon = "trash";
  del.textContent = t("common.delete");
  del.addEventListener("click", async () => {
    if (!(await showConfirm(t("confirm.delete_schedule", { name: item.name })))) return;
    const r = await apiDelete("/api/schedules/" + item.id);
    if (!r.ok) showError(r.error);
    if (editingScheduleId === item.id) resetScheduleForm();
    refreshSchedules();
  });
  actions.append(edit, del);

  const manage = document.createElement("div");
  manage.className = "ann-manage";
  const switchLabel = document.createElement("label");
  switchLabel.className = "ann-switch";
  const toggle = document.createElement("input");
  toggle.type = "checkbox";
  toggle.className = "switch-input";
  toggle.setAttribute("role", "switch");
  toggle.checked = !!item.enabled;
  const switchText = document.createElement("span");
  switchText.textContent = t("announcements.active");
  switchLabel.append(toggle, switchText);
  toggle.addEventListener("change", async () => {
    const wanted = toggle.checked;
    const r = await apiPost("/api/schedules/" + item.id, { enabled: wanted });
    if (!r.ok) {
      toggle.checked = !wanted;
      showError(r.error);
      return;
    }
    refreshSchedules();
  });
  manage.append(switchLabel);

  body.append(actions, manage);
  li.append(head, body);
  return li;
}

function renderSchedules() {
  const list = document.getElementById("scheduleList");
  list.replaceChildren();
  if (!schedulesData.length) {
    const li = document.createElement("li");
    li.textContent = t("schedules.none_yet");
    list.appendChild(li);
    return;
  }
  schedulesData.forEach((item) => list.appendChild(scheduleRow(item)));
}

async function refreshSchedules() {
  const result = await apiGet("/api/schedules");
  if (!result.ok || !result.data) return;
  schedulesData = Array.isArray(result.data.schedules) ? result.data.schedules : [];
  schedulesAllowed = Array.isArray(result.data.settings) ? result.data.settings : [];
  renderSchedules();
  fillScheduleSettingChoices();
}

const schedForm = document.getElementById("scheduleForm");
const schedOverrides = document.getElementById("schedOverrides");
const schedAddSetting = document.getElementById("schedAddSetting");

function paintScheduleDays(chosen) {
  const box = document.getElementById("schedDays");
  const picked = new Set(chosen || Array.from(box.querySelectorAll("input:checked")).map((el) => Number(el.value)));
  box.replaceChildren(...weekdayNames().map((label, day) => {
    const chip = document.createElement("label");
    chip.className = "day-chip";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = String(day);
    input.checked = picked.has(day);
    const text = document.createElement("span");
    text.textContent = label;
    chip.append(input, text);
    return chip;
  }));
}

function updateScheduleForm() {
  const when = document.getElementById("schedWhen").value;
  document.getElementById("schedDays").hidden = when !== "days";
  document.getElementById("schedDateRow").hidden = when !== "date";
  const startOn = document.getElementById("schedStartOn").checked;
  const stopOn = document.getElementById("schedStopOn").checked;
  document.getElementById("schedStart").disabled = !startOn;
  document.getElementById("schedStop").disabled = !stopOn;
  document.getElementById("schedActionRow").hidden = !stopOn;
}
["schedWhen", "schedStartOn", "schedStopOn"].forEach((id) => {
  document.getElementById(id).addEventListener("change", updateScheduleForm);
});

function fillScheduleLists(value) {
  const select = document.getElementById("schedList");
  let lists = [];
  try {
    lists = (listsData && listsData.lists) || [];
  } catch (e) {
    // Declared further down: at startup the music lists are not there yet.
  }
  select.replaceChildren();
  [["keep", t("schedules.list_keep")], ["all", t("schedules.list_all")]]
    .concat(lists.map((one) => ["id:" + one.id, one.name]))
    .forEach(([optionValue, label]) => {
      const option = document.createElement("option");
      option.value = optionValue;
      option.textContent = label;
      select.appendChild(option);
    });
  const wanted = value === null || value === undefined ? "keep" : value === "" ? "all" : "id:" + value;
  setFieldValue(select, wanted);
}

/* A schedule's setting is edited with a copy of the very control the settings pages use. */
function overrideSource(key) {
  const el = document.querySelector('#main [data-key="' + key + '"]');
  return el && ["INPUT", "SELECT"].includes(el.tagName) ? el : null;
}

function overrideLabel(key) {
  const el = overrideSource(key);
  const label = el && el.id ? document.querySelector('label[for="' + el.id + '"]') : null;
  return label ? label.textContent.trim() : key;
}

function overrideRow(key, values) {
  const source = overrideSource(key);
  if (!source) return null;
  const row = document.createElement("div");
  row.className = "field-row override-row";
  const label = document.createElement("span");
  label.className = "field-label";
  label.textContent = overrideLabel(key);
  const control = source.cloneNode(true);
  ["id", "data-key", "data-key-minute", "aria-describedby", "hidden", "disabled"]
    .forEach((name) => control.removeAttribute(name));
  control.dataset.override = key;
  control.setAttribute("aria-label", label.textContent);
  if (source.dataset.keyMinute) {
    control.dataset.overrideMinute = source.dataset.keyMinute;
    control.value = formatHM(Number(values[key]) || 0, Number(values[source.dataset.keyMinute]) || 0);
  } else {
    setFieldValue(control, values[key]);
  }
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "btn btn-icon btn-small override-remove";
  remove.dataset.icon = "x";
  remove.setAttribute("aria-label", t("schedules.remove_setting", { name: label.textContent }));
  remove.addEventListener("click", () => {
    row.remove();
    fillScheduleSettingChoices();
  });
  const holder = document.createElement("span");
  holder.className = "override-control";
  holder.append(control, remove);
  row.append(label, holder);
  return row;
}

function currentSettingValues(key) {
  const source = overrideSource(key);
  const values = {};
  if (!source) return values;
  if (source.dataset.keyMinute) {
    const [hour, minute] = timeFieldParts(source);
    values[key] = hour;
    values[source.dataset.keyMinute] = minute;
  } else {
    values[key] = collectFieldValue(source);
  }
  return values;
}

function fillScheduleSettingChoices() {
  const used = new Set(Array.from(schedOverrides.querySelectorAll("[data-override]"))
    .map((el) => el.dataset.override));
  const choices = schedulesAllowed
    .filter((key) => key !== "BASE_VOLUME" && !used.has(key) && overrideSource(key))
    .map((key) => [key, overrideLabel(key)])
    .sort((a, b) => a[1].localeCompare(b[1]));
  schedAddSetting.replaceChildren();
  [["", t("schedules.add_setting")]].concat(choices).forEach(([value, label]) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    schedAddSetting.appendChild(option);
  });
}
schedAddSetting.addEventListener("change", () => {
  const key = schedAddSetting.value;
  if (!key) return;
  const row = overrideRow(key, currentSettingValues(key));
  if (row) schedOverrides.appendChild(row);
  fillScheduleSettingChoices();
});

function collectScheduleSettings() {
  const out = {};
  schedOverrides.querySelectorAll("[data-override]").forEach((el) => {
    if (el.dataset.overrideMinute) {
      const [hour, minute] = timeFieldParts(el);
      out[el.dataset.override] = hour;
      out[el.dataset.overrideMinute] = minute;
    } else {
      out[el.dataset.override] = collectFieldValue(el);
    }
  });
  const volume = document.getElementById("schedVolume").value.trim();
  if (volume !== "") out.BASE_VOLUME = volume;
  return out;
}

function fillScheduleForm(item) {
  const data = item || { name: "", days: [], date: null, start: "07:00", stop: null,
                         stop_action: "pause", list: null, settings: {} };
  document.getElementById("schedName").value = data.name;
  document.getElementById("schedWhen").value = data.date ? "date" : (data.days || []).length ? "days" : "daily";
  paintScheduleDays(data.days || []);
  document.getElementById("schedDate").value = data.date || "";
  document.getElementById("schedStartOn").checked = !!data.start;
  document.getElementById("schedStart").value = data.start || "07:00";
  document.getElementById("schedStopOn").checked = !!data.stop;
  document.getElementById("schedStop").value = data.stop || "09:00";
  document.getElementById("schedAction").value = data.stop_action || "pause";
  const settings = data.settings || {};
  document.getElementById("schedVolume").value = "BASE_VOLUME" in settings ? settings.BASE_VOLUME : "";
  fillScheduleLists(data.list);
  schedOverrides.replaceChildren();
  Object.keys(settings).forEach((key) => {
    if (key === "BASE_VOLUME") return;
    const row = overrideRow(key, settings);
    if (row) schedOverrides.appendChild(row);
  });
  fillScheduleSettingChoices();
  updateScheduleForm();
}

function setScheduleFormTitle(item) {
  const title = document.getElementById("scheduleFormTitle");
  title.dataset.i18n = item ? "schedules.edit_section" : "schedules.add_section";
  if (item) title.dataset.i18nVarName = item.name;
  else delete title.dataset.i18nVarName;
  title.textContent = t(title.dataset.i18n, item ? { name: item.name } : undefined);
}

function resetScheduleForm() {
  editingScheduleId = null;
  setScheduleFormTitle(null);
  fillScheduleForm(null);
  document.getElementById("scheduleFormSection").open = false;
}

function startEditSchedule(item) {
  editingScheduleId = item.id;
  setScheduleFormTitle(item);
  fillScheduleForm(item);
  const section = document.getElementById("scheduleFormSection");
  section.open = true;
  section.scrollIntoView({ behavior: "smooth", block: "start" });
}

document.getElementById("scheduleFormSection").addEventListener("toggle", (event) => {
  // The lists are only known once their own card has loaded, well after this one.
  if (event.target.open && !editingScheduleId) fillScheduleLists(null);
});
document.getElementById("schedCancel").addEventListener("click", resetScheduleForm);

schedForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const when = document.getElementById("schedWhen").value;
  const list = document.getElementById("schedList").value;
  const body = {
    name: document.getElementById("schedName").value,
    days: when === "days"
      ? Array.from(document.querySelectorAll("#schedDays input:checked")).map((el) => Number(el.value)) : [],
    date: when === "date" ? document.getElementById("schedDate").value : null,
    start: document.getElementById("schedStartOn").checked ? document.getElementById("schedStart").value : null,
    stop: document.getElementById("schedStopOn").checked ? document.getElementById("schedStop").value : null,
    stop_action: document.getElementById("schedAction").value,
    list: list === "keep" ? null : list === "all" ? "" : list.slice(3),
    settings: collectScheduleSettings(),
  };
  if (when === "days" && !body.days.length) {
    showError("schedule_bad_days");
    return;
  }
  if (when === "date" && !body.date) {
    showError("schedule_bad_date");
    return;
  }
  const result = await apiPost(editingScheduleId ? "/api/schedules/" + editingScheduleId : "/api/schedules", body);
  if (!result.ok) {
    showError(result.error);
    return;
  }
  openScheduleId = result.data.id;
  showToast(t("schedules.saved", { name: result.data.name }));
  resetScheduleForm();
  refreshSchedules();
});

fillScheduleForm(null);
refreshSchedules();
refreshEvery(refreshSchedules, 20000);
window.LANG_CHANGE_LISTENERS.push(() => {
  renderSchedules();
  paintScheduleDays();
  fillScheduleSettingChoices();
});

let audioOutputs = [];

function paintAudioDetected() {
  const kind = document.getElementById("audioOutputSelect").value;
  const line = document.getElementById("audioOutputDetected");
  const found = audioOutputs.find((o) => o.kind === kind);
  if (found) {
    line.textContent = t("audioout.found", { name: found.description });
    line.classList.remove("warning");
  } else if (kind === "bluetooth") {
    line.textContent = t("audioout.bluetooth_none");
    line.classList.remove("warning");
  } else {
    line.textContent = t("audioout.not_found");
    line.classList.add("warning");
  }
}

async function refreshAudioOutputs() {
  const result = await apiGet("/api/audio/outputs");
  if (!result.ok || !result.data || !Array.isArray(result.data.outputs)) return;
  audioOutputs = result.data.outputs;
  paintAudioDetected();
}

document.getElementById("audioOutputSelect").addEventListener("change", paintAudioDetected);
document.getElementById("btnAudioTest").addEventListener("click", async () => {
  const btn = document.getElementById("btnAudioTest");
  btn.disabled = true;
  try {
    const result = await apiPost("/api/audio/test", { output: document.getElementById("audioOutputSelect").value });
    if (result.ok) showToast(t("audioout.test_sent"));
    else showError(result.error);
  } finally {
    btn.disabled = false;
  }
});
refreshAudioOutputs();
refreshEvery(refreshAudioOutputs, 20000);

let recentPlayingKey;
let recentRetry = null;

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
    if (!current && item.key) li.append(libraryButton(item, true));
    return li;
  }));
  document.getElementById("recentEmpty").hidden = items.length > 0;
  clearTimeout(recentRetry);
  if (result.data.pending) recentRetry = setTimeout(refreshRecent, 4000);
}

refreshRecent();
refreshEvery(refreshRecent, 60000);

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

function likedDate(seconds) {
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
    when.textContent = likedDate(item.liked_at);
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
window.LANG_CHANGE_LISTENERS.push(() => {
  renderLikes();
  paintLikeButton();
});

/* Duplicate tracks: the same song catalogued more than once, grouped by title and artist. */
let duplicateGroups = [];
let duplicatesAsked = false;

function duplicateFacts(copy) {
  return [copy.album, formatBytes(copy.size), formatClock(copy.duration),
    copy.kbps ? copy.kbps + " kb/s" : ""].filter(Boolean).join(" \u00b7 ");
}

function duplicateCopy(copy, group) {
  const li = document.createElement("li");
  li.className = "duplicate-copy" + (copy.hidden ? " is-hidden" : "");
  const main = document.createElement("span");
  main.className = "recent-main";
  const title = document.createElement("span");
  title.className = "recent-title";
  title.textContent = copy.name;
  const facts = document.createElement("span");
  facts.className = "recent-artist";
  facts.textContent = duplicateFacts(copy);
  main.append(title, facts);
  li.append(main);
  if (copy.hidden) {
    const badge = document.createElement("span");
    badge.className = "badge duplicate-badge";
    badge.textContent = t("duplicates.hidden_badge");
    li.append(badge);
  }
  li.append(libraryButton(copy, false), duplicateKeepButton(copy, group));
  return li;
}

function duplicateKeepButton(copy, group) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "btn btn-small duplicate-keep";
  b.dataset.icon = copy.hidden ? "refresh" : "check";
  const label = document.createElement("span");
  label.textContent = t(copy.hidden ? "duplicates.restore" : "duplicates.keep");
  b.append(label);
  b.title = t(copy.hidden ? "duplicates.restore_aria" : "duplicates.keep_aria",
    { name: copy.name });
  b.setAttribute("aria-label", b.title);
  b.dataset.costAction = "";
  b.addEventListener("click", async () => {
    b.disabled = true;
    let failed = null;
    if (copy.hidden) {
      const asked = await apiPost("/api/library/hide",
        { key: copy.key, hidden: false, path: copy.path });
      if (!asked.ok) failed = asked;
    } else {
      for (const other of group.tracks) {
        if (other.key === copy.key) continue;
        const asked = await apiPost("/api/library/hide",
          { key: other.key, hidden: true, path: other.path });
        if (!asked.ok) failed = asked;
      }
    }
    b.disabled = false;
    if (failed) {
      showToolError(t("duplicates.failed"), failed);
      return;
    }
    showToast(copy.hidden
      ? t("duplicates.restored", { name: copy.name })
      : t("duplicates.kept", { name: copy.name }));
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
    badge.className = "badge" + (group.same_file ? " duplicate-same" : "");
    badge.textContent = group.same_file
      ? t("duplicates.same_file")
      : t("duplicates.copies", { n: group.tracks.length });
    head.append(name, badge);
    const copies = document.createElement("ul");
    copies.className = "duplicate-copies";
    copies.replaceChildren(...group.tracks.map((copy) => duplicateCopy(copy, group)));
    fold.append(head, copies);
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
  const r = await apiPost("/api/settings", { SPEAKER_BT_ADAPTER: speaker, FLIC_HCI_DEVICE: flic });
  if (!r.ok) {
    showToolError(t("common.failed"), r);
    return;
  }
  btControllersDirty = false;
  settingsBaseline.SPEAKER_BT_ADAPTER = speaker;
  settingsBaseline.FLIC_HCI_DEVICE = flic;
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
  else if (!d.active) text = t("btctl.flic_not_running");
  else text = t("btctl.flic_running", { n: (d.buttons || []).length });
  line.textContent = text;
  line.dataset.state = blocked ? "warn" : "";
  document.getElementById("btnFlicInstall").hidden = d.sdk || blocked;
  document.getElementById("flicEnabledRow").hidden = !d.sdk || (blocked && !d.enabled);
  document.getElementById("flicEnabledRow").classList.toggle("is-held", held);
  document.getElementById("flicEnabled").checked = !!d.enabled;
  document.getElementById("btnFlicPair").hidden = !(d.sdk && d.active) || blocked;
  document.getElementById("btFlicController").disabled = blocked;
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
    title.className = "recent-title";
    title.textContent = item.title + (item.artist ? " \u2014 " + item.artist : "");
    const n = document.createElement("span");
    n.className = "recent-when";
    n.textContent = "\u00d7" + item.count;
    li.append(title, n);
    return li;
  }));
}
refreshToday();
refreshEvery(refreshToday, 120000);

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

function libraryButton(item, next) {
  const b = document.createElement("button");
  b.type = "button";
  const name = item.title || item.name || "";
  if (next) {
    b.className = "btn btn-small library-next";
    b.dataset.icon = "list";
    b.title = t("library.next_aria", { title: name });
    const label = document.createElement("span");
    label.textContent = t("library.next");
    b.append(label);
    b.setAttribute("aria-label", t("library.next_aria", { title: name }));
  } else {
    b.className = "btn btn-icon btn-small recent-play";
    b.dataset.icon = "play";
    b.setAttribute("aria-label", t("library.play", { title: name }));
    b.title = t("library.play", { title: name });
  }
  b.dataset.costAction = next ? "queue" : "play_now";
  paintCost(b);
  b.addEventListener("click", async () => {
    b.disabled = true;
    const r = await apiPost(next ? "/api/library/queue" : "/api/library/play", { key: item.key });
    b.disabled = false;
    if (!r.ok) {
      showToolError(t("home.transport_failed"), r);
      return;
    }
    if (next) {
      const started = r.data && r.data.started;
      showToast(started ? t("library.started", { title: name })
        : t("library.queued", { title: name }),
      started ? "" : t("library.position", { n: (r.data && r.data.position) || 1 }));
    }
    refreshStatus();
    refreshUpnext();
  });
  return b;
}

async function refreshUpnext() {
  const card = document.getElementById("upnextCard");
  const result = await apiGet("/api/queue");
  if (!result.ok || !result.data || !Array.isArray(result.data.items)) {
    card.hidden = true;
    return;
  }
  card.hidden = !result.data.enabled;
  if (!result.data.enabled) return;
  const items = result.data.items;
  document.getElementById("upnextList").replaceChildren(...items.map((item, i) => {
    const li = document.createElement("li");
    const rank = document.createElement("span");
    rank.className = "upnext-rank";
    rank.textContent = String(i + 1);
    li.append(rank, trackMain(item));

    if (item.requested) {
      li.classList.add("is-requested");
      const tag = document.createElement("span");
      tag.className = "upnext-tag";
      tag.textContent = t("upnext.requested");
      li.append(tag);
    }
    if (item.key) li.append(libraryButton(item, false));
    return li;
  }));
  document.getElementById("upnextEmpty").hidden = items.length > 0;
}
refreshEvery(refreshUpnext, 60000);

const librarySearch = document.getElementById("librarySearch");
const libraryArtist = document.getElementById("libraryArtist");
const libraryAlbum = document.getElementById("libraryAlbum");
const libraryGenre = document.getElementById("libraryGenre");
let libraryOffset = 0;
let librarySeq = 0;
let libraryTimer = null;
let libraryCatalogue = "";

function fillFacet(select, entries, allKey) {
  const keep = select.value;
  const all = document.createElement("option");
  all.value = "";
  all.dataset.i18n = allKey;
  all.textContent = t(allKey);
  select.replaceChildren(all, ...entries.map((e) => {
    const o = document.createElement("option");
    o.value = e.name;
    o.textContent = e.name + " (" + e.count + ")";
    return o;
  }));
  select.value = entries.some((e) => e.name === keep) ? keep : "";
}

async function refreshLibraryFacets() {
  const q = libraryArtist.value ? "?artist=" + encodeURIComponent(libraryArtist.value) : "";
  const r = await apiGet("/api/library/facets" + q);
  if (!r.ok || !r.data) return;
  fillFacet(libraryArtist, r.data.artists || [], "library.all_artists");
  fillFacet(libraryAlbum, r.data.albums || [], "library.all_albums");
  fillFacet(libraryGenre, r.data.genres || [], "library.all_genres");
  libraryGenre.hidden = !(r.data.genres || []).length;
  paintGenrePlay();
}

function libraryAsked() {
  return librarySearch.value.trim() || libraryArtist.value || libraryAlbum.value || libraryGenre.value;
}

async function refreshLibrary(more) {
  const seq = ++librarySeq;
  const card = document.getElementById("libraryCard");
  const list = document.getElementById("libraryList");
  const params = new URLSearchParams();
  if (librarySearch.value.trim()) params.set("q", librarySearch.value.trim());
  if (libraryArtist.value) params.set("artist", libraryArtist.value);
  if (libraryAlbum.value) params.set("album", libraryAlbum.value);
  if (libraryGenre.value) params.set("genre", libraryGenre.value);
  libraryOffset = more ? libraryOffset : 0;
  params.set("offset", String(libraryOffset));
  const r = await apiGet("/api/library?" + params.toString());
  if (seq !== librarySeq) return;
  if (!r.ok || !r.data) {
    card.hidden = true;
    return;
  }
  card.hidden = false;
  const d = r.data;
  const status = d.status || {};
  const count = document.getElementById("libraryCount");
  const asked = libraryAsked();

  const reading = status.total && status.read < status.total
    ? " " + t("library.reading", { n: status.read, total: status.total }) : "";
  count.textContent = (asked ? t("library.found", { n: d.total })
    : t("library.size", { n: status.total || 0 })) + reading;
  const signature = status.total + "/" + status.read;
  if (signature !== libraryCatalogue) {
    libraryCatalogue = signature;
    refreshLibraryFacets();
  }
  if (!asked) {
    list.replaceChildren();
    document.getElementById("libraryMore").hidden = true;
    return;
  }
  const rows = d.items.map((item) => {
    const li = document.createElement("li");
    li.append(trackMain(item));
    const actions = document.createElement("span");
    actions.className = "library-actions";
    if (document.body.dataset.access !== "guest") actions.append(libraryListButton(item));
    actions.append(libraryButton(item, true));
    li.append(actions);
    return li;
  });
  if (more) list.append(...rows);
  else list.replaceChildren(...rows);
  libraryOffset += d.items.length;
  document.getElementById("libraryMore").hidden = libraryOffset >= d.total;
}

librarySearch.addEventListener("input", () => {
  clearTimeout(libraryTimer);
  libraryTimer = setTimeout(() => refreshLibrary(false), 300);
});
libraryArtist.addEventListener("change", async () => {
  libraryAlbum.value = "";
  await refreshLibraryFacets();
  refreshLibrary(false);
});
libraryAlbum.addEventListener("change", () => refreshLibrary(false));
libraryGenre.addEventListener("change", () => {
  refreshLibrary(false);
  paintGenrePlay();
});
document.getElementById("libraryMore").addEventListener("click", () => refreshLibrary(true));
refreshLibrary(false);

refreshEvery(() => {
  if (!libraryAsked()) refreshLibrary(false);
}, 30000);

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

let listsData = { lists: [], active: null };
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
    actions.hidden = !libraryGenre.value || document.body.dataset.access === "guest";
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
  const manual = (listsData.lists || []).filter((l) => l.kind === "manual");
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
                     editingListId === item.id].join("|");
  if (signature === detailSignature) return;
  detailSignature = signature;

  const editing = editingListId === item.id;
  listsDetailActions.classList.toggle("is-two", editing);
  listsDetailActions.replaceChildren(...(editing ? [
    listActionButton("check", "common.save", () => saveList(item)),
    listActionButton("x", "common.cancel", () => {
      editingListId = null;
      renderDetail(item);
    }),
  ] : [
    listActionButton("play", "lists.play", () => playList(item)),
    listActionButton("pencil", "common.edit", () => {
      editingListId = item.id;
      renderDetail(item);
    }),
    listActionButton("trash", "common.delete", () => deleteList(item), "btn-danger-outline"),
  ]));

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
  loadListTracks(item, tracks, item.kind === "manual");
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
refreshEvery(refreshLists, 20000);

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

// The Test button asks the browser directly and writes down what it answered.
document.getElementById("hapticsTest").addEventListener("click", () => {
  const line = document.getElementById("hapticsResult");
  if (typeof navigator.vibrate !== "function") {
    line.textContent = t("haptics.unsupported");
    return;
  }
  let accepted = false;
  try {
    accepted = navigator.vibrate([HAPTIC_TAP_MS, 60, HAPTIC_TAP_MS]);
  } catch (e) {
    accepted = false;
  }
  line.textContent = t(accepted ? "haptics.sent" : "haptics.refused");
});

let suggestState = null;
let suggestRenaming = false;
let nameGenerating = false;

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
  if (suggestState.me && !suggestState.me.name && !nameGenerating) {
    nameGenerating = true;
    const r = await giveGeneratedName();
    nameGenerating = false;
    if (r) return refreshSuggestions();
  }
  renderSuggestMe();
  renderSuggestions();
  const names = document.getElementById("suggestNamesSection");
  names.hidden = !suggestState.owner;
  if (suggestState.owner && names.open) refreshSuggestNames();
}

function renderSuggestMe() {
  const name = suggestState.me && suggestState.me.name;
  const locked = !!(suggestState.me && suggestState.me.locked);
  const editing = !name || (suggestRenaming && !locked);
  document.getElementById("suggestMeLine").hidden = editing;
  document.getElementById("suggestMeName").textContent = name || "";
  document.getElementById("suggestRenameBtn").hidden = locked;
  document.getElementById("suggestNameLocked").hidden = !(locked && name);
  document.getElementById("suggestNameForm").hidden = !editing;
  document.getElementById("suggestNameCancel").hidden = !name;

  document.getElementById("suggestForm").hidden = !name;
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
  document.getElementById("suggestEmpty").hidden = suggestState.items.length > 0;
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

function setAnnRepeat(times) {
  const n = Number(times);
  annRepeat.value = n === 0 ? "forever" : n === 1 ? "once" : "count";
  document.getElementById("annRepeatCount").value = String(n > 1 ? n : 3);
}
function annRepeatTimes() {
  if (annRepeat.value === "forever") return 0;
  if (annRepeat.value === "once") return 1;
  return Math.max(2, Number(document.getElementById("annRepeatCount").value) || 2);
}

function setAnnouncementFormMode(item) {
  editingAnnouncementId = item ? item.id : null;
  const title = document.getElementById("announcementFormTitle");
  const submit = document.getElementById("annSubmit");
  if (item) {
    title.dataset.i18n = "announcements.edit_section";
    title.dataset.i18nVarName = item.name;
    title.textContent = t("announcements.edit_section", { name: item.name });
    submit.dataset.i18n = "common.save";
    submit.dataset.icon = "check";
    submit.textContent = t("common.save");
  } else {
    title.dataset.i18n = "announcements.add_section";
    delete title.dataset.i18nVarName;
    title.textContent = t("announcements.add_section");
    submit.dataset.i18n = "common.add";
    submit.dataset.icon = "plus";
    submit.textContent = t("common.add");
  }
  document.getElementById("annEditCancel").hidden = !item;
}

function paintAnnFormVolume(key) {
  const row = document.getElementById("annVolumeRow");
  row.hidden = !key;
  row.innerHTML = "";
  if (key) row.appendChild(volumeControl(key, { hint: true }));
}

function resetAnnouncementForm() {
  document.getElementById("announcementForm").reset();
  document.getElementById("annTime").value = "12:00";
  annTrigger.value = "time";
  document.getElementById("annDelay").value = "30";
  setAnnRepeat(1);
  document.getElementById("annAutoChance").value = "1/1";
  document.getElementById("annManualChance").value = "1/1";
  document.getElementById("annAfter").value = "none";
  updateAnnTimeVisibility();
  paintAnnFormVolume(null);
  setAnnouncementFormMode(null);
}

function startEditAnnouncement(item) {
  document.getElementById("annName").value = item.name;
  document.getElementById("annFolder").value = item.folder || "";
  document.getElementById("annTime").value =
    String(item.hour).padStart(2, "0") + ":" + String(item.minute).padStart(2, "0");
  annTrigger.value = item.trigger || "time";
  document.getElementById("annDelay").value = String(item.delay_min || 30);
  setAnnRepeat(item.repeat_times === undefined ? 1 : item.repeat_times);
  document.getElementById("annAutoChance").value = item.auto_chance || "1/1";
  document.getElementById("annManualChance").value = item.manual_chance || "1/1";
  document.getElementById("annAfter").value = item.after_action || "none";
  updateAnnTimeVisibility();
  paintAnnFormVolume("custom:" + item.id);
  setAnnouncementFormMode(item);
  const section = document.getElementById("announcementFormSection");
  section.open = true;
  section.scrollIntoView({ behavior: "smooth", block: "start" });
  document.getElementById("annName").focus({ preventScroll: true });
}

function returnToAnnouncement(id) {
  document.getElementById("announcementFormSection").open = false;
  if (!id) return;
  setAnnouncementOpen(id);
  const row = document.querySelector('#announcementList .ann-item[data-id="' + CSS.escape(id) + '"]');
  if (!row) return;
  const reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  row.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "center" });
  const head = row.querySelector(".ann-head");
  if (head) head.focus({ preventScroll: true });
}

document.getElementById("annEditCancel").addEventListener("click", () => {
  const id = editingAnnouncementId;
  resetAnnouncementForm();
  returnToAnnouncement(id);
});

const ANNOUNCE_CHANCES = ["1/1", "3/4", "2/3", "1/2", "1/3", "1/4", "1/5", "1/7", "1/10"];
function chanceLabel(ratio) {
  if (!ratio || ratio === "1/1") return t("chance.always");
  const [k, n] = ratio.split("/");
  return t(k === "1" ? "chance.one_in" : "chance.k_in", { k, n });
}
function fillChanceSelects() {
  document.querySelectorAll(".chance-select").forEach((select) => {
    const current = select.value || "1/1";
    select.innerHTML = "";
    ANNOUNCE_CHANCES.forEach((ratio) => {
      const opt = document.createElement("option");
      opt.value = ratio;
      opt.textContent = chanceLabel(ratio);
      select.appendChild(opt);
    });
    select.value = current;
  });
}
fillChanceSelects();
window.LANG_CHANGE_LISTENERS.push(fillChanceSelects);

document.getElementById("announcementForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const [hour, minute] = timeFieldParts(document.getElementById("annTime"));
  const body = {
    name: document.getElementById("annName").value,
    folder: document.getElementById("annFolder").value,
    hour: Number(hour),
    minute: Number(minute),
    trigger: annTrigger.value,
    delay_min: Number(document.getElementById("annDelay").value),
    repeat_times: annRepeatTimes(),
    auto_chance: document.getElementById("annAutoChance").value,
    manual_chance: document.getElementById("annManualChance").value,
    after_action: annTrigger.value === "manual" ? "none" : document.getElementById("annAfter").value,
  };
  if (editingAnnouncementId) {
    if (!body.folder.trim()) delete body.folder;
    const r = await apiPost("/api/announcements/" + encodeURIComponent(editingAnnouncementId), body);
    if (!r.ok) {
      showError(r.error);
      return;
    }
    const id = editingAnnouncementId;
    resetAnnouncementForm();
    await refreshAnnouncements();
    returnToAnnouncement(id);
    showToast(t("announcements.updated"));
    return;
  }
  const result = await apiPost("/api/announcements", body);
  if (!result.ok) {
    showError(result.error);
    return;
  }
  resetAnnouncementForm();

  await refreshAnnouncements();
  if (result.data && result.data.id) openAnnounceFiles("custom:" + result.data.id);
  showToast(t("annfiles.created", { name: result.data ? result.data.name : "" }),
            t("annfiles.created_detail"));
});

const trackOrderSourceSelect = document.getElementById("trackOrderSource");
let trackOrderCurrent = [];

let trackOrderSelected = new Set();

function updateFilesBatch() {
  const bar = document.getElementById("annFilesBatch");
  const count = trackOrderCurrent.filter((n) => trackOrderSelected.has(n)).length;
  bar.hidden = trackOrderCurrent.length === 0;
  const all = document.getElementById("annFilesSelectAll");
  all.checked = count > 0 && count === trackOrderCurrent.length;
  all.indeterminate = count > 0 && count < trackOrderCurrent.length;
  const label = document.getElementById("annFilesBatchCount");
  label.dataset.i18n = count ? "annfiles.selected" : "annfiles.select_all";
  label.textContent = count ? t("annfiles.selected", { count }) : t("annfiles.select_all");
  ["annFilesBatchUp", "annFilesBatchDown", "annFilesBatchDelete"].forEach((id) => {
    document.getElementById(id).disabled = count === 0;
  });
}

function moveSelection(direction) {
  const items = trackOrderCurrent;
  const order = direction < 0 ? items.map((_, i) => i) : items.map((_, i) => items.length - 1 - i);
  order.forEach((i) => {
    const j = i + direction;
    if (j < 0 || j >= items.length) return;
    if (trackOrderSelected.has(items[i]) && !trackOrderSelected.has(items[j])) {
      [items[i], items[j]] = [items[j], items[i]];
    }
  });
  renderTrackOrderList();
}

document.getElementById("annFilesSelectAll").addEventListener("change", (e) => {
  trackOrderSelected = e.target.checked ? new Set(trackOrderCurrent) : new Set();
  renderTrackOrderList();
});
document.getElementById("annFilesBatchUp").addEventListener("click", () => moveSelection(-1));
document.getElementById("annFilesBatchDown").addEventListener("click", () => moveSelection(1));
document.getElementById("annFilesBatchDelete").addEventListener("click", async () => {
  const names = trackOrderCurrent.filter((n) => trackOrderSelected.has(n));
  if (!names.length) return;
  if (!(await showConfirm(t("annfiles.confirm_delete_many", { count: names.length })))) return;
  const source = trackOrderSourceSelect.value;
  let failed = 0;
  for (const name of names) {
    const r = await apiDelete("/api/announce_files/" + encodeURIComponent(source) +
                              "?name=" + encodeURIComponent(name));
    if (!r.ok) failed += 1;
  }
  trackOrderSelected = new Set();
  if (failed) showToast(t("common.failed"), t("annfiles.delete_some_failed", { count: failed }), { error: true });
  else showToast(t("annfiles.deleted_many", { count: names.length }));
  await loadTrackOrder();
  refreshAnnouncements();
});

const announcePreview = { audio: new Audio(), name: null };
announcePreview.audio.addEventListener("ended", () => {
  announcePreview.name = null;
  renderTrackOrderList();
});
function toggleAnnouncePreview(name) {
  const audio = announcePreview.audio;
  if (announcePreview.name === name && !audio.paused) {
    audio.pause();
    announcePreview.name = null;
  } else {
    const source = document.getElementById("trackOrderSource").value;
    audio.src = "/api/announce_files/" + encodeURIComponent(source) + "?name=" + encodeURIComponent(name);
    announcePreview.name = name;
    audio.play().catch(() => {
      announcePreview.name = null;
      showError("preview_failed");
      renderTrackOrderList();
    });
  }
  renderTrackOrderList();
}

function renderTrackOrderList() {
  const list = document.getElementById("trackOrderList");
  list.innerHTML = "";
  updateFilesBatch();
  if (!trackOrderCurrent.length) {
    const li = document.createElement("li");
    li.textContent = t("annfiles.empty");
    list.appendChild(li);
    return;
  }
  trackOrderCurrent.forEach((name, i) => {
    const li = document.createElement("li");
    const pick = document.createElement("input");
    pick.type = "checkbox";
    pick.className = "row-select";
    pick.checked = trackOrderSelected.has(name);
    pick.setAttribute("aria-label", name);
    pick.addEventListener("change", () => {
      if (pick.checked) trackOrderSelected.add(name); else trackOrderSelected.delete(name);
      li.classList.toggle("is-selected", pick.checked);
      updateFilesBatch();
    });
    li.classList.toggle("is-selected", pick.checked);
    li.appendChild(pick);
    const label = document.createElement("span");

    label.textContent = name.replace(/\.[a-z0-9]{2,5}$/i, "");
    label.title = name;

    const actions = document.createElement("span");
    actions.className = "device-actions";

    const upBtn = document.createElement("button");
    upBtn.type = "button";
    upBtn.className = "btn btn-icon";
    upBtn.dataset.icon = "arrow-up";
    upBtn.setAttribute("aria-label", t("trackorder.move_up") + " - " + name);
    upBtn.disabled = i === 0;
    upBtn.addEventListener("click", () => {
      [trackOrderCurrent[i - 1], trackOrderCurrent[i]] = [trackOrderCurrent[i], trackOrderCurrent[i - 1]];
      renderTrackOrderList();
    });

    const downBtn = document.createElement("button");
    downBtn.type = "button";
    downBtn.className = "btn btn-icon";
    downBtn.dataset.icon = "arrow-down";
    downBtn.setAttribute("aria-label", t("trackorder.move_down") + " - " + name);
    downBtn.disabled = i === trackOrderCurrent.length - 1;
    downBtn.addEventListener("click", () => {
      [trackOrderCurrent[i], trackOrderCurrent[i + 1]] = [trackOrderCurrent[i + 1], trackOrderCurrent[i]];
      renderTrackOrderList();
    });

    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "btn btn-icon btn-danger-outline";
    delBtn.dataset.icon = "trash";
    delBtn.setAttribute("aria-label", t("annfiles.delete_aria", { name }));
    delBtn.title = t("annfiles.delete_aria", { name });
    delBtn.addEventListener("click", () => deleteAnnounceFile(name));

    const listenBtn = document.createElement("button");
    listenBtn.type = "button";
    listenBtn.className = "btn btn-icon";
    const playing = announcePreview.name === name && !announcePreview.audio.paused;
    listenBtn.dataset.icon = playing ? "stop" : "headphones";
    listenBtn.setAttribute("aria-label", playing ? t("annfiles.stop_listen") : t("annfiles.listen", { name }));
    listenBtn.title = listenBtn.getAttribute("aria-label");
    listenBtn.addEventListener("click", () => toggleAnnouncePreview(name));

    splitActions(actions, [upBtn, downBtn, listenBtn], [delBtn]);
    li.append(label, actions);
    list.appendChild(li);
  });
}

function openAnnounceFiles(source) {
  if ([...trackOrderSourceSelect.options].some((o) => o.value === source)) {
    trackOrderSourceSelect.value = source;
    loadTrackOrder();
  }
  goToCard("settings", "trackOrderTitle");
}

async function uploadAnnounceFiles(files) {
  const source = trackOrderSourceSelect.value;
  const btn = document.getElementById("btnAnnFilesAdd");
  const progress = document.getElementById("annFilesProgress");
  btn.disabled = true;
  trackOrderSourceSelect.disabled = true;
  progress.hidden = false;
  let added = 0;
  const failed = [];
  for (let i = 0; i < files.length; i++) {
    const file = files[i];
    progress.textContent = t("annfiles.sending", { n: i + 1, total: files.length, name: file.name });
    navTransfer.show("announce", i, files.length, file.name);
    const body = new FormData();
    body.append("name", file.name);
    body.append("mtime", String(Math.round(file.lastModified / 1000)));
    body.append("file", file, file.name);
    const result = await apiFetch("/api/announce_files/" + encodeURIComponent(source), { method: "POST", body });
    if (result.ok) added += 1;
    else failed.push({ name: file.name, error: result.error });
  }
  progress.hidden = true;
  navTransfer.finish("announce", !failed.length,
                     t(failed.length ? "transfer.failed" : "transfer.done",
                       { sent: added, failed: failed.length }));
  btn.disabled = false;
  trackOrderSourceSelect.disabled = false;
  await loadTrackOrder();
  refreshAnnouncements();
  if (!failed.length) {
    showToast(t("annfiles.added", { count: added }));
  } else {
    showToast(t("annfiles.some_failed", { count: failed.length, total: files.length }),
              failed[0].name + " : " + errorLabel(failed[0].error), { error: true });
  }
}

const systemSoundInput = document.getElementById("systemSoundInput");
let systemSoundTarget = null;

let openSystemSoundKey = null;

function setSystemSoundOpen(key) {
  openSystemSoundKey = key;
  document.querySelectorAll("#systemSoundsList .sys-item").forEach((li) => {
    const open = li.dataset.key === key;
    li.classList.toggle("is-open", open);
    li.querySelector(".ann-head").setAttribute("aria-expanded", open ? "true" : "false");
    li.querySelector(".ann-body").hidden = !open;
  });
}

async function refreshSystemSounds() {
  const result = await apiGet("/api/system_sounds");
  const list = document.getElementById("systemSoundsList");
  if (!result.ok || !Array.isArray(result.data)) return;
  await refreshAnnouncementVolumes();
  list.innerHTML = "";
  result.data.forEach((item) => {
    const li = document.createElement("li");
    li.className = "ann-item sys-item";
    li.dataset.key = item.key;
    const open = item.key === openSystemSoundKey;
    if (open) li.classList.add("is-open");

    const bodyId = "sys-sound-" + item.key;
    const head = document.createElement("button");
    head.type = "button";
    head.className = "ann-head";
    head.setAttribute("aria-expanded", open ? "true" : "false");
    head.setAttribute("aria-controls", bodyId);

    const text = document.createElement("span");
    text.className = "ann-text";
    const title = document.createElement("span");
    title.className = "ann-name";
    title.textContent = t("syssounds." + item.key);
    const state = document.createElement("span");
    state.className = "ann-meta sound-state";
    state.textContent = item.off ? t("syssounds.off")
      : item.custom ? t("syssounds.custom", { name: item.name })
      : t("syssounds.default");
    text.append(title, state);
    const chevron = document.createElement("span");
    chevron.className = "ann-chevron";
    chevron.dataset.icon = "chevron";
    chevron.setAttribute("aria-hidden", "true");
    head.append(text, chevron);
    head.addEventListener("click", () => {
      setSystemSoundOpen(li.classList.contains("is-open") ? null : item.key);
    });

    const body = document.createElement("div");
    body.className = "ann-body";
    body.id = bodyId;
    body.hidden = !open;
    if (!item.off) body.appendChild(volumeControl(item.key, { inline: true }));

    const actions = document.createElement("span");
    actions.className = "device-actions";

    const listen = document.createElement("button");
    listen.type = "button";
    listen.className = "btn";
    listen.dataset.icon = "play";
    listen.textContent = t("syssounds.listen");
    listen.disabled = !item.exists;
    listen.addEventListener("click", async () => {
      const r = await apiPost("/api/system_sounds/" + item.key + "/test");
      if (!r.ok) showError(r.error);
    });

    const replace = document.createElement("button");
    replace.type = "button";
    replace.className = "btn";
    replace.dataset.icon = "upload";
    replace.textContent = t("syssounds.replace");
    replace.addEventListener("click", () => {
      systemSoundTarget = item.key;
      systemSoundInput.click();
    });

    const restore = document.createElement("button");
    restore.type = "button";
    restore.className = "btn btn-warning-outline";
    restore.dataset.icon = "restart";
    restore.textContent = t("syssounds.restore");

    const off = document.createElement("button");
    off.type = "button";
    off.className = "btn";
    off.dataset.icon = "x";
    off.textContent = t("syssounds.disable");
    off.addEventListener("click", async () => {
      const r = await apiPost("/api/system_sounds/" + item.key + "/off");
      if (!r.ok) {
        showError(r.error);
        return;
      }
      showToast(t("syssounds.disabled", { name: t("syssounds." + item.key) }));
      refreshSystemSounds();
    });
    restore.addEventListener("click", async () => {
      const r = await apiDelete("/api/system_sounds/" + item.key);
      if (!r.ok) {
        showError(r.error);
        return;
      }
      showToast(t("syssounds.restored", { name: t("syssounds." + item.key) }));
      refreshSystemSounds();
    });

    const right = [];
    if (item.custom || item.off) right.push(restore);
    if (!item.off) right.push(off);
    splitActions(actions, [listen, replace], right);
    body.appendChild(actions);
    li.append(head, body);
    list.appendChild(li);
  });
}

systemSoundInput.addEventListener("change", async (event) => {
  const file = (event.target.files || [])[0];
  event.target.value = "";
  const key = systemSoundTarget;
  if (!file || !key) return;
  const body = new FormData();
  body.append("file", file, file.name);
  const r = await apiFetch("/api/system_sounds/" + key, { method: "POST", body });
  if (!r.ok) {
    showToast(t("common.failed"), errorLabel(r.error), { error: true });
    return;
  }
  showToast(t("syssounds.replaced", { name: t("syssounds." + key) }));
  refreshSystemSounds();
});

refreshSystemSounds();
window.LANG_CHANGE_LISTENERS.push(refreshSystemSounds);

async function deleteAnnounceFile(name) {
  if (!(await showConfirm(t("annfiles.confirm_delete", { name })))) return;
  const source = trackOrderSourceSelect.value;
  const result = await apiDelete("/api/announce_files/" + encodeURIComponent(source) +
                                 "?name=" + encodeURIComponent(name));
  if (result.ok) showToast(t("annfiles.deleted", { name }));
  else showError(result.error);
  await loadTrackOrder();
  refreshAnnouncements();
}

function fillPickerNotes() {
  const touch = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
  document.querySelectorAll(".picker-note").forEach((note) => {
    note.hidden = !touch;
    let question = note.querySelector(".picker-q");
    let answer = note.querySelector(".picker-more");
    if (!question) {
      question = document.createElement("button");
      question.type = "button";
      question.className = "btn-link picker-q";
      question.setAttribute("aria-expanded", "false");
      answer = document.createElement("span");
      answer.className = "picker-more";
      answer.hidden = true;
      question.addEventListener("click", () => {
        const open = answer.hidden;
        answer.hidden = !open;
        question.setAttribute("aria-expanded", open ? "true" : "false");
      });
      note.append(question, answer);
    }
    question.textContent = t("files.portal_q");

    const parts = t(note.dataset.note || "files.portal_note", { url: "\u0000" }).split("\u0000");
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "copy-chip";
    copy.dataset.icon = "copy";
    copy.textContent = location.origin;
    copy.title = t("files.copy");
    copy.setAttribute("aria-label", t("files.copy") + " " + location.origin);
    copy.addEventListener("click", () => copyText(location.origin, copy));
    answer.replaceChildren(parts[0] || "", copy, parts[1] || "");
  });
}

async function copyText(text, element) {
  let done = false;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      done = true;
    }
  } catch (e) {  }
  if (!done) {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    try { done = document.execCommand("copy"); } catch (e) { done = false; }
    area.remove();
  }
  if (done) {
    showToast(t("files.copied"), text);
  } else if (element) {
    const range = document.createRange();
    range.selectNodeContents(element);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    showToast(t("files.copy_manual"));
  }
}
fillPickerNotes();
window.LANG_CHANGE_LISTENERS.push(fillPickerNotes);

document.getElementById("btnAnnFilesAdd").addEventListener("click", () => {
  document.getElementById("annFilesInput").click();
});
document.getElementById("annFilesInput").addEventListener("change", (event) => {
  const files = Array.prototype.slice.call(event.target.files || []);

  event.target.value = "";
  if (files.length) uploadAnnounceFiles(files);
});

let trackOrderShownSource = null;
function paintAnnounceVolumeRow(source) {
  const holder = document.getElementById("announceVolumeHolder");
  if (!holder) return;
  holder.innerHTML = "";
  holder.appendChild(volumeControl(source, { hint: true }));
}
window.LANG_CHANGE_LISTENERS.push(() => paintAnnounceVolumeRow(trackOrderSourceSelect.value));

async function loadTrackOrder() {
  const source = trackOrderSourceSelect.value;
  if (source !== trackOrderShownSource) {
    trackOrderSelected = new Set();
    trackOrderShownSource = source;
  }
  await refreshAnnouncementVolumes();
  paintAnnounceVolumeRow(source);
  const hint = document.getElementById("trackOrderHint");
  hint.textContent = "";
  const result = await apiGet("/api/track_order/" + encodeURIComponent(source));

  if (source !== trackOrderSourceSelect.value) return;
  const folderLine = document.getElementById("annFilesFolder");
  folderLine.textContent = result.ok && result.data && result.data.folder
    ? t("annfiles.folder", { folder: result.data.folder }) : "";
  if (!result.ok || !result.data || !Array.isArray(result.data.order)) {
    trackOrderCurrent = [];
    renderTrackOrderList();
    hint.textContent = t("trackorder.load_failed", { error: errorLabel(result.error) });
    return;
  }
  trackOrderCurrent = result.data.order.slice();
  renderTrackOrderList();
}

trackOrderSourceSelect.addEventListener("change", loadTrackOrder);

document.getElementById("btnTrackOrderSave").addEventListener("click", async () => {
  const source = trackOrderSourceSelect.value;
  const result = await apiPost("/api/track_order/" + encodeURIComponent(source), { order: trackOrderCurrent });
  if (result.ok) showToast(t("trackorder.saved"));
  else showToast(t("common.failed"), errorLabel(result.error), { error: true });
});

document.getElementById("btnTrackOrderReset").addEventListener("click", async () => {
  if (!(await showConfirm(t("trackorder.confirm_reset")))) return;
  const source = trackOrderSourceSelect.value;
  const result = await apiDelete("/api/track_order/" + encodeURIComponent(source));
  if (!result.ok) {
    showToast(t("common.failed"), errorLabel(result.error), { error: true });
    return;
  }
  loadTrackOrder();
  showToast(t("trackorder.reset_done"));
});

loadTrackOrder();

function deviceUtcString(now) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${now.getUTCFullYear()}-${pad(now.getUTCMonth() + 1)}-${pad(now.getUTCDate())} ` +
    `${pad(now.getUTCHours())}:${pad(now.getUTCMinutes())}:${pad(now.getUTCSeconds())}`;
}

function clockWriteDetail(result) {
  const written = result.data && result.data.written_to_rtc;
  return [result.data && result.data.system_time, t(written ? "clock.rtc_written" : "clock.rtc_none")]
    .filter(Boolean).join(" · ");
}

document.getElementById("btnUseDeviceTime").addEventListener("click", async () => {
  const btn = document.getElementById("btnUseDeviceTime");
  const now = new Date();
  const utc = deviceUtcString(now);

  const pad = (n) => String(n).padStart(2, "0");
  document.getElementById("timeInput").value =
    `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}` +
    `T${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
  btn.disabled = true;
  const result = await apiPost("/api/time", { utc });
  btn.disabled = false;
  if (!result.ok) {
    showError(result.error, t("clock.sync_btn"));
    return;
  }
  showToast(t("clock.device_time_set"), clockWriteDetail(result));

  refreshStatus();
});

document.getElementById("manualTimeSection").addEventListener("toggle", (event) => {
  const input = document.getElementById("timeInput");
  if (!event.target.open || input.value) return;
  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  input.value = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}` +
    `T${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
});

document.getElementById("timeForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const raw = document.getElementById("timeInput").value;
  if (!raw) return;
  const formatted = raw.replace("T", " ");
  const result = await apiPost("/api/time", { datetime: formatted });
  if (result.ok) showToast(t("clock.time_set"), clockWriteDetail(result));
  else showError(result.error);
});

document.getElementById("btnSaveBtClock").addEventListener("click", async () => {
  const updates = collectChangedSettings(document.getElementById("btClockMac").closest(".subsection"));
  if (Object.keys(updates).length === 0) {
    showToast(t("alert.no_changes"));
    return;
  }

  if (updates.BT_CLOCK_MAC && !isCompleteMac(updates.BT_CLOCK_MAC)) {
    showToast(t("clock.mac_incomplete"));
    return;
  }
  const result = await apiPost("/api/settings", updates);
  if (result.ok) Object.assign(settingsBaseline, updates);
  if (result.ok) showToast(t("alert.bt_saved")); else showError(result.error);
});

document.getElementById("btnTestBtClock").addEventListener("click", async () => {
  const btn = document.getElementById("btnTestBtClock");
  const mac = btClockMac.value.trim();
  if (!mac) {
    showToast(t("clock.enter_mac_first"));
    return;
  }
  if (!isCompleteMac(mac)) {
    showToast(t("clock.mac_incomplete"));
    return;
  }
  if (btn.disabled) return;

  const setBusy = (busy) => {
    btn.disabled = busy;
    btn.dataset.i18n = busy ? "clock.testing" : "clock.test_device";
    btn.textContent = t(btn.dataset.i18n);
  };
  setBusy(true);

  showToast(t("clock.test_duration"));
  try {
    const result = await apiPost("/api/clock/test_bt", { mac });
    if (result.ok) showToast(t("clock.time_recovered", { time: result.time }));
    else showToast(t("common.failed"), errorLabel(result.error), { error: true });
  } finally {
    setBusy(false);
  }
});

let timezonesLoaded = false;
let timezoneCurrent = null;
let timezoneShown = null;
const UNSET_ZONES = ["", "n/a", "Factory"];

function browserTimeZone() {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch (e) { return ""; }
}

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
  if (dev.kind === "other") states.push(["speaker.state_not_audio", "badge"]);
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
  pairBtn.className = "btn";
  pairBtn.textContent = t("speaker.pair");

  pairBtn.disabled = dev.paired;
  pairBtn.addEventListener("click", async () => {
    pairBtn.disabled = true;

    const bubble = showToast(t("speaker.pair_wait_title"), t("speaker.pair_wait_hint"), { sticky: true });
    const result = await apiPost("/api/bluetooth/pair", { mac: dev.mac });
    dismissToast(bubble);
    pairBtn.disabled = dev.paired;
    if (!result.ok) showToolError(t("speaker.pair_failed"), result);
    else if (result.data && result.data.already) showToast(t("speaker.already_paired"));
    else showToast(t("speaker.paired_alert"));
    refreshScanList();
  });

  const speakerBtn = document.createElement("button");
  speakerBtn.className = "btn";
  speakerBtn.textContent = t("speaker.use_as_speaker");

  speakerBtn.disabled = dev.kind === "other";
  speakerBtn.addEventListener("click", async () => {
    speakerBtn.disabled = true;
    const result = await apiPost("/api/bluetooth/connect", { mac: dev.mac, set_as_speaker: true });
    speakerBtn.disabled = dev.kind === "other";
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

  splitActions(actions, [pairBtn], [speakerBtn, clockBtn]);
  li.appendChild(actions);
  return li;
}

function renderDevices(devices, running, blocked) {
  const list = document.getElementById("deviceList");

  const shown = devices.filter((dev) => dev.named || dev.paired || dev.connected ||
    dev.mac === lastKnownSpeakerMac);

  const signature = (running ? "run|" : "stop|") + (blocked ? "blocked|" : "") +
    shown.map((d) => [d.mac, d.name, d.paired, d.connected, d.kind, d.named].join("~")).join(",");
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

function updateStartTimeVisibility() {
  const select = document.getElementById("musicStartMode");
  document.getElementById("musicStartTimeRow").hidden = select.value !== "scheduled";

  document.getElementById("volumeFadeRow").hidden = document.getElementById("volumeChange").value !== "fade";
}
document.getElementById("volumeChange").addEventListener("change", updateStartTimeVisibility);

document.getElementById("musicStartMode").addEventListener("change", updateStartTimeVisibility);

function updateApPasswordRowVisibility() {
  const open = document.getElementById("apOpen").checked;
  document.getElementById("apPasswordRow").hidden = open;
  if (open) document.getElementById("apPassword").value = "";
}

document.getElementById("apOpen").addEventListener("change", updateApPasswordRowVisibility);

async function refreshApCard() {
  const result = await apiGet("/api/wifi/ap");
  const statusBox = document.getElementById("apStatus");
  if (!result.ok || !result.data.configured) {
    statusBox.textContent = t("ap.status_none");
    return;
  }
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
refreshEvery(refreshApCard, 10000);

document.getElementById("apForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const ssid = document.getElementById("apSsid").value.trim();
  const open = document.getElementById("apOpen").checked;
  const password = document.getElementById("apPassword").value;
  const btn = document.getElementById("apSaveBtn");

  if (btn.disabled) return;
  btn.disabled = true;
  try {
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
refreshEvery(refreshHomeWifi, 6000);

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

async function refreshSecurityCard() {
  const result = await apiGet("/api/auth/status");
  const statusBox = document.getElementById("securityStatus");
  const currentRow = document.getElementById("currentPasswordRow");
  const hasPassword = result.ok && result.data.auth_required;
  currentRow.hidden = !hasPassword;
  setLogoutVisible(!!(hasPassword && result.data.authenticated));
  statusBox.textContent = hasPassword ? t("security.status_set") : t("security.status_none");
}

refreshSecurityCard();

document.getElementById("securityForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const currentPassword = document.getElementById("currentPassword").value;
  const newPassword = document.getElementById("newPassword").value;
  const result = await apiPost("/api/auth/set_password", {
    current_password: currentPassword,
    new_password: newPassword,
  });
  if (!result.ok) {
    const messages = {
      wrong_current_password: t("security.wrong_current_password"),
      password_too_short: t("security.password_too_short"),
      too_many_attempts: t("login.locked", { s: result.retry_after || 0 }),
    };
    showToast(t("common.failed"), messages[result.error] || errorLabel(result.error), { error: true });
    return;
  }
  document.getElementById("securityForm").reset();
  refreshSecurityCard();
  showToast(result.password_set ? t("security.saved") : t("security.removed"));
});

function formatDuration(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  if (h > 0) return h + " " + t("unit.hour") + " " + String(m).padStart(2, "0") + " " + t("unit.min");
  if (m > 0) return m + " " + t("unit.min");
  return total + " " + t("unit.sec");
}

function formatDateTime(epochSeconds) {
  if (!epochSeconds) return "—";
  const d = new Date(Number(epochSeconds) * 1000);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString();
}

function formatTimeOnly(epochSeconds) {
  if (!epochSeconds) return "—";
  const d = new Date(Number(epochSeconds) * 1000);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function formatAgo(seconds) {
  const s = Math.max(0, Math.round(Number(seconds) || 0));
  if (s < 10) return t("clients.ago_now");
  if (s < 60) return t("clients.ago_seconds", { n: s });
  const minutes = Math.round(s / 60);
  if (minutes < 60) return t("clients.ago_minutes", { n: minutes });
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return t("clients.ago_hours", { n: hours });
  const days = Math.floor(hours / 24);
  if (days === 1) return t("clients.ago_yesterday", { time: formatTimeOnly(Date.now() / 1000 - s) });
  return t("clients.ago_days", { n: days });
}

function formatBytes(n) {
  const v = Number(n) || 0;
  if (v < 1024) return v + " B";
  if (v < 1024 * 1024) return (v / 1024).toFixed(1) + " KB";

  if (v < 1024 * 1024 * 1024) return (v / (1024 * 1024)).toFixed(1) + " MB";
  return (v / (1024 * 1024 * 1024)).toFixed(1) + " GB";
}

const EVENT_TYPE_KEYS = ["session_start", "session_end", "session_unclean", "shutdown",
  "clock_ready", "clock_unreliable", "clock_manual_set", "timezone_set", "click", "track_played",
  "meme_played", "announce_played", "playback_error", "playback_stalled",
  "speaker_disconnected", "speaker_reconnected", "speaker_silent", "ap_client_connected",
  "ap_client_disconnected", "web_session", "music_started", "music_list_stopped",
  "music_rescan", "track_order_changed", "track_order_reset",
  "cutoff_triggered", "volume_set", "settings_changed",
  "daemon_restart", "ssh_toggle", "home_wifi_toggle", "bluetooth_connect",
  "bluetooth_disconnect", "stats_reset",

  "update_started", "update_applied", "update_rolled_back",
  "announcement_type_added", "announcement_type_updated",
  "announcement_type_removed", "custom_announce_triggered",
  "ap_config_changed", "web_login", "web_password_changed",

  "track_skipped", "sound_skipped",
  "track_previous", "timed_pause", "sleep_timer", "loop_mode",
  "track_replayed", "device_banned", "device_unbanned", "output_override",

  "announce_file_added", "announce_file_removed", "announce_on_demand", "announce_skipped",
  "audio_restart", "bluetooth_pair", "config_exported", "config_imported", "config_reloaded",
  "counters_reset", "music_upload", "playback_pause", "portal_released", "stats_rows_deleted",
  "system_sound_off", "system_sound_reset", "system_sound_set", "track_queued", "device_free_credits",
  "device_renamed", "device_name_locked", "portal_reset", "device_forgotten",
  "devices_linked", "device_unlinked",
  "schedule_started", "schedule_ended", "schedule_stop",
  "schedule_added", "schedule_changed", "schedule_removed",
  "standby", "mute", "backup_exported", "backup_restored", "system_reboot",
  "flic_sdk_installed", "flic_enabled", "flic_disabled", "flic_button_removed", "home_wifi_autoconnect",
  "track_liked", "track_unliked", "track_hidden", "track_shown",
  "list_selected", "list_added", "list_changed", "list_removed",
  "announcement_volume_set"];
function eventLabel(type) {
  return EVENT_TYPE_KEYS.includes(type) ? t("event." + type) : type;
}

const CLOCK_SOURCE_KEYS = ["rtc", "bluetooth", "manual", "none"];
function clockSourceLabel(source) {
  return CLOCK_SOURCE_KEYS.includes(source) ? t("clocksrc." + source) : t("clocksrc.unknown");
}

const END_REASON_KEYS = ["cutoff", "long_press", "service_stop", "unclean", "speaker_lost", "speaker_absent",
  "button", "interface"];
function endReasonLabel(reason) {
  return END_REASON_KEYS.includes(reason) ? t("endreason." + reason) : reason;
}

const CLICK_ACTION_KEYS = ["sound_then_next", "next_track_no_sound", "start_from_idle",
  "announcement", "shutdown", "ignored", "previous_track", "sound_then_previous",
  "sound_then_resume", "paused", "resumed", "loop_track", "loop_album", "loop_off",
  "volume_up", "volume_down", "sleep_on", "sleep_off", "standby", "muted", "unmuted"];
function clickActionLabel(action) {
  return CLICK_ACTION_KEYS.includes(action) ? t("clickaction." + action) : action;
}

let oldestEventId = null;

function kpiTile(icon, label, value, sub, opts) {
  const options = opts || {};
  const tile = document.createElement("div");
  tile.className = "kpi" + (options.hero ? " kpi-hero" : "");

  const i = document.createElement("div");
  i.className = "kpi-icon" + (options.warn ? " warn" : "");
  i.dataset.icon = icon;
  i.setAttribute("aria-hidden", "true");

  const body = document.createElement("div");
  body.className = "kpi-body";
  const v = document.createElement("div");
  v.className = "kpi-value";
  v.textContent = value;
  const l = document.createElement("div");
  l.className = "kpi-label";
  l.textContent = label;
  body.append(v, l);
  if (sub) {
    const s = document.createElement("div");
    s.className = "kpi-sub";
    s.textContent = sub;
    body.appendChild(s);
  }

  tile.append(i, body);
  if (options.keys) armKpiReset(tile, label, options.keys);
  return tile;
}

const KPI_HOLD_MS = 700;

function armKpiReset(tile, label, keys) {
  tile.tabIndex = 0;
  tile.classList.add("kpi-resettable");
  tile.setAttribute("aria-description", t("stats.kpi_hold_hint"));
  let timer = null;
  let start = null;
  const cancel = () => {
    clearTimeout(timer);
    timer = null;
    tile.classList.remove("kpi-holding");
  };
  const ask = async () => {
    cancel();
    if (!(await showConfirm(t("stats.kpi_reset_confirm", { name: label })))) return;
    const r = await apiPost("/api/journal/reset_counters", { keys });
    if (!r.ok) {
      showError(r.error);
      return;
    }
    showToast(t("stats.kpi_reset_done", { name: label }));
    refreshStats(true);
  };
  tile.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    start = { x: e.clientX, y: e.clientY };
    tile.style.setProperty("--hold-ms", KPI_HOLD_MS + "ms");
    tile.classList.add("kpi-holding");
    timer = setTimeout(ask, KPI_HOLD_MS);
  });
  tile.addEventListener("pointermove", (e) => {
    if (timer && start && Math.hypot(e.clientX - start.x, e.clientY - start.y) > 10) cancel();
  });
  ["pointerup", "pointerleave", "pointercancel"].forEach((type) => tile.addEventListener(type, cancel));

  tile.addEventListener("contextmenu", (e) => e.preventDefault());
  tile.addEventListener("keydown", (e) => {
    if (e.key === "Delete" || e.key === "Backspace") {
      e.preventDefault();
      ask();
    }
  });
}

function renderKpis(summary) {
  const placeholder = !summary;
  summary = summary || {};
  const keysIf = (keys) => (placeholder ? undefined : keys);
  const c = summary.counters || {};
  const grid = document.getElementById("kpiGrid");
  grid.innerHTML = "";

  const totalListening =
    (c.seconds_music || 0) + (c.seconds_meme || 0) + (c.seconds_announce || 0);
  const totalClicks =
    (c.clicks_single || 0) + (c.clicks_double || 0) + (c.clicks_long || 0) + (c.clicks_speaker || 0);

  const errorCount = Math.round(c.playback_errors || 0);
  const speakerLostCount = Math.round(c.speaker_drops || 0);

  grid.append(
    kpiTile("headphones", t("kpi.music_listened"), formatDuration(c.seconds_music),
      t("kpi.music_listened_sub", { total: formatDuration(totalListening) }), { hero: true, keys: keysIf(["seconds_music", "seconds_meme", "seconds_announce"]) }),
    kpiTile("music", t("kpi.tracks_played"), Math.round(c.tracks_played || 0),
      t("kpi.tracks_played_sub", { memes: Math.round(c.memes_played || 0), announcements: Math.round(c.announcements_played || 0) }), { keys: keysIf(["tracks_played", "memes_played", "announcements_played"]) }),
    kpiTile("pointer", t("kpi.button_presses"), Math.round(totalClicks),
      t("kpi.button_presses_sub", { single: Math.round(c.clicks_single || 0), double: Math.round(c.clicks_double || 0), long: Math.round(c.clicks_long || 0), speaker: Math.round(c.clicks_speaker || 0) }), { keys: keysIf(["clicks_single", "clicks_double", "clicks_long", "clicks_speaker"]) }),
    kpiTile("antenna", t("kpi.click_sources"),
      Math.round(c.clicks_flic || 0) + " / " + Math.round(c.clicks_gpio || 0) + " / " + Math.round(c.clicks_web || 0),
      t("kpi.click_sources_sub", { ignored: Math.round(c.clicks_ignored || 0) }), { keys: keysIf(["clicks_flic", "clicks_gpio", "clicks_web", "clicks_ignored"]) }),
    kpiTile("play", t("kpi.startups"), Math.round(c.sessions_started || 0),
      t("kpi.startups_sub", { used: Math.round(c.sessions_used || 0), unclean: Math.round(c.sessions_unclean || 0) }), { keys: keysIf(["sessions_started", "sessions_used", "sessions_unclean"]) }),
    kpiTile("power", t("kpi.shutdowns"), Math.round((c.shutdowns_cutoff || 0) + (c.shutdowns_longpress || 0) + (c.shutdowns_speaker || 0)),
      t("kpi.shutdowns_sub", { scheduled: Math.round(c.shutdowns_cutoff || 0), longpress: Math.round(c.shutdowns_longpress || 0), speaker: Math.round(c.shutdowns_speaker || 0) }), { keys: keysIf(["shutdowns_cutoff", "shutdowns_longpress", "shutdowns_service", "shutdowns_speaker"]) }),
    kpiTile("alert", t("kpi.playback_errors"), errorCount,
      t("kpi.playback_errors_sub", { n: Math.round(c.playback_stalls || 0) }), { warn: errorCount > 0, keys: keysIf(["playback_errors", "playback_stalls"]) }),
    kpiTile("speaker", t("kpi.speaker_lost"), speakerLostCount,
      t("kpi.speaker_lost_sub", { n: Math.round(c.speaker_recoveries || 0) }), { warn: speakerLostCount > 0, keys: keysIf(["speaker_drops", "speaker_recoveries"]) }),
    kpiTile("wifi", t("kpi.access_point"), Math.round(c.ap_client_connections || 0),
      t("kpi.access_point_sub", { n: Math.round(c.web_sessions || 0) }), { keys: keysIf(["ap_client_connections", "web_sessions"]) }),
    kpiTile("clock", t("kpi.time_established"), t("kpi.time_established_value", { n: Math.round(c.clock_rtc || 0) }),
      t("kpi.time_established_sub", { bt: Math.round(c.clock_bluetooth || 0), manual: Math.round(c.clock_manual || 0), failed: Math.round(c.clock_unreliable || 0) }), { keys: keysIf(["clock_rtc", "clock_bluetooth", "clock_manual", "clock_unreliable"]) }),
  );

  if (placeholder) {
    grid.querySelectorAll(".kpi-value").forEach((v) => { v.textContent = "\u2013"; });
    grid.querySelectorAll(".kpi-sub").forEach((v) => { v.textContent = "\u00a0"; });
    return;
  }

  const period = document.getElementById("statsPeriod");
  const from = summary.reset_at || summary.created_at || summary.since;
  period.textContent = t("stats.since_prefix", {
    date: formatDateTime(from),
    count: summary.event_count || 0,
    days: Math.round(summary.retention_days || 0),
    size: formatBytes(summary.db_size_bytes),
  });
}

let chartDaySelected = null;

function renderChartDay(series) {
  const panel = document.getElementById("chartDay");
  const day = chartDaySelected && (series || []).find((d) => d.day === chartDaySelected);
  panel.hidden = !day;
  if (!day) {
    chartDaySelected = null;
    return;
  }
  const date = new Date(day.day + "T12:00:00");
  const title = document.createElement("h3");
  title.textContent = new Intl.DateTimeFormat(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric" }).format(date);
  const close = document.createElement("button");
  close.type = "button";
  close.className = "btn btn-icon btn-small";
  close.dataset.icon = "x";
  close.setAttribute("aria-label", t("common.close"));
  close.addEventListener("click", () => {
    chartDaySelected = null;
    renderDailyChart(series);
  });
  const head = document.createElement("div");
  head.className = "chart-day-head";
  head.append(title, close);
  const rows = [
    ["stats.r_music", formatDuration(day.seconds_music)],
    ["stats.r_sounds", formatDuration(day.seconds_meme)],
    ["stats.r_announces", formatDuration(day.seconds_announce)],
    ["stats.r_tracks", Math.round(day.tracks_played || 0)],
    ["stats.r_clicks", Math.round(day.clicks || 0)],
    ["stats.r_errors", Math.round(day.playback_errors || 0)],
    ["stats.r_startups", Math.round(day.sessions_started || 0)],
    ["stats.r_web", Math.round(day.web_sessions || 0)],
  ];
  const dl = document.createElement("dl");
  rows.forEach(([key, value]) => {
    const wrap = document.createElement("div");
    const dt = document.createElement("dt");
    dt.textContent = t(key);
    const dd = document.createElement("dd");
    dd.textContent = value;
    wrap.append(dt, dd);
    dl.append(wrap);
  });
  panel.replaceChildren(head, dl);
}

function renderDailyChart(series) {
  const box = document.getElementById("dailyChart");
  box.innerHTML = "";
  if (!series || !series.length) {
    box.textContent = t("stats.no_data");
    renderChartDay(null);
    return;
  }
  const max = Math.max(...series.map((d) => d.seconds_music), 1);

  const step = Math.max(1, Math.ceil(series.length / 7));

  series.forEach((day, i) => {
    const col = document.createElement("div");
    col.className = "chart-col";
    col.title = t("chart.tooltip", { date: day.day, duration: formatDuration(day.seconds_music), tracks: Math.round(day.tracks_played) }) +
      (day.playback_errors ? t("chart.tooltip_errors", { n: Math.round(day.playback_errors) }) : "");

    col.setAttribute("role", "button");
    col.tabIndex = 0;
    col.setAttribute("aria-label", col.title);
    col.dataset.day = day.day;
    const selected = chartDaySelected === day.day;
    col.classList.toggle("is-selected", selected);
    col.setAttribute("aria-pressed", selected ? "true" : "false");
    const pick = () => {
      chartDaySelected = chartDaySelected === day.day ? null : day.day;
      renderDailyChart(series);
    };
    col.addEventListener("click", pick);
    col.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        pick();
      }
    });

    const bar = document.createElement("div");
    bar.className = "chart-bar";

    const pct = day.seconds_music > 0 ? Math.max(4, (day.seconds_music / max) * 100) : 0;
    bar.style.height = pct + "%";
    if (day.playback_errors > 0) bar.classList.add("has-errors");

    const label = document.createElement("div");
    label.className = "chart-label" + ((series.length - 1 - i) % step ? " minor" : "");
    label.textContent = day.day.slice(8) + "/" + day.day.slice(5, 7);
    label.setAttribute("aria-hidden", "true");

    col.append(bar, label);
    box.appendChild(col);
  });
  renderChartDay(series);
}

function renderTopList(container, title, items, opts) {
  const options = opts || {};
  const block = document.createElement("div");
  block.className = "top-block";

  const h = document.createElement("div");
  h.className = "top-title";
  h.textContent = title;
  block.appendChild(h);

  if (!items || !items.length) {
    const empty = document.createElement("div");
    empty.className = "top-empty";
    empty.textContent = t("stats.nothing_recorded");
    block.appendChild(empty);
    container.appendChild(block);
    return;
  }

  const list = document.createElement("ul");
  list.className = "top-list";
  items.forEach((item) => {
    const li = document.createElement("li");

    const kind = options.kind === "custom_announce"
      ? "custom:" + item.kind
      : options.kind;
    li.append(selectionBox("items", JSON.stringify([kind, item.name])));
    const name = document.createElement("span");
    name.className = "top-name";
    name.textContent = item.name;
    const value = document.createElement("span");
    value.className = "top-value";
    value.textContent = options.showSeconds
      ? Math.round(item.count) + "x · " + formatDuration(item.seconds)
      : Math.round(item.count) + "x";
    li.append(name, value);
    list.appendChild(li);
  });
  block.appendChild(list);
  container.appendChild(block);
}

function listInUse(id) {
  const box = document.getElementById(id);
  return !!box && (box.dataset.extended === "1" || !!box.querySelector(".row-select:checked"));
}

function renderTops(summary, force) {
  const top = summary.top || {};
  const box = document.getElementById("topsBox");
  if (force || !listInUse("topsBox")) renderTopsInto(box, top);
  const errorsBox = document.getElementById("errorsBox");
  if (force || !listInUse("errorsBox")) {
    errorsBox.innerHTML = "";
    renderTopList(errorsBox, t("top.errors"), top.error, { kind: "error" });
  }
}

function renderTopsInto(box, top) {
  box.innerHTML = "";
  renderTopList(box, t("top.music"), top.music, { showSeconds: true, kind: "music" });
  renderTopList(box, t("top.cutoff"), top.cutoff_announce, { kind: "cutoff_announce" });
  renderTopList(box, t("top.meme"), top.meme, { kind: "meme" });

  renderTopList(box, t("top.custom"), top.custom_announce, { kind: "custom_announce" });
}

const SESSIONS_PAGE = 50;
let sessionsOldestId = null;

function renderSessions(summary, force) {
  const box = document.getElementById("sessionList");
  if (!force && listInUse("sessionList")) return;
  box.innerHTML = "";
  box.dataset.extended = "";
  const sessions = (summary.sessions && summary.sessions.recent) || [];
  sessionsOldestId = sessions.length ? sessions[sessions.length - 1].id : null;
  if (!sessions.length) {
    box.textContent = t("stats.no_startup");
    updateMoreSessions();
    return;
  }
  sessions.forEach((s) => box.appendChild(sessionRow(s)));
  updateMoreSessions();
}

function updateMoreSessions() {
  const loaded = document.querySelectorAll("#sessionList .row-select").length;
  document.getElementById("moreSessionsRow").hidden =
    !(sessionsOldestId !== null && remoteTotals.sessions > loaded);
}

document.getElementById("btnMoreSessions").addEventListener("click", async () => {
  const btn = document.getElementById("btnMoreSessions");
  btn.disabled = true;
  const result = await apiGet("/api/journal/sessions?limit=" + SESSIONS_PAGE +
                              (sessionsOldestId !== null ? "&before_id=" + sessionsOldestId : ""));
  btn.disabled = false;
  if (!result.ok || !result.data) return;
  const box = document.getElementById("sessionList");
  const rows = result.data.sessions || [];
  rows.forEach((s) => box.appendChild(sessionRow(s)));
  if (rows.length) {
    sessionsOldestId = rows[rows.length - 1].id;
    box.dataset.extended = "1";
  }
  if (rows.length < SESSIONS_PAGE) sessionsOldestId = null;
  updateMoreSessions();
  updateDeleteButtons();
});

function sessionRow(s) {
  const row = document.createElement("div");
  row.className = "session-row";

  const when = document.createElement("div");
  when.className = "session-when";
  when.textContent = formatDateTime(s.started_at);
  if (!s.clock_ok) when.classList.add("unreliable");

  const detail = document.createElement("div");
  detail.className = "session-detail";
  const parts = [];
  if (s.ended_at) {
    parts.push(t("session.until", { time: formatTimeOnly(s.ended_at) }));
    const duration = Number(s.ended_at) - Number(s.started_at);
    if (duration > 0) parts.push(formatDuration(duration));
  } else {
    parts.push(t("session.in_progress"));
  }
  parts.push(s.end_reason ? endReasonLabel(s.end_reason) : "—");
  parts.push(t("session.clock_prefix", { source: clockSourceLabel(s.clock_source) }));
  if (!s.used) parts.push(t("session.never_used"));
  detail.textContent = parts.join(" · ");

  row.append(s.ended_at ? selectionBox("sessions", s.id) : selectionSpacer(),
             when, detail);
  if (s.end_reason === "unclean") row.classList.add("session-unclean");
  return row;
}

function describeEvent(event) {
  const d = event.detail || {};
  switch (event.type) {
    case "click": {
      const source = d.source === "flic" ? t("evt.source_flic")
        : d.source === "gpio" ? t("evt.source_gpio")
        : d.source === "web" ? t("evt.source_web")
        : d.source === "speaker" ? t("evt.source_speaker") : t("evt.source_unknown");
      const speakerGesture = /^speaker_(playpause|next|previous)$/.exec(event.label || "");
      const kind = event.label === "single" ? t("evt.click_single")
        : event.label === "double" ? t("evt.click_double")
        : event.label === "long" ? t("evt.click_long")
        : speakerGesture ? t("speakerbtn." + speakerGesture[1]) : event.label;
      let text = t("evt.click_desc", { kind, source, action: clickActionLabel(d.action) || d.action || "—" });
      if (d.target) text += t("evt.click_target", { target: d.target });
      return text;
    }
    case "track_played":
    case "meme_played":
    case "announce_played":
      return t("evt.played_desc", { label: eventLabel(event.type), name: event.label || "—", duration: formatDuration(d.seconds) });
    case "session_start": {
      const pending = !event.label || event.label === "pending" || event.label === "clock pending";
      return t("evt.session_start_desc", { source: pending ? t("clocksrc.pending") : clockSourceLabel(event.label) });
    }
    case "stats_rows_deleted": {
      const lists = { events: "stats.eventlog_section", sessions: "stats.sessions_section",
        played: "stats.most_played_section", errors: "stats.errors_section", items: "evt.rows_items" };
      const list = lists[event.label] ? t(lists[event.label]) : (event.label || "—");
      return t("evt.rows_deleted_desc", { n: Math.round(d.count || 0), list })
        + (d.all ? " (" + t("evt.rows_all") + ")" : "");
    }
    case "playback_pause":
      return clickActionLabel(event.label === "paused" ? "paused" : "resumed");
    case "playback_error":
      return t("evt.playback_error_desc", { name: event.label || "—", error: d.error || t("common.unknown_error") });
    case "clock_ready":
      return t("evt.clock_ready_desc", { source: clockSourceLabel(event.label) }) +
        (d.offset_sec ? t("evt.clock_ready_offset", { n: Math.round(d.offset_sec) }) : "");
    case "clock_unreliable":
      return t("evt.clock_unreliable_desc");
    case "update_applied":
      return t("evt.update_applied_desc", {
        from: d.from || "?", to: d.to || "?", source: event.label || "?",
      });
    case "update_rolled_back":
      return t("evt.update_rolled_back_desc", { backup: event.label || "?" });
    case "session_end":
    case "shutdown":
      return t("evt.session_end_desc", { label: eventLabel(event.type), reason: event.label ? endReasonLabel(event.label) : "—" });
    default: {
      const base = eventLabel(event.type);

      const label = event.who ? event.who + (event.label ? " (" + event.label + ")" : "") : event.label;
      return label ? t("evt.default_with_label", { base, label }) : base;
    }
  }
}

function renderEvents(events, append) {
  const list = document.getElementById("eventList");
  if (!append) list.innerHTML = "";

  if (!events.length && !append) {
    const li = document.createElement("li");
    li.className = "event-empty";
    li.textContent = t("stats.no_event");
    list.appendChild(li);
    return;
  }

  events.forEach((event) => {
    const li = document.createElement("li");
    li.className = "event-row";
    if (event.type === "playback_error" || event.type === "playback_stalled" ||
        event.type === "speaker_disconnected" || event.type === "session_unclean" ||
        event.type === "clock_unreliable") {
      li.classList.add("event-warn");
    }

    const time = document.createElement("span");
    time.className = "event-time";
    time.textContent = formatDateTime(event.ts);

    if (!event.clock_ok) {
      time.classList.add("unreliable");
      time.title = t("stats.recorded_before_clock_title");
      time.textContent = "~ " + time.textContent;
    }

    const text = document.createElement("span");
    text.className = "event-text";
    text.textContent = describeEvent(event);

    li.append(selectionBox("events", event.id), time, text);
    list.appendChild(li);
    oldestEventId = event.id;
  });
}

async function loadEvents(append) {
  const type = document.getElementById("eventTypeFilter").value;
  let url = "/api/journal/entries?limit=60";
  if (type) url += "&type=" + encodeURIComponent(type);
  if (append && oldestEventId !== null) url += "&before_id=" + oldestEventId;

  const result = await apiGet(url);
  if (!result.ok) return;

  const filter = document.getElementById("eventTypeFilter");
  const current = filter.value;
  if (filter.options.length - 1 !== result.data.types.length) {
    filter.innerHTML = "";
    const allOpt = document.createElement("option");
    allOpt.value = "";
    allOpt.textContent = t("stats.all_events");
    filter.appendChild(allOpt);
    result.data.types.forEach((eventType) => {
      const opt = document.createElement("option");
      opt.value = eventType;
      opt.textContent = eventLabel(eventType);
      filter.appendChild(opt);
    });
    filter.value = current;
  }

  remoteTotals.events = Number(result.data.total) || 0;
  if (!append) remoteSelected.eventList = false;
  renderEvents(result.data.events, append);
  document.getElementById("btnMoreEvents").hidden = result.data.events.length < 60;
  updateDeleteButtons();
}

async function refreshStats(force) {
  force = !!force;
  const result = await apiGet("/api/journal/summary");
  const hint = document.getElementById("statsHint");
  if (!result.ok || !result.data || !result.data.enabled) {
    hint.textContent = t("stats.disabled");
    return;
  }
  hint.textContent = "";
  const totals = result.data.totals || {};
  remoteTotals.played = Number(totals.played) || 0;
  remoteTotals.errors = Number(totals.errors) || 0;
  remoteTotals.sessions = Number(totals.sessions) || 0;
  renderKpis(result.data);
  renderTops(result.data, force);
  renderSessions(result.data, force);
  updateDeleteButtons();

  const days = document.getElementById("statsDays").value;
  const daily = await apiGet("/api/journal/daily?days=" + days);
  if (daily.ok) renderDailyChart(daily.data);
  const week = days === "7" ? daily : await apiGet("/api/journal/daily?days=7");
  if (week.ok && Array.isArray(week.data)) renderRecentStats(week.data);
}

const SETUP_TARGETS = {
  storage: ["system", "healthTitle", "chip"],
  speaker: ["audio", "btTitle", "bluetooth"],
  clock: ["system", "clockTitle", "clock"],
  timezone: ["system", "clockTitle", "globe"],
  music: ["home", "musicTitle", "music"],
  password: ["system", "securityTitle", "key"],
  ap_open: ["network", "apTitle", "wifi"],
};
let setupHidden = [];

function goToCard(tab, titleId) {
  const title = titleId ? document.getElementById(titleId) : null;
  const card = title && title.closest(".card[data-page]");
  setActiveView(tab, card ? card.dataset.page : null);
  if (card) setTimeout(() => card.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
}

async function saveSetupHidden(list) {
  const result = await apiPost("/api/settings", { SETUP_HIDDEN: list.join(",") });
  if (!result.ok) showError(result.error);
  refreshSetup();
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
  document.getElementById("setupList").replaceChildren(...items.map((id) => {
    const [tab, titleId, icon] = SETUP_TARGETS[id];
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
  }));
  card.hidden = !items.length;
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
    document.getElementById("shareWifiText").textContent = t("share.wifi_text",
      { ssid: d.ssid, password: d.open ? t("share.no_password") : d.password });
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
    if (s.read_only) lines.push(t("storage.read_only"));
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

document.getElementById("btnAudioDiag").addEventListener("click", async () => {
  const btn = document.getElementById("btnAudioDiag");
  btn.disabled = true;
  const result = await apiPost("/api/diag/audio", {});
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
    title: t("diag.title"),
    bodyNode: report,
    modalClass: "modal-report",
    choices: [{ label: t("diag.copy"), value: "copy" }],
  });
  if (choice === "copy") copyText(text, report);
});

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
document.getElementById("btnMoreEvents").addEventListener("click", () => loadEvents(true));

document.getElementById("btnStatsExport").addEventListener("click", () => {
  window.location.href = "/api/journal/export";
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
});

refreshStats();

refreshEvery(refreshStats, 30000);

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
  const SOURCE_KEYS = { usb: "update.source_usb", git: "update.source_git", release: "update.source_release",
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

  const box = document.getElementById("gitUpdateBox");
  box.hidden = !(d.git_configured && d.web_updates_allowed);
  if (!box.hidden) {
    document.getElementById("gitRepoDisplay").textContent =
      d.git_url + (d.git_branch ? " (" + d.git_branch + ")" : "");
  }

  const logBox = document.getElementById("updateLog");
  const hint = document.getElementById("gitUpdateHint");
  if (d.log_tail) {
    logBox.hidden = false;
    logBox.textContent = d.log_tail;
    logBox.scrollTop = logBox.scrollHeight;
  }
  const btn = document.getElementById("btnUpdateGit");
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

document.getElementById("btnUpdateGit").addEventListener("click", async () => {
  if (!(await showConfirm(t("update.confirm_git")))) return;

  const hint = document.getElementById("gitUpdateHint");
  hint.textContent = t("update.starting");
  const result = await apiPost("/api/update/git");
  if (!result.ok) {
    hint.textContent = t("common.failed_prefix", { error: errorLabel(result.error) });
    return;
  }
  document.getElementById("btnUpdateGit").disabled = true;

  if (updatePollTimer) clearInterval(updatePollTimer);
  updatePollTimer = setInterval(refreshUpdate, 3000);
  updateAwaitingStart = Date.now();
  refreshUpdate();
});

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
    });

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
    const bytes = (data.files || []).reduce((sum, f) => sum + (Number(f.size) || 0), 0);
    line.textContent = t("music.library_line", {
      count: count, size: formatBytes(bytes), free: formatBytes(musicManifestFree),
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

  const otherKey = gpioTargetInput && gpioTargetInput.dataset.key === "GPIO_BUTTON_PIN"
    ? "GPIO_RESET_PIN" : "GPIO_BUTTON_PIN";
  const otherPin = data.assigned ? parseInt(data.assigned[otherKey], 10) : NaN;
  const otherName = otherKey === "GPIO_RESET_PIN"
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

function closeGpioPicker() {
  gpioDetectAbort = true;
  document.getElementById("gpioOverlay").hidden = true;
}

async function openGpioPicker(input) {
  gpioTargetInput = input;
  gpioDetectAbort = false;
  const status = document.getElementById("gpioStatus");
  status.textContent = "";
  const data = await loadPinout();
  if (!data) {
    showToast(t("common.failed"), t("gpio.load_failed"), { error: true });
    return;
  }
  renderPinHeader(data);

  document.getElementById("gpioDetectBtn").hidden = !data.supported;
  document.getElementById("gpioOverlay").hidden = false;
}

document.querySelectorAll(".pin-pick-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    openGpioPicker(document.getElementById(btn.dataset.pinTarget));
  });
});

document.getElementById("gpioCancel").addEventListener("click", closeGpioPicker);
document.getElementById("gpioOverlay").addEventListener("click", (e) => {
  if (e.target === e.currentTarget) closeGpioPicker();
});

document.getElementById("gpioDetectBtn").addEventListener("click", async () => {
  const btn = document.getElementById("gpioDetectBtn");
  const status = document.getElementById("gpioStatus");
  if (btn.disabled) return;
  btn.disabled = true;
  status.textContent = t("gpio.detect_waiting");
  try {
    const result = await apiPost("/api/gpio/detect");

    if (gpioDetectAbort) return;
    if (result.ok && result.data) {
      status.textContent = t("gpio.detect_found", {
        bcm: result.data.bcm, physical: result.data.physical,
      });
      selectPin(result.data.bcm);
      return;
    }
    const messages = {
      timeout: t("gpio.detect_timeout"),
      not_supported: t("gpio.detect_unsupported"),
      detect_in_progress: t("gpio.detect_busy"),
      no_pins_available: t("gpio.detect_no_pins"),
    };
    status.textContent = messages[result.error] || t("common.failed_prefix", { error: errorLabel(result.error) });
  } finally {
    btn.disabled = false;
  }
});

const guestAccessForm = document.getElementById("guestAccessForm");

guestAccessForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const updates = {};
  guestAccessForm.querySelectorAll("[data-key]").forEach((el) => {
    updates[el.dataset.key] = collectFieldValue(el);
  });
  const result = await apiPost("/api/settings", updates);
  if (result.ok) {
    Object.assign(settingsBaseline, updates);
    showToast(t("alert.settings_saved"));
    refreshSuggestions();
  } else {
    showError(result.error);
  }
});

const gpioResetForm = document.getElementById("gpioResetForm");

gpioResetForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const updates = {};
  gpioResetForm.querySelectorAll("[data-key]").forEach((el) => {
    updates[el.dataset.key] = collectFieldValue(el);
  });
  const result = await apiPost("/api/settings", updates);
  if (result.ok) showToast(t("security.gpio_saved"));
  else showError(result.error);
  if (result.ok) {
    gpioPinout = null;
    Object.assign(settingsBaseline, updates);
  }
});

function selectionBox(target, key) {
  const box = document.createElement("input");
  box.type = "checkbox";
  box.className = "row-select";
  box.dataset.target = target;
  box.dataset.key = String(key);
  box.addEventListener("change", updateDeleteButtons);

  queueMicrotask(() => {
    const row = box.parentElement;
    const text = row ? row.textContent.replace(/\s+/g, " ").trim() : "";
    if (text) box.setAttribute("aria-label", text.slice(0, 120));
  });
  return box;
}

function selectionSpacer() {
  const span = document.createElement("span");
  span.className = "row-select-spacer";
  return span;
}

function selectedIn(container) {
  return [...container.querySelectorAll(".row-select:checked")];
}

function updateSelectButtons() {
  document.querySelectorAll("[data-select-scope]").forEach((btn) => {
    const container = document.getElementById(btn.dataset.selectScope);
    if (!container) return;
    const boxes = [...container.querySelectorAll(".row-select")];
    const all = boxes.length > 0 && boxes.every((b) => b.checked);
    btn.disabled = boxes.length === 0;
    btn.dataset.i18n = all ? "stats.deselect_all" : "stats.select_all";
    btn.textContent = t(btn.dataset.i18n);
  });
}

const REMOTE_SCOPE_OF = { topsBox: "played", errorsBox: "errors", sessionList: "sessions", eventList: "events" };
const remoteTotals = { played: 0, errors: 0, sessions: 0, events: 0 };
const remoteSelected = { topsBox: false, errorsBox: false, sessionList: false, eventList: false };

function remoteTotalFor(containerId) {
  return remoteTotals[REMOTE_SCOPE_OF[containerId]] || 0;
}

function updateRemoteOffer(containerId) {
  const line = document.getElementById("selectRemote-" + containerId);
  const container = document.getElementById(containerId);
  if (!line || !container) return;
  const boxes = [...container.querySelectorAll(".row-select")];
  const allTicked = boxes.length > 0 && boxes.every((b) => b.checked);
  const total = remoteTotalFor(containerId);
  if (!allTicked || total <= boxes.length) {
    remoteSelected[containerId] = false;
    line.hidden = true;
    return;
  }
  line.hidden = false;
  const text = line.querySelector(".select-remote-text");
  const link = line.querySelector("[data-remote-scope]");
  if (remoteSelected[containerId]) {
    text.textContent = t("stats.all_n_selected", { n: total });
    link.textContent = t("stats.clear_selection");
  } else {
    text.textContent = t("stats.shown_selected", { n: boxes.length });
    link.textContent = t("stats.select_all_n", { n: total });
  }
}

document.querySelectorAll("[data-remote-scope]").forEach((link) => {
  link.addEventListener("click", () => {
    const id = link.dataset.remoteScope;
    if (remoteSelected[id]) {
      remoteSelected[id] = false;
      document.getElementById(id).querySelectorAll(".row-select").forEach((b) => { b.checked = false; });
    } else {
      remoteSelected[id] = true;
    }
    updateDeleteButtons();
  });
});

function updateDeleteButtons() {
  Object.keys(REMOTE_SCOPE_OF).forEach(updateRemoteOffer);
  document.querySelectorAll("[data-delete-scope]").forEach((btn) => {
    const id = btn.dataset.deleteScope;
    const container = document.getElementById(id);
    if (!container) return;
    const n = remoteSelected[id] ? remoteTotalFor(id) : selectedIn(container).length;
    btn.disabled = n === 0;
    btn.textContent = n ? t("stats.delete_n", { n }) : t("stats.delete_selected");
  });
  updateSelectButtons();
}

document.querySelectorAll("[data-select-scope]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const container = document.getElementById(btn.dataset.selectScope);
    if (!container) return;
    const boxes = [...container.querySelectorAll(".row-select")];
    const all = boxes.length > 0 && boxes.every((b) => b.checked);
    boxes.forEach((b) => { b.checked = !all; });
    updateDeleteButtons();
  });
});

async function deleteAllOf(btn, containerId) {
  const total = remoteTotalFor(containerId);
  if (!(await showConfirm(t("stats.confirm_delete", { n: total })))) return;
  btn.disabled = true;
  const body = { all: true, scope: REMOTE_SCOPE_OF[containerId] };
  if (containerId === "eventList") body.type = document.getElementById("eventTypeFilter").value || null;
  const result = await apiPost("/api/journal/delete", body);
  remoteSelected[containerId] = false;
  if (!result.ok) {
    showError(result.error);
    updateDeleteButtons();
    return;
  }
  showToast(t("stats.deleted"), t("stats.deleted_n", { n: (result.data && result.data.removed) || 0 }));
  if (containerId === "eventList") {
    oldestEventId = null;
    await loadEvents(false);
  }
  await refreshStats(true);
  updateDeleteButtons();
}

async function deleteSelected(btn) {
  const container = document.getElementById(btn.dataset.deleteScope);
  if (remoteSelected[btn.dataset.deleteScope]) {
    await deleteAllOf(btn, btn.dataset.deleteScope);
    return;
  }
  const boxes = selectedIn(container);
  if (!boxes.length) return;

  const target = boxes[0].dataset.target;
  const keys = boxes.map((b) => (target === "items" ? JSON.parse(b.dataset.key) : b.dataset.key));

  if (!(await showConfirm(t("stats.confirm_delete", { n: keys.length })))) return;

  btn.disabled = true;
  const result = await apiPost("/api/journal/delete", { target, keys });
  if (!result.ok) {
    showError(result.error);
    updateDeleteButtons();
    return;
  }
  const removed = (result.data && result.data.removed) || 0;

  if (removed < keys.length) {
    showToast(t("stats.deleted"), t("stats.delete_partial", { removed, asked: keys.length }));
  }

  if (target === "events") {
    oldestEventId = null;
    await loadEvents(false);
  } else {
    await refreshStats(true);
  }
  updateDeleteButtons();
}

document.querySelectorAll("[data-delete-scope]").forEach((btn) => {
  btn.addEventListener("click", () => deleteSelected(btn));
});
updateDeleteButtons();

function helpSectionOf(paragraph) {
  return paragraph.closest("details.subsection") || paragraph.closest("section.card");
}

function helpHeadingOf(section) {
  if (!section) return null;
  return section.tagName === "DETAILS"
    ? section.querySelector(":scope > summary")
    : section.querySelector(":scope > h2");
}

function initHelpToggles() {
  const sections = new Map();
  document.querySelectorAll("p.hint.help-text").forEach((p) => {
    const section = helpSectionOf(p);
    if (!section) return;
    if (!sections.has(section)) sections.set(section, []);
    sections.get(section).push(p);
  });

  sections.forEach((paragraphs, section) => {
    const heading = helpHeadingOf(section);
    if (!heading || heading.querySelector(".help-btn")
        || (section.parentElement && section.parentElement.classList.contains("subsection-wrap"))) return;

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "help-btn";

    btn.textContent = "?";
    btn.setAttribute("aria-expanded", "false");
    btn.title = t("help.show");
    btn.setAttribute("aria-label", t("help.show"));

    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      const showing = paragraphs.some((p) => p.hidden);
      paragraphs.forEach((p) => { p.hidden = !showing; });
      btn.classList.toggle("is-on", showing);
      btn.setAttribute("aria-expanded", showing ? "true" : "false");
      const label = showing ? t("help.hide") : t("help.show");
      btn.title = label;
      btn.setAttribute("aria-label", label);

      if (showing && section.tagName === "DETAILS") section.open = true;
    });

    if (section.tagName === "DETAILS") {
      const wrap = document.createElement("div");
      wrap.className = "subsection-wrap";
      section.parentNode.insertBefore(wrap, section);
      wrap.append(btn, section);
    } else {
      heading.appendChild(btn);
    }
  });
}

initHelpToggles();

function placePortalButton() {
  const btn = document.getElementById("portalReleaseBtn");
  const foot = document.getElementById("portalFoot");
  const top = document.getElementById("guestRelease");
  if (!btn || !foot || !top) return;
  const onHomeGrid = document.body.dataset.tab === "home" && !document.body.dataset.page;
  const wanted = onHomeGrid ? foot : top;
  if (btn.parentElement !== wanted) wanted.appendChild(btn);
}

const PORTAL_ANNOUNCE_SEC = 120;      // a tap still worth confirming
const PORTAL_SENTENCE_MS = 10000;     // and how long the confirmation stays
const PORTAL_ANNOUNCED_KEY = "portalAnnounced";
let portalSentenceTimer = null;

function clearPortalSentence() {
  clearTimeout(portalSentenceTimer);
  portalSentenceTimer = null;
  const hint = document.getElementById("portalHint");
  if (hint.textContent) hint.textContent = "";
}

function announcePortal() {
  const hint = document.getElementById("portalHint");
  hint.textContent = t("guest.joined");
  showPortalDone();
  clearTimeout(portalSentenceTimer);
  portalSentenceTimer = setTimeout(clearPortalSentence, PORTAL_SENTENCE_MS);
}

async function refreshPortalBanner() {
  const result = await apiGet("/api/portal/status");
  if (!result.ok || !result.data) return false;
  const d = result.data;
  const btn = document.getElementById("portalReleaseBtn");
  placePortalButton();

  // Only a device on the hotspot has anything to finish, so say it was done.
  const onAp = !!d.on_ap;
  const held = portalHolds(d);
  btn.hidden = !held;
  const tap = onAp && d.released ? d.released_at : null;
  if (!tap) {
    clearPortalSentence();
    return held;
  }
  if (localStorage.getItem(PORTAL_ANNOUNCED_KEY) !== String(tap)
      && Date.now() / 1000 - tap < PORTAL_ANNOUNCE_SEC) {
    localStorage.setItem(PORTAL_ANNOUNCED_KEY, String(tap));
    announcePortal();
  }
  return held;
}

/* The sentence appears where the button was. void offsetWidth restarts it. */
function showPortalDone() {
  const block = document.getElementById("guestRelease");
  block.classList.remove("is-done");
  void block.offsetWidth;   // a second release restarts it instead of nothing
  block.classList.add("is-done");
}

/* Redone while the page is up: a phone that changes Wi-Fi would keep the sentence for good. */
refreshEvery(refreshPortalBanner, 30000);

function portalBrowserTab() {
  try {
    return window.open(window.location.origin + "/", "_blank");
  } catch (error) {
    return null;
  }
}

document.getElementById("portalReleaseBtn").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true;
  // Requested BEFORE the await: after it, a browser no longer counts the tap as a gesture.
  const tab = applePlatform() ? null : portalBrowserTab();
  const hint = document.getElementById("portalHint");
  const result = await apiPost("/api/portal/release");
  if (result.ok) {
    btn.hidden = true;
    announcePortal();
    leaveCaptiveWindow();
  } else {
    if (tab) tab.close();
    hint.textContent = t("common.failed_prefix", { error: errorLabel(result.error) });
  }
  btn.disabled = false;
});

document.getElementById("guestLoginBtn").addEventListener("click", () => {
  showLoginOverlay();
});

const SCROLL_FADE_SELECTOR = ".device-list, .folder-list, .session-list, .event-list";

function updateScrollFade(el) {
  const above = el.scrollTop > 2;
  const below = el.scrollTop + el.clientHeight < el.scrollHeight - 2;
  const fade = above && below ? "both" : above ? "top" : below ? "bottom" : "";
  if (el.dataset.fade !== fade) el.dataset.fade = fade;
}

document.querySelectorAll(SCROLL_FADE_SELECTOR).forEach((el) => {
  const update = () => updateScrollFade(el);
  el.addEventListener("scroll", update, { passive: true });
  new MutationObserver(update).observe(el, { childList: true, subtree: true });
  if (window.ResizeObserver) new ResizeObserver(update).observe(el);
  update();
});

const portalHoldsDevice = await refreshPortalBanner();
if (portalHoldsDevice && arrivedWithoutHash && !railMode()) {
  setActiveView(DEFAULT_VIEW.tab, null, { replace: true, scroll: false });
}

document.dispatchEvent(new CustomEvent("page-shown", {
  detail: { tab: document.body.dataset.tab || null, page: document.body.dataset.page || null },
}));
} // end of initApp()
