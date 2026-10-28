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

/* Android's captive-portal window is a native pull-to-refresh around its WebView: it steals
   the drag unless the WebView can itself scroll up, so the document stays one pixel down. */
function holdDocumentDown() {
  if (Math.abs(window.scrollY - 1) > 0.5) window.scrollTo(0, 1);
}

/* Whether what was touched uses the drag itself: what it does not use moves the document,
   and the window above the WebView then takes that drag for its own refresh. */
function handlesTheDrag(node) {
  if (node.closest && node.closest("input, textarea, select, [contenteditable]")) return true;
  for (let el = node; el && el !== document.documentElement; el = el.parentElement) {
    const style = getComputedStyle(el);
    if (/auto|scroll/.test(style.overflowY) && el.scrollHeight > el.clientHeight) return true;
  }
  return false;
}

/* That gesture is not lost: pulled down from the title bar, the page reloads itself. */
const PULL_REFRESH_PX = 64;

function refreshFromTheTitleBar() {
  const bar = document.querySelector(".topbar");
  const pill = document.getElementById("pullRefresh");
  let startY = null;
  const show = (pull) => {
    pill.hidden = false;
    pill.classList.add("shown");
    pill.classList.toggle("ready", pull >= 1);
    pill.style.setProperty("--pull", pull.toFixed(2));
  };
  const away = () => {
    pill.hidden = true;
    pill.classList.remove("shown", "ready", "spinning");
    pill.style.removeProperty("--pull");
  };
  bar.addEventListener("touchstart", (event) => {
    startY = event.touches.length === 1 && !handlesTheDrag(event.target) ? event.touches[0].clientY : null;
  }, { passive: true });
  bar.addEventListener("touchmove", (event) => {
    if (startY === null || event.touches.length !== 1) return;
    const pulled = event.touches[0].clientY - startY;
    if (pulled > 8) show(Math.min(pulled / PULL_REFRESH_PX, 1));
  }, { passive: true });
  bar.addEventListener("touchend", (event) => {
    if (startY === null) return;
    const last = event.changedTouches && event.changedTouches[0];
    const pulled = (last ? last.clientY : startY) - startY;
    startY = null;
    if (pulled < PULL_REFRESH_PX) {
      away();
      return;
    }
    pill.classList.add("spinning");
    window.location.reload();
  }, { passive: true });
  bar.addEventListener("touchcancel", () => {
    startY = null;
    away();
  }, { passive: true });
}

function refusePullToRefresh(ua) {
  const name = ua === undefined ? navigator.userAgent || "" : ua;
  if (!/Android/.test(name)) return false;
  document.documentElement.classList.add("pull-guard");
  window.addEventListener("scroll", holdDocumentDown, { passive: true });
  holdDocumentDown();
  refreshFromTheTitleBar();
  const stop = (event) => { if (event.touches.length === 1) event.preventDefault(); };
  const release = () => document.removeEventListener("touchmove", stop);
  document.addEventListener("touchstart", (event) => {
    if (event.touches.length === 1 && !handlesTheDrag(event.target)) {
      document.addEventListener("touchmove", stop, { passive: false });
    }
  }, { passive: true });
  document.addEventListener("touchend", release, { passive: true });
  document.addEventListener("touchcancel", release, { passive: true });
  return true;
}
refusePullToRefresh();

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

// `closable`: opened from the guest page, which stays usable behind it.
function showLoginOverlay(errorMessage, closable) {
  offerPortalReleaseOnLogin();
  const overlay = document.getElementById("loginOverlay");
  overlay.hidden = false;
  if (closable !== undefined) document.getElementById("loginClose").hidden = !closable;
  const errorBox = document.getElementById("loginError");
  errorBox.textContent = errorMessage || "";
  errorBox.hidden = !errorMessage;
  document.getElementById("loginPassword").focus();
}

function hideLoginOverlay() {
  document.getElementById("loginOverlay").hidden = true;
}

document.getElementById("loginClose").addEventListener("click", hideLoginOverlay);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !document.getElementById("loginClose").hidden
      && !document.getElementById("loginOverlay").hidden) hideLoginOverlay();
});

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

/* The names the login form offers: never a hash, never a date. */
function fillLoginAccounts(list) {
  const select = document.getElementById("loginAccount");
  if (!select) return;
  const wanted = Array.isArray(list) ? list : [];
  select.replaceChildren(...wanted.map((one) => {
    const option = document.createElement("option");
    option.value = one.name;
    option.textContent = one.name;
    return option;
  }));
  const single = wanted.length <= 1;
  select.hidden = single;
  document.getElementById("loginAccountRow").hidden = single;
}

document.getElementById("loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const password = document.getElementById("loginPassword").value;
  const account = document.getElementById("loginAccount");
  const name = account && !account.hidden ? account.value : "";
  const btn = document.getElementById("loginSubmit");
  let lockFor = 0;
  btn.disabled = true;
  try {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, password }),
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
// The pages the owner keeps from guests, read in every status.
let guestPagesOff = [];
// What the server answers this browser as, and every path that role may call (src/roles.py).
let myRole = "guest";
let mayCallPaths = null;

function mayCall(path) {
  if (myRole === "owner" || !mayCallPaths) return true;
  const clean = path.split("?")[0];
  if (clean.startsWith("/api/auth/")) return true;
  return mayCallPaths.some((one) => {
    const cut = one.indexOf("<");
    return cut === -1 ? clean === one : clean.startsWith(one.slice(0, cut));
  });
}

const BOOT_ASK_TIMEOUT_MS = 6000;
const BOOT_RETRY_MS = 2500;

const BOOT_GIVE_UP_MS = 40000;

let booting = false;

const BOOT_QUIET_MS = 250;
const BOOT_POPULATE_TIMEOUT_MS = 6000;

// Asked for alone on arrival: a small board answers one request far faster than
// forty at once.
const BOOT_FIRST_PATHS = ["/api/portal/status", "/api/device", "/api/status", "/api/likes",
                          "/api/audio/fallback", "/api/queue", "/api/now/lyrics"];
let bootHolding = false;
let bootGateOpen = null;
const bootGate = new Promise((resolve) => { bootGateOpen = resolve; });

function bootWaits(path) {
  if (!bootHolding) return false;
  const clean = path.split("?")[0];
  return !BOOT_FIRST_PATHS.includes(clean) && !clean.startsWith("/api/auth/");
}

function releaseBootGate() {
  bootHolding = false;
  bootGateOpen();
}

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
    key === "boot.not_rukebox" || key === "boot.banned" || key === "boot.bad_host" ? "error"
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

    if (result.ok === false && result.error === "bad_host") {
      const message = document.getElementById("bootMessage");
      message.dataset.i18nVarHost = window.location.hostname;
      bootMessage("boot.bad_host", false, "boot.bad_host_how");
      message.textContent = t("boot.bad_host", { host: window.location.hostname });
      return;
    }
    if (result.ok === false && result.error === "auth_required") {
      hideBootOverlay();
      showLoginOverlay();
      return;
    }
    const identity = (result && result.data) || {};
    // A radio that answers no role is one before the accounts: no password
    // means the whole interface, a password and no guest access means the door.
    if (identity.role) myRole = identity.role;
    else if (!identity.auth_required || identity.authenticated) myRole = "owner";
    else myRole = "guest";
    if (identity.paths) mayCallPaths = identity.paths;
    fillLoginAccounts(identity.accounts);
    guestMode = myRole === "guest";
    document.body.dataset.access = myRole;
    setLogoutVisible(!!(identity.auth_required && identity.authenticated));
    if (identity.auth_required && !identity.authenticated && !identity.guest_mode) {
      hideBootOverlay();
      showLoginOverlay();
      return;
    }
    const hash = window.location.hash.replace(/^#/, "");
    bootHolding = !hash || hash === "home/player";
    try {
      await initApp();
      // Not the player after all (a held portal opens the Home menu): every tile needs its data.
      if (document.body.dataset.page !== "player") releaseBootGate();
      await waitForCards();
    } finally {
      hideBootOverlay();
      releaseBootGate();
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

