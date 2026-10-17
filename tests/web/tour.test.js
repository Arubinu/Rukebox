"use strict";
// The guided tour (web/js/init/28-tour.js): the frame once on the first arrival, a page's
// own the first time it opens, skipped for good, and played again from the header's button.
const test = require("node:test");
const assert = require("node:assert/strict");

const { until, open } = require("./harness");

function bubble(page) {
  return page.document.querySelector(".tour-bubble");
}

test("the frame's tour comes once, step by step, and is remembered", async (t) => {
  const page = open(t, { tour: true });
  const first = await until(() => bubble(page));
  assert.match(first.querySelector(".tour-text").textContent, /areas/);
  assert.ok(page.document.querySelector(".tabbar").classList.contains("tour-target"));
  const steps = Number(first.querySelector(".tour-count").textContent.split("/")[1]);
  for (let i = 1; i < steps; i += 1) first.querySelector(".tour-next").click();
  assert.equal(first.querySelector(".tour-next").textContent, "Done");
  first.querySelector(".tour-next").click();
  assert.equal(bubble(page), null);
  assert.equal(page.document.querySelectorAll(".tour-target").length, 0);
  assert.deepEqual(JSON.parse(page.window.localStorage.getItem("rukebox_tour_seen")), ["shell"]);
});

test("a page that is not obvious has its own tour, the first time only", async (t) => {
  const page = open(t, { tour: true, hash: "#home/lists",
                         storage: { rukebox_tour_seen: JSON.stringify(["shell"]) },
                         routes: { "GET /api/lists": { lists: [], active: null, settings: [] } } });
  const shown = await until(() => bubble(page));
  assert.match(shown.querySelector(".tour-text").textContent, /playlists/);
  shown.querySelector(".tour-skip").click();
  assert.equal(bubble(page), null, "skipped for good");
  assert.ok(JSON.parse(page.window.localStorage.getItem("rukebox_tour_seen")).includes("home/lists"));
});

test("the header's button plays the tour again", async (t) => {
  const page = open(t, { tour: true, storage: { rukebox_tour_seen: JSON.stringify(["shell"]) } });
  await until(() => page.$("bootOverlay").hidden);
  await new Promise((done) => setTimeout(done, 800));
  assert.equal(bubble(page), null, "nothing new to show");
  page.$("tourBtn").click();
  await until(() => bubble(page));
  assert.equal(page.window.localStorage.getItem("rukebox_tour_seen"), null, "every page's tour comes back too");
});
