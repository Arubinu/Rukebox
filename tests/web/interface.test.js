"use strict";
// The interface's own rules, on the real page: what it loads, what it sends,
// and what it never asks for.

const assert = require("node:assert/strict");
const { test } = require("node:test");
const { load, until, wait } = require("./harness");

// The page is closed whatever the test does: its timers would keep the run alive.
function open(t, options) {
  const page = load(options);
  t.after(() => page.close());
  return page;
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

test("the interface starts without an error and lands on the player", async (t) => {
  const page = open(t);
  await until(() => page.$("bootOverlay").hidden);
  assert.deepEqual(page.errors, []);
  assert.equal(page.document.body.dataset.tab, "home");
  assert.equal(page.document.body.dataset.page, "player");
  assert.equal(page.window.location.hash, "#home/player");
});

test("an address with a page opens that page", async (t) => {
  const page = open(t, { hash: "#settings/announcements" });
  await until(() => page.document.body.dataset.page === "announcements");
  assert.equal(page.document.body.dataset.tab, "settings");
});

test("a menu is built from the cards: one tile per page that has something to show", async (t) => {
  const page = open(t);
  await until(() => page.$("bootOverlay").hidden);
  const player = page.document.querySelector('.page-tile[data-page="player"]');
  assert.ok(player && !player.hidden, "the player is a page");
  const offered = [...player.parentElement.querySelectorAll(".page-tile")]
    .filter((tile) => !tile.hidden).map((tile) => tile.dataset.page);
  const empty = [...page.document.querySelectorAll('.card[data-tab="home"][hidden]')].map((card) => card.dataset.page);
  assert.ok(empty.length > 0, "the fakes leave some cards with nothing to show");
  for (const name of empty) assert.ok(!offered.includes(name), name + " has nothing to show, so no tile");
  for (const name of offered) {
    const card = page.document.querySelector('.card[data-tab="home"][data-page="' + name + '"]');
    assert.ok(card && !card.hidden, name + " is a card that shows something");
  }
});

test("with a password and no guest access, only the login is shown", async (t) => {
  const page = open(t, { routes: {
    "GET /api/portal/status": { enabled: true, mode: "release", on_ap: false, released: true,
                                guest_mode: false, auth_required: true, authenticated: false },
  } });
  await until(() => !page.$("loginOverlay").hidden);
  assert.equal(page.sent("GET", "/api/status").length, 0, "nothing is asked before the login");
  assert.equal(page.sent("GET", "/api/settings").length, 0);
});

test("a guest never asks for what a guest may not have", async (t) => {
  const page = open(t, { routes: {
    "GET /api/portal/status": { enabled: true, mode: "release", on_ap: false, released: true,
                                guest_mode: true, auth_required: true, authenticated: false },
  } });
  await until(() => page.$("bootOverlay").hidden);
  assert.equal(page.document.body.dataset.access, "guest");
  assert.ok(page.sent("GET", "/api/status").length > 0, "the status is a guest's");
  for (const path of ["/api/settings", "/api/wifi/ap", "/api/wifi/clients", "/api/journal/summary",
                      "/api/system/info", "/api/lists", "/api/likes"]) {
    assert.equal(page.sent("GET", path).length, 0, path + " is the owner's");
  }
  assert.ok(!page.document.querySelector('.page-tile[data-page="music"]:not([hidden])'),
            "adding music is the owner's, although its card sits on Home");
  assert.deepEqual(page.errors, []);
});

test("every area of the tab bar has pages, and every page belongs to an area of the bar", async (t) => {
  const page = open(t);
  await until(() => page.$("bootOverlay").hidden);
  const areas = [...page.document.querySelectorAll(".tab-btn")].map((btn) => btn.dataset.tab);
  assert.deepEqual(areas, ["home", "settings", "audio", "network", "system", "stats"]);
  const cards = [...page.document.querySelectorAll(".card[data-tab][data-page]")];
  for (const area of areas) assert.ok(cards.some((card) => card.dataset.tab === area), area + " has a page");
  for (const card of cards) assert.ok(areas.includes(card.dataset.tab), card.dataset.page + " is in the bar");
  const where = (name) => cards.filter((card) => card.dataset.page === name).map((card) => card.dataset.tab);
  assert.deepEqual(where("clock"), ["system"]);
  assert.deepEqual(where("accesspoint"), ["network"]);
  assert.deepEqual(where("guest"), ["network"]);
  assert.deepEqual(where("music"), ["home"]);
});

test("saving a settings card sends only what was changed", async (t) => {
  const submit = (one) => one.$("volumeForm").dispatchEvent(
    new one.window.Event("submit", { bubbles: true, cancelable: true }));
  const saved_by = async (one) => (await until(
    () => one.sent("POST", "/api/settings").length && one.sent("POST", "/api/settings")))[0];

  // A page that was told nothing sends the whole card: that is what the Pi would hold.
  const blank = open(t, { routes: { "POST /api/settings": {} } });
  await until(() => blank.$("bootOverlay").hidden);
  submit(blank);
  const stored = (await saved_by(blank)).body;
  assert.ok(Object.keys(stored).length > 5);

  const page = open(t, { routes: { "GET /api/settings": stored, "POST /api/settings": {} } });
  await until(() => page.$("bootOverlay").hidden);
  submit(page);
  await wait(100);
  assert.equal(page.sent("POST", "/api/settings").length, 0, "nothing changed, nothing sent");

  const field = page.$("baseVolume");
  field.value = "55";
  field.dispatchEvent(new page.window.Event("input", { bubbles: true }));
  field.dispatchEvent(new page.window.Event("change", { bubbles: true }));
  submit(page);
  const saved = await saved_by(page);
  assert.deepEqual(Object.keys(saved.body), ["BASE_VOLUME"]);
  assert.equal(String(saved.body.BASE_VOLUME), "55");
});

test("a new announcement's form shows the rows of its trigger, and no others", async (t) => {
  const page = open(t);
  await until(() => page.$("bootOverlay").hidden);
  const shown = (id) => !page.$(id).hidden;
  const choose = (value) => {
    page.$("annTrigger").value = value;
    page.$("annTrigger").dispatchEvent(new page.window.Event("change", { bubbles: true }));
  };
  assert.deepEqual([shown("annTimeRow"), shown("annDelayRow"), shown("annRepeatRow"), shown("annAfterRow")],
                   [true, false, false, true], "a fixed time, as the page loads");
  choose("after_music");
  assert.deepEqual([shown("annTimeRow"), shown("annDelayRow"), shown("annRepeatRow"), shown("annAfterRow")],
                   [false, true, true, true]);
  choose("manual");
  assert.deepEqual([shown("annTimeRow"), shown("annDelayRow"), shown("annAfterRow"), shown("annAutoChanceRow")],
                   [false, false, false, false], "nothing follows a play started by hand");
});

test("an announcement is saved with what follows it", async (t) => {
  const page = open(t, { routes: { "POST /api/announcements": { id: "noon", name: "Noon" } } });
  await until(() => page.$("bootOverlay").hidden);
  page.$("annName").value = "Noon";
  page.$("annAfter").value = "pause";
  page.$("announcementForm").dispatchEvent(new page.window.Event("submit", { bubbles: true, cancelable: true }));
  const [sent] = await until(() => page.sent("POST", "/api/announcements").length && page.sent("POST", "/api/announcements"));
  assert.equal(sent.body.after_action, "pause");
  assert.equal(sent.body.trigger, "time");
});

test("ticking \"Connect at startup\" says so, even when an older status arrives meanwhile", async (t) => {
  const stale = deferred();
  let calls = 0;
  const page = open(t, { routes: {
    "GET /api/wifi/status": () => {
      calls += 1;
      const answer = { configured: true, conn_name: "home", active: true, ip_address: "192.168.1.5/24",
                       autoconnect: false, client_here: false };
      return calls === 1 ? answer : stale.promise.then(() => answer);
    },
    "POST /api/wifi/autoconnect": {},
  } });
  await until(() => page.$("bootOverlay").hidden && calls >= 1);
  page.document.dispatchEvent(new page.window.Event("visibilitychange"));
  await until(() => calls >= 2);

  const box = page.$("homeWifiAuto");
  box.checked = true;
  box.dispatchEvent(new page.window.Event("change", { bubbles: true }));
  await until(() => page.sent("POST", "/api/wifi/autoconnect").length);
  stale.resolve();
  await wait(60);

  assert.equal(page.sent("POST", "/api/wifi/autoconnect")[0].body.on, true);
  assert.equal(box.checked, true, "the box keeps what was ticked");
  const toasts = page.$("toastStack").textContent;
  assert.match(toasts, /will connect at startup/);
  assert.doesNotMatch(toasts, /stay off/);
});

test("changing the language translates the page and keeps the help buttons", async (t) => {
  const page = open(t);
  await until(() => page.$("bootOverlay").hidden);
  const title = page.document.querySelector("#annTitle [data-i18n]") || page.$("annTitle");
  const before = title.textContent;
  const helps = page.document.querySelectorAll(".help-btn").length;
  page.$("langToggleBtn").click();
  await until(() => title.textContent !== before);
  assert.equal(page.document.querySelectorAll(".help-btn").length, helps);
  assert.ok(helps > 0);
});
