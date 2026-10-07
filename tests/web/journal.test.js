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

