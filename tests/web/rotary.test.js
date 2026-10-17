"use strict";
// The rotary knob's subsection (web/index.html, web/js/init/24-gpio.js): its pins and turn
// only show while it is switched on, and they are saved under the names the file gives them.
const test = require("node:test");
const assert = require("node:assert/strict");

const { until, open } = require("./harness");

test("the knob's fields show once it is switched on", async (t) => {
  const page = open(t, { hash: "#settings/buttons", view: "detailed", routes: {
    "GET /api/settings": { "buttons.rotary_enabled": "false", "buttons.rotary_pin_a": "17",
                           "buttons.rotary_turn_action": "volume" },
  } });
  await until(() => page.$("rotaryPinA").value === "17");
  const details = [...page.document.querySelectorAll("#rotarySection .rotary-detail")];
  assert.ok(details.length >= 5);
  assert.ok(details.every((el) => el.hidden), "nothing to set while it is off");
  page.$("rotaryEnabled").checked = true;
  page.$("rotaryEnabled").dispatchEvent(new page.window.Event("change", { bubbles: true }));
  assert.ok(details.every((el) => !el.hidden));
  assert.equal(page.$("rotaryTurnAction").dataset.key, "buttons.rotary_turn_action");
  assert.deepEqual(page.errors, []);
});

test("a knob already switched on shows its fields as the settings arrive", async (t) => {
  const page = open(t, { hash: "#settings/buttons", view: "detailed", routes: {
    "GET /api/settings": { "buttons.rotary_enabled": "true", "buttons.rotary_pin_a": "5" },
  } });
  await until(() => page.$("rotaryPinA").value === "5");
  assert.equal(page.$("rotaryPinA").closest(".rotary-detail").hidden, false);
});
