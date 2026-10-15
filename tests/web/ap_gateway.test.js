"use strict";
// The access point card (web/app.js): its Save button also saves "Internet through the radio",
// and leaves the access point itself alone when only that switch changed.
const test = require("node:test");
const assert = require("node:assert/strict");

const { load, until } = require("./harness");

const AP = { configured: true, ssid: "Rukebox", password_set: false };

function page() {
  return load({ hash: "#network/accesspoint", routes: {
    "GET /api/settings": { "network.ap_gateway": false },
    "GET /api/wifi/ap": AP,
    "POST /api/settings": {},
    "POST /api/wifi/ap": { password_set: false },
  } });
}

test("the switch alone is saved without restarting the access point", async () => {
  const p = page();
  await until(() => p.$("apSsid").value === "Rukebox");
  p.$("apGatewayToggle").checked = true;
  p.$("apGatewayToggle").dispatchEvent(new p.window.Event("change"));
  p.$("apForm").dispatchEvent(new p.window.Event("submit", { cancelable: true }));
  await until(() => p.sent("POST", "/api/settings").length);
  assert.deepEqual(p.sent("POST", "/api/settings")[0].body, { "network.ap_gateway": "true" }, "sent the way every settings form sends a switch");
  assert.equal(p.sent("POST", "/api/wifi/ap").length, 0);
  await p.close();
});

test("a new name saves the switch first, then the access point", async () => {
  const p = page();
  await until(() => p.$("apSsid").value === "Rukebox");
  p.$("apGatewayToggle").checked = true;
  p.$("apSsid").value = "Radio";
  p.$("apForm").dispatchEvent(new p.window.Event("submit", { cancelable: true }));
  await until(() => p.sent("POST", "/api/wifi/ap").length);
  assert.equal(p.sent("POST", "/api/settings").length, 1);
  assert.equal(p.sent("POST", "/api/wifi/ap")[0].body.ssid, "Radio");
  await p.close();
});
