"use strict";
// The daily curve (web/app.js): the strip draws the points the daemon reads,
// a dot is dragged, added and removed, and the line under it says what happens.
// The level the strip shows at a time follows src/volume_curve.py, whose own
// tests pin the same numbers (tests/test_volume_curve.py).
const test = require("node:test");
const assert = require("node:assert/strict");

const { load, until, STATUS } = require("./harness");

const SETTINGS = { "playback.base_volume": 70, "playback.volume_shape": "curve",
                   "playback.volume_curve_manual": "scaled" };

// 06:00 at 40%, 22:00 at 100%: the day ramps both ways, as the maths above says.
const POINTS = [{ at: 360, level: 40 }, { at: 1320, level: 100 }];

function page(options) {
  return load(Object.assign({ hash: "#settings/volume", routes: {
    "GET /api/settings": SETTINGS,
    "GET /api/volume_curve": { points: POINTS, summary: [], now: { minute: 600, level: 55 } },
    "POST /api/volume_curve": { ok: true, data: {} },
  } }, options || {}));
}

function svgBox(page) {
  // jsdom has no layout: the strip is given a size the maths can use.
  page.$("volumeCurveSvg").getBoundingClientRect = () => ({ left: 0, top: 0, width: 1440, height: 110 });
}

test("the strip draws the curve the daemon reads", async (t) => {
  const view = page();
  await until(() => view.$("volumeShape").value === "curve");
  const dots = await until(() => {
    const found = view.$("volumeCurveSvg").querySelectorAll(".curve-dot");
    return found.length === 2 && found;
  });
  assert.deepEqual([...dots].map((dot) => Number(dot.getAttribute("cx"))), [360, 1320]);
  assert.equal(Number(dots[0].getAttribute("cy")), 64, "40% sits a tenth above the bottom");
  assert.equal(Number(dots[1].getAttribute("cy")), 10, "100% at the top");
  assert.ok(view.$("volumeCurveSvg").querySelectorAll(".curve-line").length >= 2,
            "the lines from one dot to the next, and across midnight");
  assert.deepEqual(view.errors, []);
  await view.close();
});

test("a dot is dragged, and the strip shows what it means", async (t) => {
  const view = page();
  await until(() => view.$("volumeCurveSvg").querySelectorAll(".curve-dot").length === 2);
  svgBox(view);
  const dot = view.$("volumeCurveSvg").querySelector(".curve-dot");
  dot.dispatchEvent(new view.window.MouseEvent("mousedown", { bubbles: true, clientX: 360, clientY: 100 }));
  view.document.dispatchEvent(new view.window.MouseEvent("mousemove", { clientX: 720, clientY: 55 }));
  assert.match(view.$("volumeCurveReadout").textContent, /12:00 → 50% of the volume · 35% played/);
  view.document.dispatchEvent(new view.window.MouseEvent("mouseup", { clientX: 720, clientY: 55 }));
  const sent = (await until(() => view.sent("POST", "/api/volume_curve").length
    && view.sent("POST", "/api/volume_curve")))[0];
  const moved = sent.body.points.find((one) => one.at === 720);
  assert.ok(moved, "the dot landed at 12:00: " + JSON.stringify(sent.body.points));
  assert.equal(moved.level, 50, "and at half height");
  await view.close();
});

test("a double-click adds a dot, a right-click takes one away", async (t) => {
  const view = page();
  await until(() => view.$("volumeCurveSvg").querySelectorAll(".curve-dot").length === 2);
  svgBox(view);
  const svg = view.$("volumeCurveSvg");
  svg.dispatchEvent(new view.window.MouseEvent("dblclick", { bubbles: true, clientX: 600, clientY: 60 }));
  const added = (await until(() => view.sent("POST", "/api/volume_curve").length
    && view.sent("POST", "/api/volume_curve")))[0];
  assert.deepEqual(added.body.points.map((one) => one.at), [360, 600, 1320]);
  await until(() => svg.querySelectorAll(".curve-dot").length === 3);
  const dot = svg.querySelectorAll(".curve-dot")[1];
  dot.dispatchEvent(new view.window.MouseEvent("contextmenu", { bubbles: true, cancelable: true }));
  const removed = view.sent("POST", "/api/volume_curve")[1];
  assert.deepEqual(removed.body.points.map((one) => one.at), [360, 1320]);
  await view.close();
});

test("the arrow keys move the dot the keyboard is on", async (t) => {
  const view = page();
  await until(() => view.$("volumeCurveSvg").querySelectorAll(".curve-dot").length === 2);
  const dot = view.$("volumeCurveSvg").querySelector(".curve-dot");
  dot.dispatchEvent(new view.window.KeyboardEvent("keydown", { key: "ArrowUp", bubbles: true, cancelable: true }));
  const sent = (await until(() => view.sent("POST", "/api/volume_curve").length
    && view.sent("POST", "/api/volume_curve")))[0];
  assert.equal(sent.body.points[0].level, 45, "up by five");
  await view.close();
});

test("the card chooses how the curve answers a hand on the volume", async (t) => {
  const view = page();
  await until(() => view.$("volumeCurveSvg").querySelectorAll(".curve-dot").length === 2);
  assert.equal(view.$("volumeCurveManualRow").hidden, false, "the choice sits with the strip");
  const options = [...view.$("volumeCurveManual").querySelectorAll("option")]
    .map((one) => one.value);
  assert.deepEqual(options, ["scaled", "force", "shifted", "paused"]);
  assert.equal(view.$("volumeCurveManual").value, "scaled",
               "the curve multiplies what was chosen, unless asked otherwise");
  await view.close();
});

test("the player says when the curve decides the volume", async (t) => {
  const view = page({ routes: {
    "GET /api/settings": SETTINGS,
    "GET /api/volume_curve": { points: POINTS, summary: [], now: { minute: 600, level: 55 } },
    "GET /api/status": Object.assign({}, STATUS, { volume: 40, volume_forced: true }),
  }, hash: "#home/player" });
  await until(() => !view.$("npNotices").hidden);
  assert.match(view.$("npNotices").textContent, /curve/i, "the notice names the curve");
  await view.close();
});

test("the readout says the level at the hour, and what will be played", async (t) => {
  const view = page();
  await until(() => view.$("volumeCurveSvg").querySelectorAll(".curve-dot").length === 2);
  assert.equal(view.$("volumeCurveReadout").textContent,
               "10:00 → 55% of the volume · 39% played",
               "the radio's own 10:00, and 70% of a 55% stretch played");
  await view.close();
});

test("the lines under the strip are the daemon's own words", async (t) => {
  const summary = [{ at: 360, from: 100, to: 40, seconds: 28800, kind: "fall" },
                   { at: 1320, from: 40, to: 100, seconds: 57600, kind: "rise" }];
  const view = page({ routes: {
    "GET /api/settings": SETTINGS,
    "GET /api/volume_curve": { points: POINTS, summary, now: { minute: 600, level: 55 } },
  } });
  await until(() => view.$("volumeCurveSvg").querySelectorAll(".curve-dot").length === 2);
  const lines = await until(() => {
    const found = view.$("volumeCurveSummary").children;
    return found.length === 2 && found;
  });
  assert.match(lines[0].textContent, /06:00: 100% → 40%, falling over 480 min/);
  assert.match(lines[1].textContent, /22:00: 40% → 100%, rising over 960 min/);
  await view.close();
});

test("a cut is drawn as one, with no fade", async (t) => {
  const view = page({ routes: {
    "GET /api/settings": SETTINGS,
    "GET /api/volume_curve": { points: [{ at: 360, level: 40 }, { at: 360, level: 100 },
                                        { at: 1320, level: 100 }],
                               summary: [{ at: 360, from: 40, to: 100, seconds: 0, kind: "cut" }],
                               now: { minute: 600, level: 100 } },
  } });
  await until(() => view.$("volumeCurveSvg").querySelectorAll(".curve-dot").length === 3);
  assert.equal(view.$("volumeCurveSvg").querySelectorAll(".curve-cut").length, 1);
  await until(() => view.$("volumeCurveSummary").children.length === 2);
  assert.match(view.$("volumeCurveSummary").textContent, /06:00: 40% → 100% at once/,
               "the cut, and the fall that leads to it");
  await view.close();
});
