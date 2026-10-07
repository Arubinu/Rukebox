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
                 captive_portal: false, gpio: false, local_audio: true, music_upload: false,
                 power: false, rtc: false, self_update: false, set_clock: false,
                 usb_gadget: false, wireless: false };

function withCaps(caps, options = {}) {
  // A caller that brings its own status keeps it: this only fills in the
  // capabilities when the test has nothing more specific to say.
  const routes = Object.assign({}, options.routes);
  if (!routes["GET /api/status"]) {
    routes["GET /api/status"] = Object.assign({}, STATUS, { capabilities: caps });
  }
  return load(Object.assign({}, options, { routes }));
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
  // The Clock page is one value to set, and a container can set neither the
  // time nor its zone: there is nothing left on it to offer. The card itself
  // is hidden by the stylesheet (tests/test_repo.py checks the rule exists).
  const clock = tilesOf(page.document, "system").find((entry) => entry.page === "clock");
  assert.ok(clock, "the clock is a page of the system area");
  assert.equal(clock.hidden, true, "the clock page goes with the rest");
  assert.equal(page.document.querySelector('.card[data-page="clock"]').dataset.needs,
               "set_clock", "the card asks for what the page needs");
  // Adding music uploads files into the music folder, and a container's
  // arrives read-only: the page that does it has nothing to do there.
  const music = tilesOf(page.document, "home").find((entry) => entry.page === "music");
  assert.ok(music, "adding music is a page of the home area");
  assert.equal(music.hidden, true, "and it is not offered");
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

test("a stream that is off says nothing: the setting is the owner's", async (t) => {
  t.diagnostic("\"nothing plays\" has several causes, and a browser cannot see any of them");
  const page = withCaps(PI, {
    routes: { "GET /api/status": Object.assign({}, STATUS, {
      capabilities: PI,
      stream: { enabled: false, available: false, url: "", encoder: "",
                content_type: "", listeners: 0, source: "", why: "off" },
    }) },
  });
  await until(() => page.document.body.dataset.caps !== undefined);
  assert.equal(page.$("btnListen").hidden, true, "no button when there is nothing to hear");
  assert.equal(page.$("listenWhy").hidden, true,
               "the stream is simply switched off: no line under the player");
  await page.close();
});

test("a stream that cannot be served says why", async (t) => {
  t.diagnostic("the stream is on but this machine cannot feed it: that is worth a line");
  const page = withCaps(PI, {
    routes: { "GET /api/status": Object.assign({}, STATUS, {
      capabilities: PI,
      stream: { enabled: true, available: false, url: "", encoder: "",
                content_type: "", listeners: 0, source: "", why: "no_ffmpeg" },
    }) },
  });
  await until(() => page.document.body.dataset.caps !== undefined);
  assert.equal(page.$("btnListen").hidden, true);
  const hint = page.$("listenWhy");
  assert.equal(hint.hidden, false, "the line carries the reason");
  assert.match(hint.textContent, /ffmpeg/i, "and it names what is missing");
  await page.close();
});

test("a stream that is ready shows the button and no excuse", async (t) => {
  t.diagnostic("with the stream on, nothing to explain: the button is just there");
  const page = withCaps(PI, {
    routes: { "GET /api/status": Object.assign({}, STATUS, {
      capabilities: PI,
      stream: { enabled: true, available: true, url: "http://x/stream.opus", encoder: "opus",
                content_type: "audio/ogg", listeners: 0, source: "sink.monitor", why: "" },
    }) },
  });
  await until(() => page.document.body.dataset.caps !== undefined);
  assert.equal(page.$("btnListen").hidden, false);
  assert.equal(page.$("listenWhy").hidden, true);
  await page.close();
});

test("a status that says nothing about the stream is not an accusation", async (t) => {
  t.diagnostic("an older daemon answers the same address: the page keeps quiet");
  const withoutStream = Object.assign({}, STATUS);
  delete withoutStream.stream;
  const page = load({ routes: { "GET /api/status": withoutStream } });
  await until(() => page.document.body.dataset.caps !== undefined);
  assert.equal(page.$("listenWhy").hidden, true);
  assert.equal(page.$("listenWhy").textContent, "");
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
