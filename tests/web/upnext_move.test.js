"use strict";
// Up next reordered by hand: a long press picks a song up, a finger that moves at once scrolls.
const test = require("node:test");
const assert = require("node:assert/strict");

const { load, until } = require("./harness");

const QUEUE = { enabled: true, may_move: true, fixed: 0, items: [
  { key: "k1", title: "One", name: "one" },
  { key: "k2", title: "Two", name: "two" },
  { key: "k3", title: "Three", name: "three" },
] };

function page(moveAnswer) {
  return load({ hash: "#home/upnext", routes: {
    "GET /api/queue": QUEUE,
    "POST /api/queue/move": moveAnswer || { position: 0 },
    "POST /api/queue/remove": {},
    "POST /api/queue/undo": {},
  } });
}

function rows(p) {
  const list = [...p.$("upnextList").children];
  // jsdom has no layout: each row is 40px tall, one under the other.
  list.forEach((li, i) => {
    li.getBoundingClientRect = () => ({ top: i * 40, bottom: i * 40 + 40, height: 40, left: 0, right: 300 });
  });
  return list;
}

function pointer(p, li, type, y) {
  li.dispatchEvent(new p.window.MouseEvent(type, { bubbles: true, clientX: 10, clientY: y, button: 0 }));
}

test("held, the last song carried to the top is moved first", async () => {
  const p = page();
  await until(() => p.$("upnextList").children.length === 3, 8000);
  assert.equal(p.$("upnextMoveHint").hidden, false);
  const [, , third] = rows(p);
  pointer(p, third, "pointerdown", 100);
  await until(() => third.classList.contains("is-dragging"), 8000);
  pointer(p, third, "pointermove", 10);
  pointer(p, third, "pointerup", 10);
  await until(() => p.sent("POST", "/api/queue/move").length === 1, 8000);
  assert.deepEqual(p.sent("POST", "/api/queue/move")[0].body, { key: "k3", after: "" });
  await p.close();
});

test("a finger that moves before the hold is a scroll: nothing moves", async () => {
  const p = page();
  await until(() => p.$("upnextList").children.length === 3, 8000);
  const [, , third] = rows(p);
  pointer(p, third, "pointerdown", 100);
  pointer(p, third, "pointermove", 60);
  await new Promise((resolve) => setTimeout(resolve, 600));
  assert.equal(third.classList.contains("is-dragging"), false);
  pointer(p, third, "pointerup", 60);
  await new Promise((resolve) => setTimeout(resolve, 100));
  assert.equal(p.sent("POST", "/api/queue/move").length, 0);
  await p.close();
});

test("a queue that changed meanwhile says so and is read again", async () => {
  const p = page({ __raw: { ok: false, error: "queue_changed" } });
  await until(() => p.$("upnextList").children.length === 3, 8000);
  const before = p.sent("GET", "/api/queue").length;
  const [first] = rows(p);
  pointer(p, first, "pointerdown", 20);
  await until(() => first.classList.contains("is-dragging"), 8000);
  pointer(p, first, "pointermove", 70);
  pointer(p, first, "pointerup", 70);
  await until(() => p.document.querySelector(".toast"), 8000);
  assert.match(p.document.querySelector(".toast").textContent, /changed meanwhile/);
  await until(() => p.sent("GET", "/api/queue").length > before, 8000);
  assert.deepEqual(p.sent("POST", "/api/queue/move")[0].body, { key: "k1", after: "k2" });
  await p.close();
});

test("without the right to put lists on, nothing can be picked up", async () => {
  const p = load({ hash: "#home/upnext", routes: { "GET /api/queue": { ...QUEUE, may_move: false } } });
  await until(() => p.$("upnextList").children.length === 3, 8000);
  assert.equal(p.$("upnextMoveHint").hidden, true);
  assert.equal(p.$("upnextList").querySelectorAll(".is-movable").length, 0);
  assert.equal(p.$("upnextList").querySelectorAll('[data-icon="circle-minus"]').length, 0);
  await p.close();
});

test("a song taken out of Up next is sent by key, and the list read again", async () => {
  const p = page();
  await until(() => p.$("upnextList").children.length === 3, 8000);
  const before = p.sent("GET", "/api/queue").length;
  p.$("upnextList").children[1].querySelector('[data-icon="circle-minus"]').click();
  await until(() => p.sent("POST", "/api/queue/remove").length === 1, 8000);
  assert.deepEqual(p.sent("POST", "/api/queue/remove")[0].body, { key: "k2" });
  await until(() => p.document.querySelector(".toast"), 8000);
  assert.match(p.document.querySelector(".toast").textContent, /Two taken out/);
  p.document.querySelector(".toast .toast-action").click();
  await until(() => p.sent("POST", "/api/queue/undo").length, 8000);
  assert.deepEqual(p.sent("POST", "/api/queue/undo")[0].body, { key: "k2" }, "undone from the toast");
  await until(() => p.sent("GET", "/api/queue").length > before, 8000);
  await p.close();
});

test("a song asked for says by whom, under its title", async () => {
  const p = load({ hash: "#home/upnext", routes: {
    "GET /api/queue": { enabled: true, may_move: false, fixed: 0, items: [
      { key: "k1", title: "One", name: "one", requested: true, asked_by: "Marie-Charlotte de la Montagne" },
      { key: "k2", title: "Two", name: "two", requested: true }] },
  } });
  await until(() => p.$("upnextList").children.length === 2, 8000);
  const [first, second] = [...p.$("upnextList").children].map((li) => li.querySelector(".recent-main .upnext-tag"));
  assert.equal(first.textContent, "Asked for by Marie-Charlotte de la Montagne");
  assert.equal(first.title, first.textContent, "the whole name when it is cut");
  assert.equal(second.textContent, "Asked for");
  await p.close();
});
