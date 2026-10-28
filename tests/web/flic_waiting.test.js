"use strict";
// A Flic button waiting for a press is said among the player's notices, to the house only.
const test = require("node:test");
const assert = require("node:assert/strict");

const { load, until, STATUS } = require("./harness");

test("the player says the Flic button waits for a press, and stops saying it once connected", async () => {
  let waiting = true;
  const p = load({ hash: "#home/player", routes: {
    "GET /api/status": () => Object.assign({}, STATUS, { flic_waiting: waiting }),
  } });
  await until(() => /press it once/.test(p.$("npNotices").textContent), 8000);
  waiting = false;
  await until(() => !/press it once/.test(p.$("npNotices").textContent), 12000);
  await p.close();
});

test("a Flic battery going flat is one of the player's notices", async () => {
  const p = load({ hash: "#home/player", routes: {
    "GET /api/status": Object.assign({}, STATUS, { flic_battery_low: 9 }),
  } });
  await until(() => /battery is low \(9 %\)/.test(p.$("npNotices").textContent), 8000);
  await p.close();
});

test("a weak power supply, a full storage and a clock that lost the time are the player's notices", async () => {
  const p = load({ hash: "#home/player", routes: {
    "GET /api/status": Object.assign({}, STATUS, { rtc_lost: true, clock_ready: false,
      health: { power: "now", storage: { problem: "full", music: true, free: 1048576 } } }),
  } });
  await until(() => /power supply is too weak/.test(p.$("npNotices").textContent), 8000);
  const text = p.$("npNotices").textContent;
  assert.match(text, /music storage is almost full/);
  assert.match(text, /lost the time/);
  assert.doesNotMatch(text, /time is not established/, "one clock notice, the precise one");
  await p.close();
});
