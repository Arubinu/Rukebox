"use strict";
// M3U in the lists page (web/js/init/14-lists.js): a list goes out as a download of its own
// route, a file taken in becomes a list that opens, and what was not found is said.
const test = require("node:test");
const assert = require("node:assert/strict");

const { until, open } = require("./harness");

const LISTS = { lists: [{ id: "soir", name: "Soir", kind: "manual", count: 1 }], active: null, settings: [] };

test("a list is exported by its own route", async (t) => {
  const page = open(t, { hash: "#home/lists", routes: {
    "GET /api/lists": LISTS,
    "GET /api/lists/soir/tracks": { items: [{ key: "k1", title: "One", path: "/m/one.mp3" }], missing: 0 },
  } });
  await until(() => page.$("listsList").children.length === 1);
  page.$("listsList").querySelector(".ann-head").click();
  const button = await until(() => [...page.$("listsDetailManage").querySelectorAll("button")]
    .find((b) => b.dataset.icon === "download"));
  const opened = [];
  page.window.HTMLAnchorElement.prototype.click = function () { opened.push(this.getAttribute("href")); };
  button.click();
  assert.deepEqual(opened, ["/api/lists/soir/m3u"]);
});

test("a file taken in becomes a list, and what was not found is said", async (t) => {
  const page = open(t, { hash: "#home/lists", routes: {
    "GET /api/lists": LISTS,
    "GET /api/lists/road/tracks": { items: [], missing: 0 },
    "POST /api/lists/import": { list: { id: "road", name: "Road trip", kind: "manual", count: 2 },
                                found: 2, missing: ["Lost Song"], missing_count: 1 },
  } });
  await until(() => page.$("listsList").children.length === 1);
  const input = page.$("listsImportInput");
  const file = new page.window.File(["#EXTM3U\n"], "Road trip.m3u", { type: "audio/x-mpegurl" });
  Object.defineProperty(input, "files", { value: [file], configurable: true });
  input.dispatchEvent(new page.window.Event("change", { bubbles: true }));
  await until(() => page.sent("POST", "/api/lists/import").length);
  await until(() => /Lost Song/.test(page.document.body.textContent));
  assert.deepEqual(page.errors, []);
});
