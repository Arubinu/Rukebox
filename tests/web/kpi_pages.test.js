"use strict";
// A counter on the Statistics page leads to the page that details it (web/app.js):
// a brief press opens it, a long press still resets the counter.
const test = require("node:test");
const assert = require("node:assert/strict");

const { load, until, wait, STATUS } = require("./harness");

const SUMMARY = {
  enabled: true, days: 14, chart: [], sessions: { recent: [] }, totals: {},
  counters: { seconds_music: 3600, tracks_played: 40, clicks_single: 9,
              sessions_started: 7, playback_errors: 3 },
  top: { music: [], meme: [], cutoff_announce: [], custom_announce: [], error: [] },
};

function withCounters() {
  return load({
    hash: "#stats/overview",
    view: "detailed",
    routes: {
      "GET /api/status": STATUS,
      "GET /api/journal/summary": SUMMARY,
      "GET /api/journal/daily": [],
      "POST /api/journal/reset_counters": {},
    },
  });
}

function tileOf(page, label) {
  return [...page.document.querySelectorAll("#kpiGrid .kpi")]
    .find((one) => one.querySelector(".kpi-label").textContent === label) || null;
}

/* jsdom has no PointerEvent: the listeners only read the type and the mouse fields. */
function pointer(page, tile, type) {
  tile.dispatchEvent(new page.window.MouseEvent(type, { bubbles: true, button: 0,
                                                        clientX: 10, clientY: 10 }));
}

async function press(page, tile, ms) {
  pointer(page, tile, "pointerdown");
  await wait(ms);
  pointer(page, tile, "pointerup");
}

test("a brief press opens the page that details the counter", async (t) => {
  const page = withCounters();
  const wanted = [["Music listened", "#stats/recap"], ["Tracks played", "#stats/tops"],
                  ["Startups", "#stats/sessions"], ["Shutdowns", "#stats/sessions"],
                  ["Playback errors", "#stats/errors"]];
  await until(() => tileOf(page, "Playback errors"));
  for (const [label, hash] of wanted) {
    const tile = await until(() => tileOf(page, label));
    await press(page, tile, 60);
    assert.equal(page.window.location.hash, hash, label);
    t.diagnostic(label + " opens " + hash);
  }
  assert.equal(page.sent("POST", "/api/journal/reset_counters").length, 0,
               "a brief press resets nothing");
  await page.close();
});

test("a counter with no page of its own opens nothing", async () => {
  const page = withCounters();
  const tile = await until(() => tileOf(page, "Button presses"));
  assert.equal(tile.classList.contains("kpi-link"), false, "it does not claim a page");
  await press(page, tile, 60);
  assert.equal(page.window.location.hash, "#stats/overview", "the page stays where it was");
  await page.close();
});

test("a long press still asks before resetting, and opens nothing", async () => {
  const page = withCounters();
  const tile = await until(() => tileOf(page, "Playback errors"));
  await press(page, tile, 900);
  await until(() => !page.$("modalOverlay").hidden);
  assert.match(page.$("modalBody").textContent, /Playback errors/, "the dialog names the counter");
  assert.equal(page.sent("POST", "/api/journal/reset_counters").length, 0,
               "nothing goes before the answer");
  assert.equal(page.window.location.hash, "#stats/overview",
               "the release after a hold does not open the page either");
  page.$("modalCancel").click();
  await until(() => page.$("modalOverlay").hidden);
  await page.close();
});
