"use strict";
// The night cap (web/app.js): its fields show while it is on, say what 100% and 50% play at,
// and the player says when it applies, the volume shown staying the one chosen.
// The cap is one of two shapes now, so the card's mode choice is what shows it.
const test = require("node:test");
const assert = require("node:assert/strict");

const { load, until, STATUS } = require("./harness");

const SETTINGS = { "playback.base_volume": 70, "playback.volume_cap_enabled": true, "playback.volume_cap_percent": 60,
                   "playback.volume_cap_start_hour": 21, "playback.volume_cap_start_minute": 30, "playback.volume_cap_end_hour": 7,
                   "playback.volume_cap_end_minute": 0 };

test("the cap's fields say what the volume shown plays at, and hide while it is off", async () => {
  // A radio that updated has no shape chosen: the cap's own switch still says.
  const page = load({ hash: "#settings/volume", routes: { "GET /api/settings": SETTINGS } });
  await until(() => page.$("volumeCapOn").checked);
  await until(() => !page.$("volumeCapRow").hidden);
  assert.equal(page.$("volumeShape").value, "cap",
               "nothing is named in the file, so the old switch is what it does");
  assert.equal(page.$("volumeShapeOld").hidden, false, "and the card says so");
  assert.equal(page.$("volumeCapStart").value, "21:30");
  assert.equal(page.$("volumeCapEnd").value, "07:00");
  assert.equal(page.$("volumeCapExample").textContent,
               "The volume is then a share of this maximum: 100% shown plays at 60%, 50% at 30%.");
  assert.equal(page.$("volumeCurveEditor").hidden, true, "and no strip to draw");
  page.$("volumeCapPercent").value = "40";
  page.$("volumeCapPercent").dispatchEvent(new page.window.Event("input"));
  assert.match(page.$("volumeCapExample").textContent, /plays at 40%, 50% at 20%/);
  // Choosing "nothing" from the card hides the cap's fields, whatever the
  // switch a version before the curve used to read says.
  page.$("volumeShape").value = "off";
  page.$("volumeShape").dispatchEvent(new page.window.Event("change"));
  assert.equal(page.$("volumeCapOn").checked, false, "the old switch follows the choice");
  assert.equal(page.$("volumeCapRow").hidden, true);
  assert.equal(page.$("volumeCapHoursRow").hidden, true);
  assert.equal(page.$("volumeCapFadeRow").hidden, true);
  await page.close();
});

test("choosing the curve shows the strip, and choosing the cap brings its fields back", async () => {
  const page = load({ hash: "#settings/volume", routes: {
    "GET /api/settings": SETTINGS,
    "GET /api/volume_curve": { points: [{ at: 360, level: 40 }, { at: 1320, level: 100 }],
                               summary: [], now: { minute: 0, level: 100 } },
  } });
  // The settings land first: the card reads the file, then the choice is mine.
  await until(() => page.$("volumeShapeOld").hidden === false);
  page.$("volumeShape").value = "curve";
  page.$("volumeShape").dispatchEvent(new page.window.Event("change"));
  assert.equal(page.$("volumeCapRow").hidden, true, "the cap's fields step aside");
  assert.equal(page.$("volumeCurveEditor").hidden, false, "and the strip is there");
  assert.equal(page.$("volumeCapOn").checked, false);
  assert.equal(page.$("volumeCapOldRow").hidden, true, "the old switch is never shown");
  await until(() => page.$("volumeCurveSvg").querySelectorAll(".curve-dot").length === 2);
  page.$("volumeShape").value = "cap";
  page.$("volumeShape").dispatchEvent(new page.window.Event("change"));
  assert.equal(page.$("volumeCapOn").checked, true);
  assert.equal(page.$("volumeCapRow").hidden, false);
  assert.equal(page.$("volumeCurveEditor").hidden, true);
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
