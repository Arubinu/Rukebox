"use strict";
// The interface's own rules, on the real page: how it starts, its menus,
// the accounts and what a guest is offered.
// (Four files rather than one: node runs files side by side, and the tests of a file in turn.)

const assert = require("node:assert/strict");
const { test } = require("node:test");
const { load, until, wait, open, deferred, STATUS, GUEST_PATHS, MEMBER_PATHS, OWNER, MARIE } = require("./harness");

test("the interface starts without an error and lands on the player", async (t) => {
  const page = open(t);
  await until(() => page.$("bootOverlay").hidden);
  assert.deepEqual(page.errors, []);
  assert.equal(page.document.body.dataset.tab, "home");
  assert.equal(page.document.body.dataset.page, "player");
  assert.equal(page.window.location.hash, "#home/player");
});

test("a page the radio tells it is switching off says so, without having asked", async (t) => {
  // The long poll carries it: every page is told, not only the one that asked.
  let calls = 0;
  let release;
  const booted = new Promise((resolve) => { release = resolve; });
  const page = open(t, { routes: {
    "GET /api/status/wait": () => {
      calls += 1;
      return calls === 1 ? booted.then(() => ({ version: 1 }))
                         : { version: 1, going_down: "poweroff" };
    },
  } });
  await until(() => page.$("bootOverlay").hidden);
  release();
  await until(() => /switched off/i.test(page.$("bootMessage").textContent));
  assert.equal(page.$("bootOverlay").hidden, false);
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
                                guest_mode: false, auth_required: true, authenticated: false,
                                role: "guest", paths: [], accounts: [OWNER, MARIE] },
  } });
  await until(() => !page.$("loginOverlay").hidden);
  assert.equal(page.$("loginClose").hidden, true, "nothing behind it to go back to");
  assert.equal(page.sent("GET", "/api/status").length, 0, "nothing is asked before the login");
  assert.equal(page.sent("GET", "/api/settings").length, 0);
});

test("someone who lives here gets the music, not the settings", async (t) => {
  const page = open(t, { routes: {
    "GET /api/portal/status": { enabled: true, mode: "release", on_ap: false, released: true,
                                guest_mode: false, auth_required: true, authenticated: true,
                                role: "member", paths: MEMBER_PATHS, accounts: [OWNER, MARIE] },
    "GET /api/lists": { lists: [] },
    "GET /api/library/facets": { artists: [], albums: [], genres: [] },
  } });
  await until(() => page.$("bootOverlay").hidden);
  assert.equal(page.document.body.dataset.access, "member");
  for (const tab of ["settings", "network", "system", "stats"]) {
    const button = page.document.querySelector('.tab-btn[data-tab="' + tab + '"]');
    assert.ok(button.hidden, tab + " is not a member's");
  }
  assert.equal(page.sent("GET", "/api/settings").length, 0, "the settings are not theirs");
  assert.ok(page.sent("GET", "/api/status").length > 0, "the player is");
  await until(() => !page.$("listsCard").hidden);
  assert.equal(page.$("listsCard").hidden, false, "the lists they build are theirs");
  const tile = (name) => page.document.querySelector('.page-tile[data-page="' + name + '"]');
  assert.ok(!tile("security") || tile("security").hidden, "the password is not");
  assert.deepEqual(page.errors, []);
});

test("the people card lists who may log in, and adds one", async (t) => {
  const page = open(t, { routes: {
    "GET /api/auth/accounts": { accounts: [OWNER, { id: "marie", name: "Marie", role: "member",
                                                    password: true }],
                                roles: ["member", "operator", "owner"] },
    "POST /api/auth/accounts": { id: "lucie", name: "Lucie", role: "operator" },
  }, hash: "#system/people" });
  await until(() => page.$("bootOverlay").hidden);
  const rows = await until(() => {
    const found = page.$("peopleList").querySelectorAll(".device-row");
    return found.length === 2 && found;
  });
  assert.equal(rows[0].querySelector(".device-name").textContent, "Le patron");
  assert.match(rows[0].querySelector(".device-badge").textContent, /Everything/);
  assert.match(rows[1].querySelector(".device-badge").textContent, /Lives here/);
  assert.equal(rows[0].querySelector(".device-note").textContent, "",
               "an owner with a password says nothing");

  page.$("personName").value = "Lucie";
  page.$("personRole").value = "operator";
  page.$("personPassword").value = "huit-caracteres";
  page.$("peopleForm").dispatchEvent(new page.window.Event("submit", { bubbles: true, cancelable: true }));
  const sent = (await until(() => page.sent("POST", "/api/auth/accounts").length
    && page.sent("POST", "/api/auth/accounts")))[0];
  assert.deepEqual(sent.body, { name: "Lucie", role: "operator", password: "huit-caracteres" });
  assert.deepEqual(page.errors, []);
});

test("a guest is never told who may log in", async (t) => {
  const page = open(t, { routes: {
    "GET /api/portal/status": { enabled: true, mode: "release", on_ap: false, released: true,
                                guest_mode: true, auth_required: true, authenticated: false,
                                role: "guest", paths: GUEST_PATHS, accounts: [OWNER, MARIE] },
  } });
  await until(() => page.$("bootOverlay").hidden);
  assert.equal(page.sent("GET", "/api/auth/accounts").length, 0, "the accounts are the owner's");
  const tile = page.document.querySelector('.page-tile[data-page="people"]');
  assert.ok(!tile || tile.hidden, "and so is the card");
});

test("the login form offers the names, and sends the one chosen", async (t) => {
  const page = open(t, { routes: {
    "GET /api/portal/status": { enabled: true, mode: "release", on_ap: false, released: true,
                                guest_mode: false, auth_required: true, authenticated: false,
                                role: "guest", paths: [], accounts: [OWNER, MARIE] },
    "POST /api/auth/login": {},
  } });
  await until(() => !page.$("loginOverlay").hidden);
  assert.equal(page.$("loginAccountRow").hidden, false, "two names to choose from");
  const names = [...page.$("loginAccount").querySelectorAll("option")].map((one) => one.value);
  assert.deepEqual(names, ["Le patron", "Marie"]);
  page.$("loginAccount").value = "Marie";
  page.$("loginPassword").value = "mot-de-passe-marie";
  page.$("loginForm").dispatchEvent(new page.window.Event("submit", { bubbles: true, cancelable: true }));
  const sent = (await until(() => page.sent("POST", "/api/auth/login").length
    && page.sent("POST", "/api/auth/login")))[0];
  assert.deepEqual(sent.body, { name: "Marie", password: "mot-de-passe-marie" });
});

test("one account needs no name at the door: the password says it", async (t) => {
  const page = open(t, { routes: {
    "GET /api/portal/status": { enabled: true, mode: "release", on_ap: false, released: true,
                                guest_mode: false, auth_required: true, authenticated: false,
                                role: "guest", paths: [], accounts: [OWNER] },
  } });
  await until(() => !page.$("loginOverlay").hidden);
  assert.equal(page.$("loginAccountRow").hidden, true, "nothing to choose");
});

test("a guest is only offered the pages they are served", async (t) => {
  const guestRoutes = {
    "GET /api/portal/status": { enabled: true, mode: "release", on_ap: false, released: true,
                                guest_mode: true, auth_required: true, authenticated: false,
                                role: "guest", paths: GUEST_PATHS, accounts: [OWNER] },
  };
  const page = open(t, { routes: guestRoutes, hash: "#system/security" });
  await until(() => page.$("bootOverlay").hidden);
  assert.equal(page.document.body.dataset.access, "guest");
  // An address cannot open what the guest is not served: they land on their own
  // menu, not on a settings page filled with the page's default values.
  await until(() => page.document.body.dataset.tab === "home");
  assert.equal(page.document.body.dataset.page, "", "the guest's menu, not a page");

  for (const tab of ["settings", "audio", "network", "system", "stats"]) {
    const button = page.document.querySelector('.tab-btn[data-tab="' + tab + '"]');
    assert.ok(button.hidden, tab + " has nothing for a guest");
  }
  const home = page.document.querySelector('.page-grid[data-tab="home"]');
  assert.ok(home && !home.hidden, "the guest's menu is Home");
  const offered = [...home.querySelectorAll(".page-tile")].filter((tile) => !tile.hidden)
    .map((tile) => tile.dataset.page);
  assert.ok(offered.includes("player"), "the player is theirs");
  const served = ["game", "library", "player", "recent", "suggest", "today", "upnext"];
  offered.forEach((name) => assert.ok(served.includes(name),
                                      name + " is not a page a guest is served"));
  ["security", "volume", "guest", "update", "clients", "settings"].forEach((name) => {
    const tile = page.document.querySelector('.page-tile[data-page="' + name + '"]');
    assert.ok(!tile || tile.hidden, name + " is not a guest page");
  });
  assert.deepEqual(page.errors, []);
});

test("a guest never asks for what a guest may not have", async (t) => {
  const page = open(t, { routes: {
    "GET /api/portal/status": { enabled: true, mode: "release", on_ap: false, released: true,
                                guest_mode: true, auth_required: true, authenticated: false,
                                role: "guest", paths: GUEST_PATHS, accounts: [OWNER] },
  } });
  await until(() => page.$("bootOverlay").hidden);
  assert.equal(page.document.body.dataset.access, "guest");
  assert.ok(page.sent("GET", "/api/status").length > 0, "the status is a guest's");
  for (const path of ["/api/settings", "/api/wifi/ap", "/api/wifi/clients", "/api/journal/summary",
                      "/api/system/info", "/api/lists", "/api/likes", "/api/excluded"]) {
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
  assert.deepEqual(Object.keys(saved.body), ["playback.base_volume"]);
  assert.equal(String(saved.body["playback.base_volume"]), "55");
});

test("every settings card is sent under the name the file gives it", async (t) => {
  const page = open(t, { routes: { "POST /api/settings": {} } });
  await until(() => page.$("bootOverlay").hidden);
  page.document.querySelectorAll("form").forEach((form) => form.dispatchEvent(
    new page.window.Event("submit", { bubbles: true, cancelable: true })));
  await until(() => page.sent("POST", "/api/settings").length > 0);
  await wait(200);
  const sent = page.sent("POST", "/api/settings");
  const keys = new Set();
  sent.forEach((one) => Object.keys(one.body).forEach((key) => keys.add(key)));
  assert.ok(keys.size > 100, "the whole settings page is covered: " + keys.size);
  keys.forEach((key) => assert.match(key, /^[a-z][a-z0-9_]*\.[a-z0-9_]+$/,
                                     key + " is not a section.name"));
  assert.deepEqual(page.errors, []);
});
