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

/* Beside the menu, the portal block belongs to the menu's foot: pinned under its pages, not over them.
   Called before this point of initApp runs, so it keeps its state on itself rather than in a const. */
function placeGuestRelease() {
  const block = document.getElementById("guestRelease");
  const navFoot = document.getElementById("navFoot");
  if (!block || !navFoot) return false;
  if (!placeGuestRelease.query) {
    placeGuestRelease.query = window.matchMedia("(min-width: 640px)");
    placeGuestRelease.query.addEventListener("change", () => placePortalButton());
    placeGuestRelease.next = block.nextElementSibling;
  }
  const inMenu = placeGuestRelease.query.matches && document.body.dataset.access !== "guest";
  if (inMenu && block.parentElement !== navFoot) navFoot.appendChild(block);
  if (!inMenu && block.parentElement === navFoot) placeGuestRelease.next.before(block);
  return inMenu;
}

function placePortalButton() {
  const btn = document.getElementById("portalReleaseBtn");
  const foot = document.getElementById("portalFoot");
  const top = document.getElementById("guestRelease");
  if (!btn || !foot || !top) return;
  const inMenu = placeGuestRelease();
  const onHomeGrid = !inMenu && document.body.dataset.tab === "home" && !document.body.dataset.page;
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
  showLoginOverlay("", true);
});

const SCROLL_FADE_SELECTOR = ".device-list, .folder-list, .session-list, .event-list, .nav-scroll";

function updateScrollFade(el) {
  const above = el.scrollTop > 2;
  const below = el.scrollTop + el.clientHeight < el.scrollHeight - 2;
  const fade = above && below ? "both" : above ? "top" : below ? "bottom" : "";
  if (el.dataset.fade !== fade) el.dataset.fade = fade;
}

document.querySelectorAll(SCROLL_FADE_SELECTOR).forEach((el) => {
  const update = () => updateScrollFade(el);
  el.addEventListener("scroll", update, { passive: true });
  // An area's page list opens by a class or attribute, which changes the height without a child.
  new MutationObserver(update).observe(el, { childList: true, subtree: true, attributes: true,
                                              attributeFilter: ["class", "hidden"] });
  if (window.ResizeObserver) new ResizeObserver(update).observe(el);
  update();
});

async function refreshDomains() {
  const result = await apiGet("/api/security/hosts");
  if (!result.ok || !result.data) return;
  const refused = result.data.refused || [];
  document.getElementById("refusedHostsBox").hidden = !refused.length;
  document.getElementById("refusedHosts").replaceChildren(...refused.map((one) => {
    const li = document.createElement("li");
    const name = document.createElement("span");
    name.className = "device-name";
    name.textContent = one.host;
    const allow = document.createElement("button");
    allow.type = "button";
    allow.className = "btn btn-small";
    allow.dataset.icon = "check";
    allow.textContent = t("domain.allow");
    allow.addEventListener("click", async () => {
      const field = document.getElementById("extraHosts");
      const names = field.value.split(/[\s,]+/).filter(Boolean);
      if (!names.includes(one.host)) names.push(one.host);
      const value = names.join(", ");
      allow.disabled = true;
      const r = await apiPost("/api/settings", { "network.web_extra_hosts": value });
      allow.disabled = false;
      if (!r.ok) {
        showError(r.error);
        return;
      }
      field.value = value;
      settingsBaseline["network.web_extra_hosts"] = value;
      showToast(t("domain.allowed", { host: one.host }), t("domain.allowed_hint"));
      refreshDomains();
    });
    const forget = document.createElement("button");
    forget.type = "button";
    forget.className = "btn btn-icon btn-small btn-danger-outline";
    forget.dataset.icon = "trash";
    forget.setAttribute("aria-label", t("domain.forget"));
    forget.title = t("domain.forget");
    forget.addEventListener("click", async () => {
      forget.disabled = true;
      const r = await apiDelete("/api/security/hosts", { host: one.host });
      if (!r.ok) {
        forget.disabled = false;
        showError(r.error);
        return;
      }
      refreshDomains();
    });
    const actions = document.createElement("span");
    actions.className = "device-actions";
    actions.append(allow, forget);
    li.append(name, actions);
    return li;
  }));
}
refreshEvery(refreshDomains, 30000, ["system/security"]);

const portalHoldsDevice = await refreshPortalBanner();
if (portalHoldsDevice && arrivedWithoutHash && !railMode()) {
  setActiveView(DEFAULT_VIEW.tab, null, { replace: true, scroll: false });
}

document.dispatchEvent(new CustomEvent("page-shown", {
  detail: { tab: document.body.dataset.tab || null, page: document.body.dataset.page || null },
}));
// The settings search: every option of every page, found by its words.
function searchEntries() {
  const out = [];
  document.querySelectorAll(".card[data-page]").forEach((card) => {
    if (card.hasAttribute("hidden") || !pageIsOffered(card)) return;
    const titleEl = card.querySelector("h2 [data-i18n]");
    const cardTitle = titleEl ? titleEl.textContent.trim() : "";
    const where = t("tab." + card.dataset.tab) + " \u203a " + cardTitle;
    out.push({ el: card, card, label: cardTitle, where: t("tab." + card.dataset.tab), desc: "", page: true });
    card.querySelectorAll(".field-row, details.subsection > summary").forEach((row) => {
      const hiddenBy = row.closest("[hidden]");
      if (hiddenBy && hiddenBy !== card) return;
      const labelEl = row.matches("summary") ? row : row.querySelector("label, .field-text > span");
      const label = labelEl ? labelEl.textContent.replace(/\s+/g, " ").trim() : "";
      if (!label) return;
      const descEl = row.querySelector(".field-desc");
      out.push({ el: row, card, label, where, desc: descEl ? descEl.textContent.trim() : "", page: false });
    });
  });
  return out;
}

function searchMatches(query) {
  const words = foldText(query).split(/\s+/).filter(Boolean);
  if (!words.length) return [];
  const found = [];
  searchEntries().forEach((entry) => {
    const label = foldText(entry.label);
    const all = label + " " + foldText(entry.desc) + " " + foldText(entry.where);
    if (!words.every((w) => all.includes(w))) return;
    const score = (words.every((w) => label.includes(w)) ? 0 : 2) + (label.startsWith(words[0]) ? 0 : 1)
      + (entry.page ? 0 : 0.5);
    found.push({ entry, score });
  });
  found.sort((a, b) => a.score - b.score || a.entry.label.localeCompare(b.entry.label));
  return found.slice(0, 40).map((f) => f.entry);
}

function goToOption(entry) {
  const card = entry.card;
  if ((card.dataset.level === "detail" || entry.el.closest('[data-level="detail"]')) && currentView() === "simple") {
    showDetailed();
  }
  setActiveView(card.dataset.tab, card.dataset.page);
  const fold = entry.el.closest("details");
  if (fold) fold.open = true;
  setTimeout(() => {
    const target = entry.page ? card : entry.el;
    target.scrollIntoView({ behavior: "smooth", block: entry.page ? "start" : "center" });
    if (entry.page) return;
    target.classList.remove("search-hit");
    void target.offsetWidth;
    target.classList.add("search-hit");
    setTimeout(() => target.classList.remove("search-hit"), 2500);
    const field = target.querySelector("input:not([type=hidden]), select, textarea, button");
    if (field) field.focus({ preventScroll: true });
  }, 120);
}

function openSearch() {
  const box = document.createElement("div");
  box.className = "search-box";
  const input = document.createElement("input");
  input.type = "search";
  input.className = "text-input";
  input.placeholder = t("search.placeholder");
  input.setAttribute("aria-label", t("search.open"));
  input.autocomplete = "off";
  const list = document.createElement("ul");
  list.className = "search-results";
  const empty = document.createElement("p");
  empty.className = "hint search-empty";
  empty.textContent = t("search.hint");
  box.append(input, list, empty);

  let shown = [];
  const paint = () => {
    shown = searchMatches(input.value);
    list.replaceChildren(...shown.map((entry) => {
      const li = document.createElement("li");
      const b = document.createElement("button");
      b.type = "button";
      const label = document.createElement("span");
      label.className = "search-label";
      label.textContent = entry.page ? entry.label + " (" + t("search.page") + ")" : entry.label;
      const where = document.createElement("span");
      where.className = "search-where";
      where.textContent = entry.where;
      b.append(label, where);
      if (entry.desc) {
        const desc = document.createElement("span");
        desc.className = "search-desc";
        desc.textContent = entry.desc;
        b.append(desc);
      }
      b.addEventListener("click", () => {
        closeModal(false);
        goToOption(entry);
      });
      li.append(b);
      return li;
    }));
    empty.hidden = shown.length > 0;
    empty.textContent = input.value.trim() ? t("search.empty") : t("search.hint");
  };
  input.addEventListener("input", paint);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && shown.length) {
      e.preventDefault();
      closeModal(false);
      goToOption(shown[0]);
    }
  });
  openModal({ title: t("search.title"), bodyNode: box, actions: false, modalClass: "modal-search" });
  paint();
  input.focus();
}

document.getElementById("searchBtn").addEventListener("click", () => {
  setTopbarMenu(false);
  openSearch();
});
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k" && document.body.dataset.access !== "guest") {
    e.preventDefault();
    openSearch();
  }
});

document.getElementById("dedicationsVoiceHttps").hidden = window.isSecureContext;
// A field the browser refuses inside a closed fold would block the save in silence.
let invalidToastAt = 0;
document.addEventListener("invalid", (event) => {
  const field = event.target;
  for (let el = field.parentElement; el; el = el.parentElement) {
    if (el.tagName === "DETAILS") el.open = true;
  }
  if (Date.now() - invalidToastAt < 1000) return;
  invalidToastAt = Date.now();
  const label = field.id ? document.querySelector('label[for="' + field.id + '"]') : null;
  showToast(t("alert.invalid_field"), (label ? label.textContent.trim() + " - " : "") + field.validationMessage,
            { error: true });
}, true);
