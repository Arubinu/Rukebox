"use strict";
// Two small things the page says: who asks the most (only while the statistics keep it,
// web/js/init/20-journal.js), and that what plays was sent to the radio (02-ui.js).
const test = require("node:test");
const assert = require("node:assert/strict");

const { until, open, STATUS } = require("./harness");

function summary(top) {
  return { enabled: true, days: 14, totals: {}, chart: [], top };
}

test("who asks the most is listed only when the statistics keep it", async (t) => {
  const kept = open(t, { hash: "#stats/tops", view: "detailed", routes: {
    "GET /api/journal/summary": summary({ music: [], person: [{ name: "Marie", count: 4 }] }),
  } });
  await until(() => /Who asks the most/.test(kept.$("topsBox").textContent));
  assert.match(kept.$("topsBox").textContent, /Marie/);
  assert.match(kept.$("topsBox").textContent, /4x/);

  const off = open(t, { hash: "#stats/tops", view: "detailed", routes: {
    "GET /api/journal/summary": summary({ music: [] }),
  } });
  await until(() => off.$("topsBox").querySelector(".top-block"));
  assert.doesNotMatch(off.$("topsBox").textContent, /Who asks the most/);
});

test("music sent to the radio is named so on the player", async (t) => {
  const page = open(t, { routes: {
    "GET /api/status": Object.assign({}, STATUS, { mode: "cast", track_title: "Sent song",
                                                   cast: { url: "http://phone/song.mp3", title: "Sent song" } }),
  } });
  await until(() => page.$("npMode").textContent === "Music sent");
  assert.deepEqual(page.errors, []);
});
