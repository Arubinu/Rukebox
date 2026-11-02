"use strict";
// The network buttons (web/js/init/30-network-buttons.js): each shows up at its first press and waits to be approved.
const test = require("node:test");
const assert = require("node:assert/strict");

const { until, open } = require("./harness");

test("a button waiting is approved from its row, renamed in a dialog, and removed once confirmed", async (t) => {
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
  assert.equal(list.querySelector(".device-name").textContent, "GL-SFT1200");
  assert.match(list.textContent, /10\.42\.0\.60.*Waiting for approval.*Last press/);
  assert.equal(list.querySelector("input"), null, "the name is no field of the row");

  [...list.querySelectorAll("button")].find((b) => b.textContent === "Approve").click();
  await until(() => /Approved/.test(list.textContent));
  assert.deepEqual(sent[0], { approved: true });
  assert.ok(![...list.querySelectorAll("button")].some((b) => b.textContent === "Approve"), "approved once");

  const icons = [...list.querySelectorAll("button")].map((b) => b.dataset.icon);
  assert.deepEqual(icons, ["pencil", "x"], "rename sits to the left of remove");

  list.querySelector('button[data-icon="pencil"]').click();
  await until(() => !page.$("modalOverlay").hidden && page.$("modalBody").querySelector("input"));
  const name = page.$("modalBody").querySelector("input");
  assert.equal(name.value, "GL-SFT1200");
  name.value = "Rayon frais";
  page.$("modalOk").click();
  await until(() => list.querySelector(".device-name").textContent === "Rayon frais");
  assert.deepEqual(sent[1], { name: "Rayon frais" });

  list.querySelector('button[data-icon="x"]').click();
  await until(() => !page.$("modalOverlay").hidden);
  assert.deepEqual(removed, [], "nothing goes before it is confirmed");
  page.$("modalOk").click();
  await until(() => list.children.length === 0);
  assert.deepEqual(removed, ["ab12"]);
  assert.equal(page.$("networkButtonEmpty").hidden, false);
});
