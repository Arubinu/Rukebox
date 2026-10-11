"use strict";
// Add music (web/app.js): the line over the folder counts the songs as well as the files, since
// lyrics and covers are files too and the library only lists songs.
const test = require("node:test");
const assert = require("node:assert/strict");

const { load, until } = require("./harness");

test("the library line says how many songs among the files", async () => {
  const files = [
    { path: "A/song.mp3", size: 4000 }, { path: "A/song.lrc", size: 10 }, { path: "A/cover.jpg", size: 500 },
    { path: "B/tune.FLAC", size: 9000 }, { path: "B/tune.txt", size: 10 },
  ];
  const page = load({ hash: "#home/music", routes: {
    "GET /api/music/manifest": { exists: true, dir: "/music", files, free_bytes: 1024 * 1024 * 1024 },
  } });
  await until(() => /songs/.test(page.$("musicLibraryLine").textContent), 8000);
  assert.match(page.$("musicLibraryLine").textContent, /^2 songs, 5 files in all · /);
  await page.close();
});
