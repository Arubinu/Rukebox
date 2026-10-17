"use strict";
// The update card (web/js/init/22-system-update.js): what a release changes is shown before it is
// installed, and a new major version is installed only once what it implies was accepted.
const test = require("node:test");
const assert = require("node:assert/strict");

const { until, open } = require("./harness");

const BADGE = "![](https://img.shields.io/badge/tree--hash-cc6da6fbc80d-blue)";

function offer(major) {
  return {
    tag: major ? "v2.0.0" : "v1.19.2", newer: true, major, allowed: true, installed: "v1.19.1",
    published_at: "2026-11-01T00:00:00Z",
    changes: [{ tag: major ? "v2.0.0" : "v1.19.2", published_at: "2026-11-01T00:00:00Z",
                notes: "# Title " + BADGE + "\n\n> Updating? Read <b>this</b>.\n\n- [notes](https://example/n)" }],
  };
}

test("what a release changes is listed before installing it, drawn without any HTML", async (t) => {
  const page = open(t, { hash: "#system/update", view: "detailed",
                         routes: { "GET /api/update/release": offer(false), "POST /api/update/release": {} } });
  await until(() => page.sent("GET", "/api/update/status").length);
  page.$("btnReleaseCheck").click();
  await until(() => !page.$("releaseChanges").hidden);
  const list = page.$("releaseChangesList");
  assert.equal(list.querySelector("h4").textContent.trim(), "Title", "the badge is no picture here");
  assert.equal(list.querySelector("img"), null);
  assert.equal(list.querySelector("b"), null, "markup in the notes stays text");
  assert.match(list.querySelector(".release-callout").textContent, /<b>this<\/b>/);
  const link = list.querySelector("a");
  assert.equal(link.rel, "noopener noreferrer");
  assert.equal(link.href, "https://example/n");
  assert.match(page.$("releaseStatus").textContent, /v1\.19\.2/);
  assert.equal(page.sent("GET", "/api/update/release")[0].query.get("lang"), "en");

  page.$("btnReleaseInstall").click();
  await until(() => !page.$("modalOverlay").hidden);
  page.$("modalOk").click();
  await until(() => page.sent("POST", "/api/update/release").length);
  assert.deepEqual(page.sent("POST", "/api/update/release")[0].body, {});
});

test("a new major version asks for its own confirmation, saying it is a migration", async (t) => {
  const page = open(t, { hash: "#system/update", view: "detailed",
                         routes: { "GET /api/update/release": offer(true), "POST /api/update/release": {} } });
  await until(() => page.sent("GET", "/api/update/status").length);
  page.$("btnReleaseCheck").click();
  await until(() => !page.$("btnReleaseInstall").hidden);
  assert.match(page.$("releaseStatus").textContent, /major version v2\.0\.0/);

  page.$("btnReleaseInstall").click();
  await until(() => !page.$("modalOverlay").hidden);
  assert.equal(page.$("modalTitle").textContent, "Switch to v2.0.0?");
  assert.match(page.$("modalBody").textContent, /not an ordinary update: v2\.0\.0 is a new major version/);
  page.$("modalCancel").click();
  await until(() => page.$("modalOverlay").hidden);
  assert.equal(page.sent("POST", "/api/update/release").length, 0, "declined: nothing installed");

  page.$("btnReleaseInstall").click();
  await until(() => !page.$("modalOverlay").hidden);
  const go = page.$("modalChoices").querySelector("button");
  assert.equal(go.textContent, "Switch to v2.0.0");
  go.click();
  await until(() => page.sent("POST", "/api/update/release").length);
  assert.deepEqual(page.sent("POST", "/api/update/release")[0].body, { major: true });
});
