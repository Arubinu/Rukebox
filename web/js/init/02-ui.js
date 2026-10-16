function closeModal(value) {
  if (!modalResolve) return;
  const resolve = modalResolve;
  modalResolve = null;
  document.getElementById("modalOverlay").hidden = true;
  resolve(value);
}

function openModal({ title, body, bodyNode, legend, confirm, choices, actions, modalClass }) {
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

    // Under the choices, as the timer dialog's legend is under its own buttons.
    const legendBox = document.getElementById("modalLegend");
    legendBox.replaceChildren();
    legendBox.hidden = !legend;
    if (legend) legendBox.append(legend);

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

const MODE_KEYS = ["music", "idle", "stopped", "meme", "cutoff_announce", "shutting_down", "restarting", "game",
                   "cast"];

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

/* "area/page", or "area/" for any page of an area. No list: a refresh every page needs. */
function pageOnScreen(pages) {
  if (!pages) return true;
  const here = (document.body.dataset.tab || "") + "/" + (document.body.dataset.page || "");
  return pages.some((page) => (page.endsWith("/") ? here.startsWith(page) : here === page));
}

