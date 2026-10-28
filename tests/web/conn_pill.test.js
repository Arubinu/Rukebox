"use strict";
// The badge at the top says how fresh the page is: up to date, refreshing, how old, or no answer.
const test = require("node:test");
const assert = require("node:assert/strict");

const { load, until, deferred, STATUS } = require("./harness");

function page() {
  const control = { hold: null };
  const p = load({ hash: "#home/player", routes: {
    "GET /api/status": () => (control.hold ? control.hold.promise : STATUS),
  } });
  return { p, control, label: () => p.$("connLabel").textContent };
}

test("back on the page, it says it refreshes until the radio answers again", async () => {
  const { p, control, label } = page();
  await until(() => label() === "Up to date", 8000);
  control.hold = deferred();
  p.document.dispatchEvent(new p.window.Event("visibilitychange"));
  assert.match(label(), /^Refreshing/, "said before any answer");
  assert.ok(p.$("connDot").classList.contains("busy"));
  const answer = control.hold;
  control.hold = null;
  answer.resolve(STATUS);
  await until(() => label() === "Up to date", 8000);
  await p.close();
});

test("an answer too old says how old, in orange", async () => {
  const { p, control, label } = page();
  await until(() => label() === "Up to date", 8000);
  // The page's first reads are over: only the status, held from now on, would answer.
  await new Promise((resolve) => setTimeout(resolve, 3000));
  control.hold = deferred();
  const realNow = p.window.Date.now;
  p.window.Date.now = () => realNow() + 20000;
  await until(() => /^\d+ s ago$/.test(label()), 8000);
  assert.ok(p.$("connDot").classList.contains("stale"));
  await p.close();
});

test("a tap on the badge reads the page again", async () => {
  const { p, label } = page();
  await until(() => label() === "Up to date", 8000);
  const before = p.sent("GET", "/api/status").length;
  p.$("connPill").click();
  await until(() => p.sent("GET", "/api/status").length > before, 8000);
  await until(() => label() === "Up to date", 8000);
  await p.close();
});
