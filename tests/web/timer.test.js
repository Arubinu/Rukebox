"use strict";
// The timer dialog (web/app.js): its two heads are buttons, so each applies the
// shortest delay of its own group, and they still look like plain headings.
const test = require("node:test");
const assert = require("node:assert/strict");

const { load, until, STATUS } = require("./harness");

function withTimer() {
  return load({
    routes: {
      "GET /api/status": Object.assign({}, STATUS, {
        pause_durations: [5, 15, 30, 60],
        sleep_durations: [30, 60, 90, 120],
      }),
      "POST /api/action/timed_pause": () => ({ ok: true }),
      "POST /api/action/sleep_timer": () => ({ ok: true }),
    },
  });
}

async function openTimer(page) {
  // The timer reads what the status brought, so the page has to be up first.
  await until(() => page.document.body.dataset.caps !== undefined);
  page.$("btnTimer").click();
  await until(() => !page.$("modalOverlay").hidden);
  return [...page.document.querySelectorAll(".timer-group-head")];
}

test("the timer's heads are buttons, one per group", async (t) => {
  const page = withTimer();
  const heads = await openTimer(page);
  assert.equal(heads.length, 2, "the pause and the sleep");
  assert.deepEqual(heads.map((h) => h.tagName), ["BUTTON", "BUTTON"],
                   "each head can be pressed on its own");
  assert.deepEqual(heads.map((h) => h.querySelector(".timer-symbol").dataset.icon),
                   ["pause", "moon"], "the same round icons as before");
  t.diagnostic("the heads say: " + heads.map((h) => h.textContent.trim()).join(", "));
  await page.close();
});

test("a head asks for the shortest delay under it", async (t) => {
  const page = withTimer();
  const heads = await openTimer(page);
  heads[0].click();
  const pause = await until(() => page.sent("POST", "/api/action/timed_pause")[0] || null);
  assert.deepEqual(pause.body, { minutes: 5 }, "the shortest pause offered");
  await until(() => page.$("modalOverlay").hidden);
  await page.close();
});

test("the sleep head asks for the shortest sleep", async (t) => {
  const page = withTimer();
  const heads = await openTimer(page);
  heads[1].click();
  const sleep = await until(() => page.sent("POST", "/api/action/sleep_timer")[0] || null);
  assert.deepEqual(sleep.body, { on: true, minutes: 30 }, "the shortest sleep offered");
  await page.close();
});
