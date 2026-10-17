"use strict";
// Lists that belong to someone (web/js/init/14-lists.js) and the permission to put one on
// instead of the library (web/js/init/19-security.js).
const test = require("node:test");
const assert = require("node:assert/strict");

const { until, open } = require("./harness");

const TRACKS = { items: [{ key: "k1", path: "/m/a.mp3", title: "Alpha", artist: "A" }], missing: 0 };

function lists(mayPlay) {
  return {
    active: null, may_play: mayPlay,
    lists: [
      { id: "mine", name: "Mine", kind: "manual", count: 1, editable: true, owner_name: "" },
      { id: "radio", name: "Radio", kind: "manual", count: 1, editable: false, owner_name: "" },
    ],
  };
}

function labels(page) {
  return [...page.$("listsDetailActions").children].map((b) => b.dataset.icon);
}

test("a member changes only their lists, and plays one only when allowed", async (t) => {
  let state = lists(false);
  const page = open(t, { hash: "#home/lists", routes: {
    "GET /api/lists": () => state,
    "GET /api/library/facets": { genres: [] },
    "GET /api/lists/mine/tracks": TRACKS,
    "GET /api/lists/radio/tracks": TRACKS,
  } });
  await until(() => page.$("listsList").children.length === 2);
  assert.equal(page.$("listsPlayAll").hidden, true, "no going back to the library either");

  page.$("listsList").children[1].querySelector(".ann-head").click();
  await until(() => page.$("listsDetailBody").querySelector("li"));
  assert.deepEqual(labels(page), ["download"], "someone else's list: only its export");
  const row = page.$("listsDetailBody").querySelector("li");
  assert.ok(row.querySelector(".library-next"), "its songs can still be queued");
  assert.equal(row.querySelector('[data-icon="x"]'), null, "but not taken out");

  page.$("listsBack").click();
  page.$("listsList").children[0].querySelector(".ann-head").click();
  await until(() => labels(page).length === 3);
  assert.deepEqual(labels(page), ["pencil", "download", "trash"]);

  state = lists(true);
  // Arriving on the page again refreshes it, as a tap on the menu would.
  page.document.dispatchEvent(new page.window.CustomEvent("page-shown", { detail: { tab: "home", page: "lists" } }));
  await until(() => labels(page).length === 4);
  assert.deepEqual(labels(page), ["play", "pencil", "download", "trash"], "allowed: it can be put on");
  assert.equal(page.$("listsPlayAll").hidden, false);
});

test("the owner lets a member play lists from the People page", async (t) => {
  const page = open(t, { hash: "#system/people", routes: {
    "GET /api/auth/accounts": { roles: ["member", "operator", "owner"], accounts: [
      { id: "boss", name: "Boss", role: "owner", password: true },
      { id: "marie", name: "Marie", role: "member", password: true, play_lists: false },
    ] },
    "POST /api/auth/accounts": { id: "marie", name: "Marie", role: "member" },
  } });
  await until(() => page.$("peopleList").children.length === 2);
  const [boss, marie] = page.$("peopleList").children;
  assert.equal(boss.querySelector(".switch-input"), null, "the radio's keepers need no permission");
  const toggle = marie.querySelector(".switch-input");
  assert.equal(toggle.checked, false);
  toggle.checked = true;
  toggle.dispatchEvent(new page.window.Event("change"));
  await until(() => page.sent("POST", "/api/auth/accounts").length);
  assert.deepEqual(page.sent("POST", "/api/auth/accounts")[0].body, { id: "marie", play_lists: true });
});
