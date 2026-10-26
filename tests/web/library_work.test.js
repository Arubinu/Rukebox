"use strict";
// The side menu says what the catalogue is doing in the background, and is silent at rest.
const test = require("node:test");
const assert = require("node:assert/strict");

const { load, until } = require("./harness");

test("tags being read show the songs left", async () => {
  const p = load({ hash: "#home/player", routes: {
    "GET /api/library/work": { stage: "tags", total: 200, left: 50 },
  } });
  await until(() => !p.$("navLibrary").hidden, 8000);
  assert.match(p.$("navLibraryLine").textContent, /Reading the tags: 50 songs left/);
  assert.equal(p.$("navLibraryBar").style.width, "75%");
  await p.close();
});

test("a scan has no count, and a catalogue at rest shows nothing", async () => {
  const scan = load({ hash: "#home/player", routes: {
    "GET /api/library/work": { stage: "scan", total: 200, left: null },
  } });
  await until(() => !scan.$("navLibrary").hidden, 8000);
  assert.match(scan.$("navLibraryLine").textContent, /Looking for new files/);
  await scan.close();
  const rest = load({ hash: "#home/player", routes: {
    "GET /api/library/work": { stage: null, total: 200, left: null },
  } });
  await until(() => rest.sent("GET", "/api/library/work").length > 0, 8000);
  await until(() => rest.$("bootOverlay").hidden, 8000);
  assert.equal(rest.$("navLibrary").hidden, true);
  await rest.close();
});

test("a sync started elsewhere shows in the side menu, with its name and progress", async () => {
  const p = load({ hash: "#home/player", routes: {
    "GET /api/sync/status": { seq: 1, held: {}, mirror: {}, runs: [], running: {
      job: "j1", name: "NAS music", started: 0, progress: { done: 3, total: 12, bytes: 40, total_bytes: 160 } } },
  } });
  await until(() => !p.$("navSync").hidden, 8000);
  assert.match(p.$("navSyncLine").textContent, /NAS music: 3 of 12 files, 25 %/);
  assert.equal(p.$("navSyncBar").style.width, "25%");
  await p.close();
});
