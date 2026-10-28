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
