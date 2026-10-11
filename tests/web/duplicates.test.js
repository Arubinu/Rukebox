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
