"use strict";
// What the page hides when the machine cannot do it (web/app.js,
// web/style.css): the capabilities /api/status reports decide which cards and
// which rows are offered. A card carries data-needs="a b" and disappears from
// the menus as soon as one of the two is missing; the card itself is hidden by
// CSS, the menus by pageIsAvailable().
const test = require("node:test");
const assert = require("node:assert/strict");

const { load, until, STATUS } = require("./harness");

const PI = STATUS.capabilities;
const DOCKER = { platform: "docker", access_point: false, bluetooth: true,
                 captive_portal: false, gpio: false, local_audio: true, power: false,
                 rtc: false, self_update: false, set_clock: false, usb_gadget: false,
                 wireless: false };

function withCaps(caps, options = {}) {
  return load(Object.assign({}, options, {
    routes: Object.assign({}, options.routes, {
      "GET /api/status": Object.assign({}, STATUS, { capabilities: caps }),
    }),
  }));
}

function rowsOf(document) {
  return Array.from(document.querySelectorAll("[data-needs]"))
    .filter((el) => !el.classList.contains("card"))
    .map((el) => ({ needs: el.dataset.needs, hidden: el.hidden }));
}

/* A tile of an area's menu: `hidden` means the page was dropped from it. */
function tilesOf(document, tab) {
  const grid = document.querySelector('.page-grid[data-tab="' + tab + '"]');
  return Array.from(grid.querySelectorAll(".page-tile"))
    .map((tile) => ({ page: tile.dataset.page, hidden: tile.hidden }));
}

test("the Pi offers every card and row that asks for a capability", async (t) => {
  t.diagnostic("the reference platform: nothing is hidden for a capability");
  const page = withCaps(PI);
  await until(() => page.document.body.dataset.caps !== undefined);
  const caps = page.document.body.dataset.caps;
  assert.match(caps, /access_point/);
  assert.match(caps, /set_clock/);
  assert.doesNotMatch(caps, /platform/, "the platform name is not a capability");
  assert.deepEqual(rowsOf(page.document).filter((row) => row.hidden), []);
  const network = tilesOf(page.document, "network");
  assert.equal(network.find((tile) => tile.page === "accesspoint").hidden, false);
  assert.equal(network.find((tile) => tile.page === "clients").hidden, false);
  await page.close();
});

test("a container is offered none of what it cannot do", async (t) => {
  t.diagnostic("no clock to set, no GPIO pin, no USB port, no access point");
  const page = withCaps(DOCKER);
  await until(() => page.document.body.dataset.caps !== undefined);
  const rows = rowsOf(page.document);
  assert.ok(rows.length >= 4, "the rows carry data-needs: " + rows.length);
  assert.deepEqual(rows.filter((row) => !row.hidden), [],
                   "every row that needs a capability is closed");
  for (const need of ["gpio", "set_clock", "usb_gadget"]) {
    assert.ok(rows.some((row) => row.needs === need), "a row asks for " + need);
  }
  await page.close();
});

test("a card the machine cannot honour leaves the area's menu", async (t) => {
  t.diagnostic("the tiles are the menu, so a hidden tile is a page nobody can open");
  const page = withCaps(DOCKER);
  await until(() => page.document.body.dataset.caps !== undefined);
  const network = tilesOf(page.document, "network");
  for (const wanted of ["accesspoint", "clients", "previous", "banned", "guest", "homewifi"]) {
    const tile = network.find((entry) => entry.page === wanted);
    assert.ok(tile, wanted + " is a page of the network area");
    assert.equal(tile.hidden, true, wanted + " is not offered");
  }
  await page.close();
});

test("an area with nothing left is not a tab any more", async (t) => {
  t.diagnostic("on a container the Network area IS the access point and its clients");
  const page = withCaps(DOCKER);
  await until(() => page.document.body.dataset.caps !== undefined);
  const network = page.document.querySelector('.tab-btn[data-tab="network"]');
  assert.equal(network.hidden, true, "its button goes");
  assert.ok(network.hasAttribute("data-empty"));
  const audio = page.document.querySelector('.tab-btn[data-tab="audio"]');
  assert.equal(audio.hidden, false, "a container still has its sound card and Bluetooth");
  await page.close();
});

test("a page left open in an area that emptied falls back to Home", async (t) => {
  t.diagnostic("never an empty grid: the landing page is Home");
  const page = withCaps(DOCKER);
  await until(() => page.document.body.dataset.caps !== undefined);
  page.window.location.hash = "#network/accesspoint";
  page.window.dispatchEvent(new page.window.Event("hashchange"));
  await until(() => page.document.body.dataset.tab === "home");
  const cards = Array.from(page.document.querySelectorAll('.card[data-tab="network"]'))
    .filter((card) => page.window.getComputedStyle(card).display !== "none"
                      && !card.classList.contains("tab-hidden"));
  assert.equal(cards.length, 0, "and no network card is left on screen");
  await page.close();
});

test("the Pi keeps its areas", async (t) => {
  t.diagnostic("the Stats button is a detail one and only shows in the full view");
  const page = withCaps(PI);
  await until(() => page.document.body.dataset.caps !== undefined);
  const buttons = Array.from(page.document.querySelectorAll(".tab-btn[data-tab]"))
    .filter((btn) => btn.dataset.level !== "detail");
  assert.deepEqual(buttons.filter((btn) => btn.hidden).map((btn) => btn.dataset.tab), []);
  assert.equal(page.document.querySelector('.tab-btn[data-tab="network"]').hasAttribute("data-empty"),
               false);
  await page.close();
});

test("a capability the machine has keeps its page", async (t) => {
  t.diagnostic("the same pages, on a machine that can do them");
  const page = withCaps(PI);
  await until(() => page.document.body.dataset.caps !== undefined);
  const network = tilesOf(page.document, "network");
  for (const wanted of ["accesspoint", "clients"]) {
    assert.equal(network.find((entry) => entry.page === wanted).hidden, false, wanted);
  }
  await page.close();
});

test("a capability that only takes one page away leaves the others alone", async (t) => {
  t.diagnostic("a container that has Bluetooth but no access point and no Wi-Fi card");
  const caps = Object.assign({}, PI, { access_point: false, wireless: false, gpio: false });
  const page = withCaps(caps);
  await until(() => page.document.body.dataset.caps !== undefined);
  const network = tilesOf(page.document, "network");
  assert.equal(network.find((entry) => entry.page === "accesspoint").hidden, true);
  assert.equal(network.find((entry) => entry.page === "homewifi").hidden, true);
  const system = tilesOf(page.document, "system");
  assert.equal(system.find((entry) => entry.page === "clock").hidden, false,
               "the clock can still be read without being settable");
  await page.close();
});

test("nothing is dropped before the first status answer", async (t) => {
  t.diagnostic("the capabilities arrive with /api/status: until then, show what there is");
  const page = load({ routes: { "GET /api/status": new Promise(() => {}) } });
  await until(() => page.document.querySelectorAll(".page-tile").length > 5);
  assert.equal(page.document.body.dataset.caps, undefined);
  const tiles = tilesOf(page.document, "network").filter((tile) => !tile.hidden);
  assert.ok(tiles.length > 0, "the menu is not empty while waiting");
  await page.close();
});
