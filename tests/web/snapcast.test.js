"use strict";
// The multiroom output in Audio > Output (web/app.js): offered where snapserver is installed,
// the second of delay said above the choice, and the devices listening managed from a dialog.
const test = require("node:test");
const assert = require("node:assert/strict");

const { load, until, STATUS } = require("./harness");

const CLIENTS = { available: true, running: true, web: true, port: 1780, clients: [
  { id: "a", name: "Salon", host: "salon-pi", ip: "192.168.1.8", connected: true, volume: 75, muted: false },
  { id: "b", name: "Kitchen", host: "kitchen", ip: "", connected: false, volume: 40, muted: true },
] };

function page(caps, output) {
  return load({ hash: "#audio/output", view: "detailed", routes: {
    "GET /api/status": Object.assign({}, STATUS, { capabilities: Object.assign({}, STATUS.capabilities, caps) }),
    "GET /api/settings": { AUDIO_OUTPUT: output, SNAPCAST_CODEC: "opus" },
    "GET /api/audio/outputs": { output, outputs: [] },
    "GET /api/snapcast/clients": CLIENTS,
    "POST /api/snapcast/client": {},
    "POST /api/snapcast/clients": {},
  } });
}

test("chosen, the multiroom output says its delay and opens the devices listening", async () => {
  const p = page({ snapcast: true }, "snapcast");
  await until(() => p.$("audioOutputSelect").value === "snapcast");
  await until(() => /one second/.test(p.$("audioOutputDetected").textContent));
  assert.match(p.$("audioOutputDetected").textContent, /Listening now: 1\./);
  await until(() => !p.$("snapcastRow").hidden);
  p.$("snapcastManage").click();
  const list = await until(() => p.document.querySelector("#modalBody .snapcast-list"));
  assert.equal(list.children.length, 3, "all the devices first, then each one");
  assert.ok(list.children[0].classList.contains("snapcast-all"));
  assert.equal(p.document.querySelector("#modalOverlay .modal-actions").hidden, true, "the cross closes it, no OK");
  assert.equal(list.children[1].querySelectorAll(".btn-danger-outline").length, 0);
  assert.equal(list.children[2].querySelectorAll(".btn-danger-outline").length, 1, "only a device gone can be forgotten");
  list.children[0].querySelector(".snapcast-head button").click();
  await until(() => p.sent("POST", "/api/snapcast/clients").length);
  assert.deepEqual(p.sent("POST", "/api/snapcast/clients")[0].body, { muted: true }, "mutes them all");
  await until(() => list.children[1].querySelector(".snapcast-head button").getAttribute("aria-pressed") === "true");
  const volume = list.children[1].querySelector('input[type="range"]');
  volume.value = "30";
  volume.dispatchEvent(new p.window.Event("change"));
  await until(() => p.sent("POST", "/api/snapcast/client").length);
  assert.deepEqual(p.sent("POST", "/api/snapcast/client")[0].body, { id: "a", volume: 30 });
  await p.close();
});

test("without snapserver the multiroom output is not offered", async () => {
  const p = page({ snapcast: false }, "bluetooth");
  await until(() => p.document.body.dataset.caps !== undefined && p.sent("GET", "/api/audio/outputs").length);
  await until(() => !p.$("audioOutputSelect").querySelector('option[value="snapcast"]'));
  assert.equal(p.$("snapcastRow").hidden, true);
  await p.close();
});
