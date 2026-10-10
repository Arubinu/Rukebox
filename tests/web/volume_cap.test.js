"use strict";
// The night cap (web/app.js): its fields show while it is on, say what 100% and 50% play at,
// and the player says when it applies, the volume shown staying the one chosen.
const test = require("node:test");
const assert = require("node:assert/strict");

const { load, until, STATUS } = require("./harness");

const SETTINGS = { BASE_VOLUME: 70, VOLUME_CAP_ENABLED: true, VOLUME_CAP_PERCENT: 60,
                   VOLUME_CAP_START_HOUR: 21, VOLUME_CAP_START_MINUTE: 30, VOLUME_CAP_END_HOUR: 7,
                   VOLUME_CAP_END_MINUTE: 0 };

test("the cap's fields say what the volume shown plays at, and hide while it is off", async () => {
  const page = load({ hash: "#settings/volume", routes: { "GET /api/settings": SETTINGS } });
  await until(() => page.$("volumeCapOn").checked);
  await until(() => !page.$("volumeCapRow").hidden);
  assert.equal(page.$("volumeCapStart").value, "21:30");
  assert.equal(page.$("volumeCapEnd").value, "07:00");
  assert.equal(page.$("volumeCapExample").textContent,
               "The volume is then a share of this maximum: 100% shown plays at 60%, 50% at 30%.");
  page.$("volumeCapPercent").value = "40";
  page.$("volumeCapPercent").dispatchEvent(new page.window.Event("input"));
  assert.match(page.$("volumeCapExample").textContent, /plays at 40%, 50% at 20%/);
  page.$("volumeCapOn").checked = false;
  page.$("volumeCapOn").dispatchEvent(new page.window.Event("change"));
  assert.equal(page.$("volumeCapRow").hidden, true);
  assert.equal(page.$("volumeCapHoursRow").hidden, true);
  await page.close();
});

test("while the cap applies the player says so, and shows the volume chosen", async () => {
  const page = load({ hash: "#home/player", routes: {
    "GET /api/status": Object.assign({}, STATUS, { volume: 100, volume_cap: 60 }) } });
  await until(() => !page.$("npNotices").hidden);
  assert.match(page.$("npNotices").textContent, /Night volume: the volume shown plays at 60% of itself\./);
  assert.equal(page.$("volumeValue").textContent, "100");
  await page.close();
});
