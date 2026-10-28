"use strict";
// The signature before each system sound: a switch of the card, never one of its entries.
const test = require("node:test");
const assert = require("node:assert/strict");

const { load, until } = require("./harness");

const SOUNDS = [
  { key: "SIGNATURE_SOUND", custom: false, off: false, name: null, exists: true },
  { key: "CLOCK_OK_SOUND", custom: false, off: false, name: null, exists: true },
];

test("the signature is a switch above the sounds, turned off and back on", async () => {
  const p = load({ hash: "#settings/sounds", view: "detailed", routes: {
    "GET /api/system_sounds": SOUNDS,
    "GET /api/announcement_volumes": {},
    "POST /api/system_sounds/SIGNATURE_SOUND/off": {},
    "DELETE /api/system_sounds/SIGNATURE_SOUND": {},
  } });
  await until(() => !p.$("systemSignatureRow").hidden, 8000);
  assert.equal(p.$("systemSignatureOn").checked, true);
  const buttons = [...p.$("systemSignatureActions").querySelectorAll("button")].map((b) => b.dataset.icon);
  assert.deepEqual(buttons, ["play", "upload"], "listen and replace, on their own line");
  assert.match(p.$("systemSignatureState").textContent, /glass/, "what it sounds like");
  assert.deepEqual([...p.$("systemSoundsList").children].map((li) => li.dataset.key), ["CLOCK_OK_SOUND"],
                   "not one of the entries");
  const off = p.$("systemSignatureOn");
  off.checked = false;
  off.dispatchEvent(new p.window.Event("change"));
  await until(() => p.sent("POST", "/api/system_sounds/SIGNATURE_SOUND/off").length, 8000);
  off.checked = true;
  off.dispatchEvent(new p.window.Event("change"));
  await until(() => p.sent("DELETE", "/api/system_sounds/SIGNATURE_SOUND").length, 8000);
  await p.close();
});

test("once replaced, the replace button becomes the way back to the original", async () => {
  const p = load({ hash: "#settings/sounds", view: "detailed", routes: {
    "GET /api/system_sounds": [{ key: "SIGNATURE_SOUND", custom: true, off: false, name: "gong", exists: true }],
    "GET /api/announcement_volumes": {},
    "DELETE /api/system_sounds/SIGNATURE_SOUND": {},
  } });
  await until(() => !p.$("systemSignatureRow").hidden, 8000);
  const buttons = [...p.$("systemSignatureActions").querySelectorAll("button")];
  assert.deepEqual(buttons.map((b) => b.dataset.icon), ["play", "restart"]);
  assert.match(p.$("systemSignatureState").textContent, /gong/);
  buttons[1].click();
  await until(() => p.sent("DELETE", "/api/system_sounds/SIGNATURE_SOUND").length, 8000);
  await p.close();
});
