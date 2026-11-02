"use strict";
// The network buttons (web/js/init/30-network-buttons.js): each shows up at its first press and waits to be approved.
const test = require("node:test");
const assert = require("node:assert/strict");

const { until, open } = require("./harness");

test("a button waiting is approved and named from its row, and removed", async (t) => {
  const sent = [];
  const removed = [];
  let buttons = [{ id: "ab12", name: "GL-SFT1200", ip: "10.42.0.60", approved: false, seen: 1700000000 }];
  const page = open(t, { hash: "#settings/buttons", storage: { rukebox_lang: "en", rukebox_view: "detailed" }, routes: {
    "GET /api/network_buttons": () => ({ buttons }),
    "POST /api/network_buttons/ab12": (request) => {
      sent.push(request.body);
      buttons = [Object.assign({}, buttons[0], request.body)];
      return {};
    },
    "DELETE /api/network_buttons/ab12": () => {
      removed.push("ab12");
      buttons = [];
      return {};
    },
  } });
  const list = page.$("networkButtonList");
  await until(() => list.children.length === 1, 8000);
  assert.equal(page.$("networkButtonEmpty").hidden, true);
  assert.match(list.textContent, /10\.42\.0\.60.*Waiting for approval.*Last press/);

  const approve = [...list.querySelectorAll("button")].find((b) => b.textContent === "Approve");
  approve.click();
  await until(() => /Approved/.test(list.textContent));
  assert.deepEqual(sent[0], { approved: true });
  assert.ok(![...list.querySelectorAll("button")].some((b) => b.textContent === "Approve"), "approved once");

  const name = list.querySelector("input");
  assert.equal(name.value, "GL-SFT1200");
  name.value = "Rayon frais";
  name.dispatchEvent(new page.window.Event("change"));
  await until(() => sent.length === 2);
  assert.deepEqual(sent[1], { name: "Rayon frais" });

  await until(() => list.querySelector("input").value === "Rayon frais");
  list.querySelector('button[data-icon="x"]').click();
  await until(() => list.children.length === 0);
  assert.deepEqual(removed, ["ab12"]);
  assert.equal(page.$("networkButtonEmpty").hidden, false);
});
