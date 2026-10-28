"use strict";
// The Flic switch (web/app.js): on the radio's only Bluetooth controller it asks first,
// since the button then takes Bluetooth whole.
const test = require("node:test");
const assert = require("node:assert/strict");

const { load, until } = require("./harness");

function page(takesOnly) {
  return load({ hash: "#audio/bluetooth", view: "detailed", routes: {
    "GET /api/flic/status": { sdk: true, usable: true, enabled: false, held: false, active: false,
                              bridge: false, buttons: null, pairing: { state: "idle" },
                              takes_only_controller: takesOnly },
    "GET /api/bluetooth/controllers": { controllers: [{ name: "hci0", bus: "uart", address: "B8:27:EB:D7:FC:3A" }],
                                        speaker: null, speaker_setting: "", flic: "B8:27:EB:D7:FC:3A" },
    "POST /api/flic/enable": {},
  } });
}

test("switching the Flic button on over the only controller asks first, and a no leaves it off", async () => {
  const p = page(true);
  await until(() => p.$("bootOverlay").hidden, 8000);
  p.$("btControllersSection").open = true;
  p.$("btControllersSection").dispatchEvent(new p.window.Event("toggle"));
  await until(() => p.$("flicStatusLine").textContent, 8000);
  const toggle = p.$("flicEnabled");
  toggle.checked = true;
  toggle.dispatchEvent(new p.window.Event("change"));
  await until(() => !p.$("modalOverlay").hidden, 8000);
  assert.match(p.$("modalBody").textContent, /takes it whole/);
  p.$("modalCancel").click();
  await until(() => !toggle.checked, 8000);
  assert.equal(p.sent("POST", "/api/flic/enable").length, 0);
  await p.close();
});

test("with another controller free, it switches on without asking", async () => {
  const p = page(false);
  await until(() => p.$("bootOverlay").hidden, 8000);
  p.$("btControllersSection").open = true;
  p.$("btControllersSection").dispatchEvent(new p.window.Event("toggle"));
  await until(() => p.$("flicStatusLine").textContent, 8000);
  p.$("flicEnabled").checked = true;
  p.$("flicEnabled").dispatchEvent(new p.window.Event("change"));
  await until(() => p.sent("POST", "/api/flic/enable").length, 8000);
  assert.equal(p.$("modalOverlay").hidden, true);
  await p.close();
});

test("a Flic with no button paired rests, and still offers to pair one", async () => {
  const p = load({ hash: "#audio/bluetooth", view: "detailed", routes: {
    "GET /api/flic/status": { sdk: true, usable: true, enabled: true, held: false, active: false, idle: true,
                              bridge: false, buttons: [], pairing: { state: "idle" }, takes_only_controller: false },
    "GET /api/bluetooth/controllers": { controllers: [{ name: "hci0", bus: "uart", address: "B8:27:EB:D7:FC:3A" }],
                                        speaker: null, speaker_setting: "", flic: "B8:27:EB:D7:FC:3A" },
  } });
  await until(() => p.$("bootOverlay").hidden, 8000);
  p.$("btControllersSection").open = true;
  p.$("btControllersSection").dispatchEvent(new p.window.Event("toggle"));
  await until(() => /resting/.test(p.$("flicStatusLine").textContent), 8000);
  assert.equal(p.$("btnFlicPair").hidden, false, "pairing wakes it");
  assert.equal(p.$("flicEnabled").checked, true, "still on: only asleep");
  await p.close();
});

test("a button that is not connected says a press brings it back", async () => {
  const p = load({ hash: "#audio/bluetooth", view: "detailed", routes: {
    "GET /api/flic/status": { sdk: true, usable: true, enabled: true, held: false, active: true, bridge: true,
                              buttons: ["90:88:A9:51:CD:2F"], waiting: true, pairing: { state: "idle" } },
    "GET /api/bluetooth/controllers": { controllers: [{ name: "hci0", bus: "uart", address: "B8:27:EB:D7:FC:3A" }],
                                        speaker: null, speaker_setting: "", flic: "B8:27:EB:D7:FC:3A" },
  } });
  await until(() => p.$("bootOverlay").hidden, 8000);
  p.$("btControllersSection").open = true;
  p.$("btControllersSection").dispatchEvent(new p.window.Event("toggle"));
  await until(() => p.$("flicStatusLine").textContent, 8000);
  assert.match(p.$("flicStatusLine").textContent, /press it once to connect it/);
  await p.close();
});

test("a Flic button's battery going flat is a warning", async () => {
  const p = load({ hash: "#audio/bluetooth", view: "detailed", routes: {
    "GET /api/flic/status": { sdk: true, usable: true, enabled: true, held: false, active: true, bridge: true,
                              buttons: ["90:88:A9:51:CD:2F"], battery: { "90:88:A9:51:CD:2F": 12 },
                              pairing: { state: "idle" } },
    "GET /api/bluetooth/controllers": { controllers: [{ name: "hci0", bus: "uart", address: "B8:27:EB:D7:FC:3A" }],
                                        speaker: null, speaker_setting: "", flic: "B8:27:EB:D7:FC:3A" },
  } });
  await until(() => p.$("bootOverlay").hidden, 8000);
  p.$("btControllersSection").open = true;
  p.$("btControllersSection").dispatchEvent(new p.window.Event("toggle"));
  await until(() => /battery/.test(p.$("flicStatusLine").textContent), 8000);
  assert.match(p.$("flicStatusLine").textContent, /battery low \(12 %\)/);
  assert.equal(p.$("flicStatusLine").dataset.state, "warn");
  await p.close();
});
