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

test("a song often passed over can be excluded from its line", async (t) => {
  let excluded = null;
  const page = open(t, { hash: "#stats/tops", view: "detailed", routes: {
    "GET /api/journal/summary": Object.assign(summary({ music: [] }), { often_skipped: [
      { name: "ymca.mp3", key: "k1", title: "Y.M.C.A.", artist: "Village People", skipped: 9, played: 10 }] }),
    "POST /api/excluded": (request) => { excluded = request.body.keys; return { count: 1, total: 1 }; },
  } });
  await until(() => /Often skipped/.test(page.$("topsBox").textContent));
  assert.match(page.$("topsBox").textContent, /9 of 10/);
  const line = [...page.$("topsBox").querySelectorAll("li")].find((li) => /Y\.M\.C\.A\./.test(li.textContent));
  line.querySelector("button[data-icon='circle-minus']").click();
  await until(() => excluded !== null);
  assert.deepEqual(excluded, ["k1"]);
  await until(() => !/Y\.M\.C\.A\./.test(page.$("topsBox").textContent));
});

test("a few songs never played are offered, each one a tap from Up next", async (t) => {
  let queued = null;
  const page = open(t, { hash: "#stats/tops", view: "detailed", routes: {
    "GET /api/journal/summary": summary({ music: [] }),
    "GET /api/journal/never_played": { enabled: true, total: 1234, items: [
      { key: "k1", title: "Forgotten", artist: "Someone", name: "forgotten" }] },
    "POST /api/library/queue": (request) => { queued = request.body.key; return {}; },
  } });
  await until(() => !page.$("neverPlayedBox").hidden && page.$("neverPlayedList").children.length === 1, 8000);
  assert.match(page.$("neverPlayedCount").textContent, /1,234 songs/);
  assert.equal(page.$("neverPlayedMore").hidden, false);
  page.$("neverPlayedList").querySelector(".library-next").click();
  await until(() => queued !== null);
  assert.equal(queued, "k1");
});

test("the liked songs play at once or next, for whoever may put a list on", async (t) => {
  const sent = [];
  const likes = (mayPlay) => ({ tracks: [{ key: "k1", title: "Liked", artist: "A", liked_at: 1 }], keys: ["k1"], may_play: mayPlay });
  const page = open(t, { hash: "#home/likes", routes: {
    "GET /api/likes": likes(true),
    "POST /api/likes/play": (request) => { sent.push(request.body.now); return { added: 1, of: 1, already: 0 }; },
  } });
  await until(() => !page.$("likesPlayActions").hidden, 8000);
  page.$("likesQueue").click();
  await until(() => sent.length === 1);
  page.$("likesPlay").click();
  await until(() => sent.length === 2);
  assert.deepEqual(sent, [false, true]);

  const barred = open(t, { hash: "#home/likes", routes: { "GET /api/likes": likes(false) } });
  await until(() => barred.$("likesList").children.length === 1, 8000);
  assert.equal(barred.$("likesPlayActions").hidden, true);
});
