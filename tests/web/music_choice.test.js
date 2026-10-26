"use strict";
// Add music (web/js/init/23-music-sync.js): a folder chosen before the page knows what the radio
// holds still gets an answer - the plan, "already there", or that the radio did not say.
const test = require("node:test");
const assert = require("node:assert/strict");

const { load, until } = require("./harness");

const STAMP = Date.UTC(2026, 0, 1);
const HELD = { exists: true, dir: "/music", files: [{ path: "song.mp3", size: 5, mtime: STAMP / 1000 }],
               free_bytes: 1024 * 1024 * 1024 };

function choose(page, names) {
  const input = page.$("musicFolderInput");
  const files = names.map((name) => new page.window.File(["12345"], name, { lastModified: STAMP }));
  Object.defineProperty(input, "files", { value: files, configurable: true });
  input.dispatchEvent(new page.window.Event("change", { bubbles: true }));
}

test("a folder already on the radio says so, even when the radio was slow to answer", async () => {
  let calls = 0;
  const page = load({ hash: "#home/music", routes: {
    // The first look, as the page boots, gets nothing: the radio was busy.
    "GET /api/music/manifest": () => (++calls === 1 ? undefined : HELD),
  } });
  await until(() => calls === 1);
  choose(page, ["song.mp3"]);
  await until(() => page.document.body.textContent.includes("Everything is already on the radio"));
  assert.equal(calls, 2, "it asked the radio again");
  await page.close();
});

test("a radio that does not say what it holds is told, not left silent", async () => {
  const page = load({ hash: "#home/music", routes: {} });
  await until(() => page.sent("GET", "/api/music/manifest").length === 1);
  choose(page, ["song.mp3"]);
  await until(() => page.document.body.textContent.includes("choose the folder again"));
  await page.close();
});

test("a new song is planned at once when the radio is known", async () => {
  const page = load({ hash: "#home/music", routes: { "GET /api/music/manifest": HELD } });
  await until(() => /songs/.test(page.$("musicLibraryLine").textContent));
  choose(page, ["new.mp3"]);
  await until(() => !page.$("musicPlanBox").hidden);
  assert.match(page.$("musicPlanLine").textContent, /^1 files to send/);
  await page.close();
});
