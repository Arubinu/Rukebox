"use strict";
// Several lists played at once and lists left out (web/js/init/14-lists.js), in a schedule
// (07-schedules.js) and on a card (11-cards.js).
const test = require("node:test");
const assert = require("node:assert/strict");

const { until, open } = require("./harness");

const TRACKS = { items: [{ key: "k1", path: "/m/a.mp3", title: "Alpha", artist: "A" }], missing: 0 };
const LISTS = [
  { id: "calm", name: "Calm", kind: "manual", count: 2, editable: true, owner_name: "" },
  { id: "party", name: "Party", kind: "manual", count: 3, editable: true, owner_name: "" },
  { id: "xmas", name: "Xmas", kind: "genre", genres: ["Christmas"], count: 4, editable: true, owner_name: "" },
];

function listsWith(selection) {
  return { active: selection.play[0] || null, selection, may_play: true, may_exclude: true, lists: LISTS };
}

function buttons(page) {
  return [...page.$("listsDetailActions").children].map((b) => b.dataset.icon);
}

test("lists are added to what plays, taken out, and left out", async (t) => {
  let selection = { play: ["calm"], exclude: [] };
  const page = open(t, { hash: "#home/lists", routes: {
    "GET /api/lists": () => listsWith(selection),
    "GET /api/library/facets": { genres: [] },
    "GET /api/lists/calm/tracks": TRACKS, "GET /api/lists/party/tracks": TRACKS, "GET /api/lists/xmas/tracks": TRACKS,
    "POST /api/lists/active": (request) => {
      selection = { play: request.body.play || selection.play, exclude: request.body.exclude || selection.exclude };
      return { active: selection.play[0] || null, tracks: 5, selection };
    },
  } });
  await until(() => page.$("listsList").children.length === 3);
  assert.ok(page.$("listsList").children[0].classList.contains("is-playing"));
  assert.match(page.$("listsNow").textContent, /Calm/);

  page.$("listsList").children[1].querySelector(".ann-head").click();
  await until(() => buttons(page).length === 3);
  assert.deepEqual(buttons(page), ["play", "plus", "ban"], "alone, with the others, or left out");
  page.$("listsDetailActions").querySelector('[data-icon="plus"]').click();
  await until(() => page.sent("POST", "/api/lists/active").length === 1);
  assert.deepEqual(page.sent("POST", "/api/lists/active")[0].body, { play: ["calm", "party"] });
  await until(() => buttons(page).includes("circle-minus"));

  page.$("listsBack").click();
  page.$("listsList").children[2].querySelector(".ann-head").click();
  await until(() => buttons(page).includes("ban"));
  page.$("listsDetailActions").querySelector('[data-icon="ban"]').click();
  await until(() => page.sent("POST", "/api/lists/active").length === 2);
  assert.deepEqual(page.sent("POST", "/api/lists/active")[1].body, { exclude: ["xmas"] });
  await until(() => buttons(page).includes("check"));
  assert.match(page.$("listsDetailMeta").textContent, /left out/);

  page.$("listsBack").click();
  await until(() => page.$("listsList").children[2].classList.contains("is-excluded"));
  assert.match(page.$("listsNow").textContent, /Calm \+ Party/);
  assert.match(page.$("listsNow").textContent, /without Xmas/);
});

test("leaving out a list that plays takes it out of what plays", async (t) => {
  const page = open(t, { hash: "#home/lists", routes: {
    "GET /api/lists": () => listsWith({ play: ["calm", "party"], exclude: [] }),
    "GET /api/library/facets": { genres: [] },
    "GET /api/lists/party/tracks": TRACKS,
    "POST /api/lists/active": { active: "calm", tracks: 2 },
  } });
  await until(() => page.$("listsList").children.length === 3);
  page.$("listsList").children[1].querySelector(".ann-head").click();
  await until(() => buttons(page).includes("ban"));
  page.$("listsDetailActions").querySelector('[data-icon="ban"]').click();
  await until(() => page.sent("POST", "/api/lists/active").length);
  assert.deepEqual(page.sent("POST", "/api/lists/active")[0].body, { exclude: ["party"], play: ["calm"] });
});

test("a schedule plays the lists ticked and leaves others out", async (t) => {
  const page = open(t, { hash: "#settings/settings", routes: {
    "GET /api/settings": {},
    "GET /api/schedules": { schedules: [
      { id: "soir", name: "Le soir", enabled: true, date: null, days: [], start: "20:00", stop: null,
        stop_action: "pause", lists: ["calm"], exclude: null, settings: {} },
    ], settings: [] },
    "POST /api/schedules/soir": (request) => Object.assign({ id: "soir" }, request.body),
  } });
  (await until(() => page.$("scheduleList").querySelector(".sched-edit"))).click();
  assert.equal(page.$("schedList").value, "pick");
  assert.equal(page.$("schedListPicks").hidden, false);
  assert.deepEqual([...page.$("schedListPicks").querySelectorAll("input:checked")].map((i) => i.value), ["calm"]);
  assert.equal(page.$("schedExclude").value, "keep");
  assert.equal(page.$("schedExcludePicks").hidden, true);

  page.$("schedExclude").value = "none";
  page.$("schedExclude").dispatchEvent(new page.window.Event("change", { bubbles: true }));
  page.$("scheduleForm").dispatchEvent(new page.window.Event("submit", { bubbles: true, cancelable: true }));
  const sent = (await until(() => page.sent("POST", "/api/schedules/soir").length
    && page.sent("POST", "/api/schedules/soir")))[0].body;
  assert.deepEqual([sent.lists, sent.exclude], [["calm"], []]);

  page.$("schedList").value = "pick";
  page.$("schedListPicks").querySelectorAll("input").forEach((input) => { input.checked = false; });
  page.$("scheduleForm").dispatchEvent(new page.window.Event("submit", { bubbles: true, cancelable: true }));
  await until(() => !page.$("toastStack") || page.document.body.textContent.includes("Tick at least one list"));
  assert.equal(page.sent("POST", "/api/schedules/soir").length, 1, "nothing ticked is not sent");
});

test("a card plays every list ticked on it", async (t) => {
  const page = open(t, { hash: "#settings/cards", view: "detailed", routes: {
    "GET /api/cards": [],
    "GET /api/lists": listsWith({ play: [], exclude: [] }),
    "GET /api/library/facets": { genres: [] },
    "GET /api/announcements": [],
    "POST /api/cards": (request) => Object.assign({}, request.body),
  } });
  await until(() => page.$("cardList").querySelectorAll("input").length === 3);
  page.$("cardId").value = "00001111";
  page.$("cardName").value = "Soirée";
  page.$("cardAction").value = "list";
  page.$("cardAction").dispatchEvent(new page.window.Event("change", { bubbles: true }));
  const chips = page.$("cardList").querySelectorAll("input");
  chips[0].checked = true;
  chips[1].checked = true;
  page.$("cardForm").dispatchEvent(new page.window.Event("submit", { bubbles: true, cancelable: true }));
  const sent = (await until(() => page.sent("POST", "/api/cards").length && page.sent("POST", "/api/cards")))[0].body;
  assert.equal(sent.target, "calm,party");
});
