/* The guided tour: the frame once after the first arrival, and a page's own tour the first
   time a page that is not obvious opens. A step whose element is not shown here is skipped. */
const TOUR_SEEN_KEY = "rukebox_tour_seen";
// Set by the test harness: the tour would only sit over pages a test drives.
const TOUR_OFF_KEY = "rukebox_tour_off";

const TOURS = {
  shell: [
    [".tabbar", "tour.shell_areas"],
    ["#pageBack", "tour.shell_back"],
    ["#topbarMoreBtn", "tour.shell_more"],
    ["#searchBtn", "tour.shell_search"],
    ["#themeToggleBtn", "tour.shell_look"],
    ["#connPill", "tour.shell_link"],
    ["#viewBarBtn", "tour.shell_view"],
    ["#tourBtn", "tour.shell_again"],
  ],
  "settings/settings": [
    ["#scheduleList", "tour.schedules_list"],
    ["#scheduleFormSection", "tour.schedules_add"],
  ],
  "home/lists": [
    ["#listsList", "tour.lists_list"],
    ["#listsImport", "tour.lists_m3u"],
    ["#stationsSection", "tour.lists_stations"],
  ],
  "settings/volume": [["#volumeShape", "tour.volume_shape"]],
  "settings/announcements": [["#announcementList", "tour.announcements_list"]],
  "settings/buttons": [["#singleClickAction", "tour.buttons_actions"]],
};

let tourBox = null;
let tourSteps = [];
let tourIndex = 0;
let tourId = null;

function tourSeen() {
  try {
    return JSON.parse(localStorage.getItem(TOUR_SEEN_KEY) || "[]");
  } catch (e) {
    return [];
  }
}

function markTourSeen(id) {
  try {
    const seen = tourSeen();
    if (!seen.includes(id)) localStorage.setItem(TOUR_SEEN_KEY, JSON.stringify(seen.concat(id)));
  } catch (e) { /* a browser that keeps nothing sees the tour again: no harm */ }
}

function tourOff() {
  try {
    return localStorage.getItem(TOUR_OFF_KEY) === "1";
  } catch (e) {
    return false;
  }
}

/* Shown here: no hidden ancestor, nothing that takes it out of the page. */
function tourVisible(el) {
  if (!el || el.closest("[hidden]")) return false;
  for (let node = el; node && node.nodeType === 1; node = node.parentElement) {
    if (window.getComputedStyle(node).display === "none") return false;
  }
  return true;
}

function endTour() {
  if (tourId) markTourSeen(tourId);
  document.querySelectorAll(".tour-target").forEach((el) => el.classList.remove("tour-target"));
  if (tourBox) tourBox.remove();
  tourBox = null;
  tourId = null;
}

function placeTourBubble(bubble, target) {
  const rect = target.getBoundingClientRect();
  const margin = 12;
  const width = Math.min(320, window.innerWidth - 2 * margin);
  bubble.style.width = width + "px";
  const left = Math.min(Math.max(margin, rect.left + rect.width / 2 - width / 2), window.innerWidth - width - margin);
  bubble.style.left = left + "px";
  const below = rect.bottom + margin;
  const height = bubble.offsetHeight || 160;
  if (below + height < window.innerHeight - margin || rect.top < height + 2 * margin) {
    bubble.style.top = Math.min(below, window.innerHeight - height - margin) + "px";
    bubble.dataset.side = "below";
  } else {
    bubble.style.top = (rect.top - height - margin) + "px";
    bubble.dataset.side = "above";
  }
}

function showTourStep() {
  document.querySelectorAll(".tour-target").forEach((el) => el.classList.remove("tour-target"));
  const [selector, key] = tourSteps[tourIndex];
  const target = document.querySelector(selector);
  target.classList.add("tour-target");
  if (target.closest("#main")) target.scrollIntoView({ block: "center" });
  const bubble = tourBox.querySelector(".tour-bubble");
  bubble.querySelector(".tour-text").textContent = t(key);
  bubble.querySelector(".tour-count").textContent =
    t("tour.count", { i: tourIndex + 1, n: tourSteps.length });
  const next = bubble.querySelector(".tour-next");
  next.textContent = t(tourIndex + 1 < tourSteps.length ? "tour.next" : "tour.done");
  placeTourBubble(bubble, target);
  next.focus();
}

function startTour(id) {
  if (tourBox) endTour();
  const steps = (TOURS[id] || []).filter(([selector]) => tourVisible(document.querySelector(selector)));
  if (!steps.length) {
    markTourSeen(id);
    return false;
  }
  tourId = id;
  tourSteps = steps;
  tourIndex = 0;
  tourBox = document.createElement("div");
  tourBox.className = "tour";
  const bubble = document.createElement("div");
  bubble.className = "tour-bubble";
  bubble.setAttribute("role", "dialog");
  bubble.setAttribute("aria-live", "polite");
  bubble.setAttribute("aria-label", t("tour.label"));
  const text = document.createElement("p");
  text.className = "tour-text";
  const foot = document.createElement("div");
  foot.className = "tour-foot";
  const count = document.createElement("span");
  count.className = "tour-count";
  const skip = document.createElement("button");
  skip.type = "button";
  skip.className = "btn-link tour-skip";
  skip.textContent = t("tour.skip");
  skip.addEventListener("click", endTour);
  const next = document.createElement("button");
  next.type = "button";
  next.className = "btn btn-primary btn-small tour-next";
  next.addEventListener("click", () => {
    tourIndex += 1;
    if (tourIndex < tourSteps.length) showTourStep();
    else endTour();
  });
  foot.append(count, skip, next);
  bubble.append(text, foot);
  tourBox.appendChild(bubble);
  document.body.appendChild(tourBox);
  showTourStep();
  return true;
}

/* The frame's tour first, once; then each page's own, the first time it opens. */
function offerTour(tab, page) {
  if (tourOff() || tourBox) return;
  if (!document.getElementById("bootOverlay").hidden) return;
  const login = document.getElementById("loginOverlay");
  if (login && !login.hidden) return;
  const seen = tourSeen();
  if (!seen.includes("shell")) {
    startTour("shell");
    return;
  }
  const id = tab + "/" + page;
  if (TOURS[id] && !seen.includes(id)) startTour(id);
}

document.addEventListener("page-shown", (event) => {
  const { tab, page } = event.detail || {};
  // After the page has drawn what its tour points at.
  setTimeout(() => offerTour(tab, page), 600);
});
// A page opened straight from its address shows before the loading screen leaves: offered then.
new MutationObserver(() => {
  if (document.getElementById("bootOverlay").hidden) {
    setTimeout(() => offerTour(document.body.dataset.tab, document.body.dataset.page), 600);
  }
}).observe(document.getElementById("bootOverlay"), { attributes: true, attributeFilter: ["hidden"] });
window.addEventListener("resize", () => {
  if (tourBox) showTourStep();
});
document.addEventListener("keydown", (event) => {
  if (tourBox && event.key === "Escape") endTour();
});

document.getElementById("tourBtn").addEventListener("click", () => {
  try {
    localStorage.removeItem(TOUR_SEEN_KEY);
  } catch (e) { /* nothing kept, nothing to forget */ }
  // After the header's menu has closed itself on this click.
  setTimeout(() => startTour("shell"), 50);
});
