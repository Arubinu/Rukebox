"use strict";
// The event log's search (web/app.js, src/web_server.py): the query goes to the
// route - so it finds the entries "Load more" has not fetched - and the list
// says so when nothing matches.
const test = require("node:test");
const assert = require("node:assert/strict");

const { load, until, STATUS } = require("./harness");

const EVENTS = [
  { id: 3, ts: 1790928000, clock_ok: true, type: "track_played", label: "Song", detail: null },
  { id: 2, ts: 1790927000, clock_ok: true, type: "playback_error", label: "Broken.opus", detail: null },
  { id: 1, ts: 1790926000, clock_ok: true, type: "announce_played", label: "Morning", detail: null },
];

function entriesFor(request) {
  const needle = (request.query.get("q") || "").toLowerCase();
  const events = needle
    ? EVENTS.filter((e) => (e.type + " " + e.label).toLowerCase().includes(needle))
    : EVENTS;
  return { events, types: ["announce_played", "playback_error", "track_played"],
           total: events.length };
}

function withLog() {
  return load({
    routes: {
      "GET /api/status": STATUS,
      "GET /api/journal/summary": { enabled: true, days: 14, totals: {}, chart: [] },
      "GET /api/journal/entries": entriesFor,
    },
  });
}

/* What the app itself dispatches when the page is shown (setActiveView). The
   app registers that listener after its first await, so the page has to be up. */
async function openLog(page) {
  await until(() => page.document.body.dataset.caps !== undefined);
  page.document.dispatchEvent(new page.window.CustomEvent("page-shown",
    { detail: { tab: "stats", page: "events" } }));
}

const rows = (page) => page.document.querySelectorAll("#eventList .event-row").length;

test("the event log is searched in the database, not in what it loaded", async (t) => {
  const page = withLog();
  await openLog(page);
  await until(() => rows(page) === 3);
  t.diagnostic("three events, as the route answered");

  const search = page.$("eventSearch");
  assert.ok(search, "the log offers a search field");
  search.value = "error";
  search.dispatchEvent(new page.window.Event("input", { bubbles: true }));

  await until(() => page.sent("GET", "/api/journal/entries")
    .some((r) => r.query.get("q") === "error"));
  t.diagnostic("typing asks the route for q=error");
  await until(() => rows(page) === 1);
  assert.match(page.document.getElementById("eventList").textContent, /Broken\.opus/,
               "only the matching event is listed");
  await page.close();
});

test("what the reader reads is what the search searches", async (t) => {
  const page = withLog();
  await openLog(page);
  await until(() => rows(page) === 3);

  // The type names are translated in the page, and the database only knows the
  // keys: typing a word of the name the reader sees must send that key along.
  const option = Array.from(page.document.querySelectorAll("#eventTypeFilter option"))
    .find((one) => one.value);
  assert.ok(option, "the type filter lists the types");
  const word = option.textContent.trim().split(/\s+/)[0];
  t.diagnostic("searching for '" + word + "', from the label '" + option.textContent.trim() + "'");

  const search = page.$("eventSearch");
  search.value = word;
  search.dispatchEvent(new page.window.Event("input", { bubbles: true }));
  const labels = await until(() => {
    const asked = page.sent("GET", "/api/journal/entries")
      .map((r) => r.query.get("labels")).filter(Boolean);
    return asked.length ? asked[asked.length - 1] : null;
  });
  assert.ok(labels.split(",").includes(option.value),
            "the type whose name matched is sent: " + labels + " for " + word);
  await page.close();
});

test("a search that matches nothing says so", async (t) => {
  const page = withLog();
  await openLog(page);
  await until(() => rows(page) === 3);

  const search = page.$("eventSearch");
  search.value = "nothing here";
  search.dispatchEvent(new page.window.Event("input", { bubbles: true }));
  await until(() => page.document.querySelector("#eventList .event-empty"));

  const empty = page.document.querySelector("#eventList .event-empty").textContent;
  assert.notEqual(empty, "", "the list explains itself");
  assert.doesNotMatch(empty, /no event recorded/i, "and it is not the 'nothing ever' line");
  await page.close();
});


/* Erasing the last run (web/app.js): what it holds is said before anything
   goes, and the three ways out of it leave the radio where they say. */
const RUN = { session: 4, started_at: 1790928000, ended_at: null, open: true,
              clock_source: "rtc", tracks: 12, seconds: 3720, events: 40,
              first_ts: 1790924000, last_ts: 1790928000 };

function withRun() {
  let erased = false;
  return load({
    routes: {
      "GET /api/status": STATUS,
      "GET /api/journal/summary": { enabled: true, days: 14, totals: {}, chart: [] },
      "GET /api/journal/run": () => (erased ? null : RUN),
      "POST /api/journal/erase_run": () => {
        erased = true;
        return { session: 4, tracks: 12, events: 40, wins: 1 };
      },
      "POST /api/action/start_music": () => ({ ok: true }),
    },
  });
}

test("the last run is described, and erasing it asks what the radio does next", async (t) => {
  const page = withRun();
  const summary = await until(() => page.$("runSummary").textContent || null);
  t.diagnostic("the page says: " + summary);
  assert.match(summary, /12/, "how many songs it played");
  assert.equal(page.$("btnEraseRun").hidden, false, "the button is offered");

  page.$("btnEraseRun").click();
  await until(() => !page.$("modalOverlay").hidden);
  assert.equal(page.sent("POST", "/api/journal/erase_run").length, 0,
               "nothing is erased before the answer");
  assert.match(page.$("modalBody").textContent, /\d/, "and the dialog says since when");

  const what = [...page.document.querySelectorAll(".run-list li")].map((li) => li.textContent);
  assert.equal(what.length, 3, "the dialog lists what goes");
  t.diagnostic("the dialog lists: " + what.join(" "));
  assert.equal(page.document.querySelectorAll("#modalLegend .timer-legend-line").length, 3,
               "and the legend explains the three ways out");

  const ways = [...page.document.querySelectorAll(".run-choice")];
  assert.deepEqual(ways.map((b) => b.querySelector(".timer-symbol").dataset.icon),
                   ["moon", "play", "power"], "standby, music, off: an icon each");
  assert.deepEqual(ways.map((b) => b.querySelector(".timer-group-title").textContent.trim()),
                   ["Standby", "Music", "Off"], "named under the icon, as in the timer");
  t.diagnostic("the buttons say: " + ways.map((b) => b.textContent.trim()).join(", "));

  ways[1].click();
  const sent = await until(() => {
    const calls = page.sent("POST", "/api/journal/erase_run");
    return calls.length ? calls[0] : null;
  });
  assert.deepEqual(sent.body, { session: 4 }, "the run the page read is the one erased");
  await until(() => page.sent("POST", "/api/action/start_music").length);
  t.diagnostic("and the radio was asked to play what the run had played");

  await until(() => page.$("btnEraseRun").hidden);
  assert.match(page.$("runSummary").textContent, /nothing/i,
               "and the page says there is nothing left to erase");
  await until(() => page.document.querySelector(".toast"));
  t.diagnostic("the toast says: " + page.document.querySelector(".toast").textContent);
  await page.close();
});

test("a start that did nothing yet offers nothing", async (t) => {
  const page = load({
    routes: {
      "GET /api/status": STATUS,
      "GET /api/journal/summary": { enabled: true, days: 14, totals: {}, chart: [] },
      "GET /api/journal/run": null,
    },
  });
  const summary = await until(() => page.$("runSummary").textContent || null);
  t.diagnostic("with no run, the page says: " + summary);
  assert.equal(page.$("btnEraseRun").hidden, true, "no button to press");
  assert.match(summary, /nothing/i);
  await page.close();
});
