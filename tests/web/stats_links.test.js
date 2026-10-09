"use strict";
// The statistics lead back to the music (web/app.js): a song the library holds
// opens the library already searched, and the log names settings as their page does.
const test = require("node:test");
const assert = require("node:assert/strict");

const { load, until, STATUS } = require("./harness");

const SUMMARY = {
  enabled: true, days: 14, totals: {}, chart: [], sessions: { recent: [] },
  top: {
    music: [{ name: "Take On Me.opus", count: 12, seconds: 2500, in_library: true },
            { name: "Gone.mp3", count: 3, seconds: 600, in_library: false }],
    meme: [], cutoff_announce: [], custom_announce: [],
    error: [{ name: "Broken.flac", count: 4, seconds: 0, in_library: true }],
  },
};

function withStats(extra, hash) {
  return load({
    hash: hash || "#stats/tops",
    view: "detailed",
    routes: Object.assign({
      "GET /api/status": STATUS,
      "GET /api/journal/summary": SUMMARY,
      "GET /api/library": { items: [{ key: "k1", title: "Take On Me", artist: "a-ha" }], total: 1,
                            status: { total: 120, read: 120 } },
      "GET /api/library/facets": { artists: [{ name: "a-ha", count: 3 }], albums: [], genres: [] },
    }, extra || {}),
  });
}

async function shown(page, tab, name) {
  await until(() => page.document.body.dataset.caps !== undefined);
  page.document.dispatchEvent(new page.window.CustomEvent("page-shown", { detail: { tab, page: name } }));
}

test("a most played song the library holds opens the library searched for it", async () => {
  const page = withStats();
  await shown(page, "stats", "tops");
  const links = await until(() => {
    const found = [...page.document.querySelectorAll("#topsBox .library-link")];
    return found.length && found;
  });
  assert.deepEqual(links.map((b) => b.textContent), ["Take On Me.opus"],
                   "a song missing from the library is plain text");
  assert.equal(page.document.querySelectorAll("#errorsBox .library-link").length, 1,
               "a playback error links too");

  links[0].click();
  await until(() => page.sent("GET", "/api/library").some((r) => r.query.get("q") === "Take On Me.opus"));
  assert.equal(page.$("librarySearch").value, "Take On Me.opus");
  assert.equal(page.window.location.hash, "#home/library");
  await until(() => page.document.querySelectorAll("#libraryList li").length === 1);
  assert.equal(page.$("libraryCount").textContent, "1 track found");
  await page.close();
});

test("a recap artist opens the library on that artist", async () => {
  const page = withStats({
    "GET /api/journal/recap": { enabled: true, seconds_music: 900, tracks_played: 4, days: 1, likes: 0,
                                artists: 1, best_day: null, morning: null,
                                top_artists: [{ artist: "a-ha", count: 4 }],
                                top_tracks: [{ title: "Take On Me", artist: "a-ha", count: 4,
                                               name: "Take On Me.opus", in_library: true }] },
  }, "#stats/recap");
  await shown(page, "stats", "recap");
  const artist = await until(() => page.document.querySelector("#recapArtists .library-link"));
  assert.ok(page.document.querySelector("#recapTracks .library-link"), "a recap song links too");
  artist.click();
  await until(() => page.sent("GET", "/api/library").some((r) => r.query.get("artist") === "a-ha"));
  assert.equal(page.$("libraryArtist").value, "a-ha");
  assert.equal(page.$("librarySearch").value, "");
  await page.close();
});

test("the log names applied settings as their page does", async () => {
  const page = withStats({
    "GET /api/journal/entries": { events: [
      { id: 1, ts: 1790928000, clock_ok: true, type: "config_reloaded", label: "WEB_BEHIND_PROXY, NOT_A_FIELD",
        detail: { applied: ["WEB_BEHIND_PROXY", "NOT_A_FIELD"], restart: [] } }],
      types: ["config_reloaded"], total: 1 },
  }, "#stats/events");
  await shown(page, "stats", "events");
  const row = await until(() => page.document.querySelector("#eventList .event-row"));
  assert.match(row.textContent, /Behind a reverse proxy/);
  assert.doesNotMatch(row.textContent, /WEB_BEHIND_PROXY/);
  assert.match(row.textContent, /NOT_A_FIELD/, "a key no field shows stays as it is");
  await page.close();
});
