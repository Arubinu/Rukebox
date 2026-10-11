"use strict";
// The duplicates (web/app.js): a tap on a copy's name shows every copy's place in the music
// folder, another shows the names again.
const test = require("node:test");
const assert = require("node:assert/strict");

const { load, until } = require("./harness");

const GROUPS = { groups: [{ artist: "a-ha", title: "Take On Me", same_file: false, reclaimable: 4000, tracks: [
  { key: "k1", path: "/home/pi/audio/music/80s/take on me.mp3", rel: "80s/take on me.mp3",
    name: "take on me.mp3", size: 4000, duration: 225 },
  { key: "k2", path: "/home/pi/audio/music/Best of/01.mp3", rel: "Best of/01.mp3",
    name: "01.mp3", size: 5000, duration: 225 },
] }] };

test("a tap on one name shows every copy's path, and another tap the names", async () => {
  const page = load({ hash: "#home/duplicates", view: "detailed", routes: { "GET /api/library/duplicates": GROUPS } });
  await until(() => page.document.querySelectorAll("#duplicatesList .duplicate-name").length === 2, 8000);
  const names = () => [...page.document.querySelectorAll("#duplicatesList .duplicate-name")].map((el) => el.textContent);
  assert.deepEqual(names(), ["take on me.mp3", "01.mp3"]);
  page.document.querySelectorAll("#duplicatesList .duplicate-name")[1].click();
  assert.deepEqual(names(), ["80s/take on me.mp3", "Best of/01.mp3"]);
  page.document.querySelectorAll("#duplicatesList .duplicate-name")[0].click();
  assert.deepEqual(names(), ["take on me.mp3", "01.mp3"]);
  await page.close();
});

test("each copy is kept or set aside on its own, never the last one kept, and a group can be ignored", async () => {
  const groups = { groups: [
    { key: "a|one", artist: "A", title: "One", done: false, ignored: false, tracks: [
      { key: "k1", path: "/m/1.mp3", rel: "1.mp3", name: "1.mp3", size: 1, duration: 1, hidden: false },
      { key: "k2", path: "/m/2.mp3", rel: "2.mp3", name: "2.mp3", size: 1, duration: 1, hidden: true },
    ] },
    { key: "a|two", artist: "A", title: "Two", done: true, ignored: true, tracks: [
      { key: "k3", path: "/m/3.mp3", rel: "3.mp3", name: "3.mp3", size: 1, duration: 1, hidden: false },
      { key: "k4", path: "/m/4.mp3", rel: "4.mp3", name: "4.mp3", size: 1, duration: 1, hidden: false },
    ] },
  ] };
  const page = load({ hash: "#home/duplicates", view: "detailed", routes: {
    "GET /api/library/duplicates": groups, "POST /api/library/hide": {}, "POST /api/library/duplicates/ignore": {} } });
  await until(() => page.document.querySelectorAll("#duplicatesList .duplicate-group").length === 2);
  const [first, second] = page.document.querySelectorAll("#duplicatesList .duplicate-group");
  const keeps = [...first.querySelectorAll(".duplicate-keep")];
  assert.equal(keeps[0].disabled, true, "the only copy kept stays kept");
  assert.equal(keeps[1].getAttribute("aria-pressed"), "false");
  keeps[1].click();
  await until(() => page.sent("POST", "/api/library/hide").length);
  assert.deepEqual(page.sent("POST", "/api/library/hide")[0].body, { key: "k2", hidden: false, path: "/m/2.mp3" });
  assert.equal(first.querySelector(".badge").classList.contains("duplicate-done"), false);
  assert.equal(second.querySelector(".badge").classList.contains("duplicate-done"), true);
  assert.equal(second.querySelector(".badge").textContent, "not a duplicate");
  second.querySelector(".duplicate-actions button").click();
  await until(() => page.sent("POST", "/api/library/duplicates/ignore").length);
  assert.deepEqual(page.sent("POST", "/api/library/duplicates/ignore")[0].body, { key: "a|two", ignored: false });
  await page.close();
});
