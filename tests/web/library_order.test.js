"use strict";
// The library's newest arrivals: a list of their own, with nothing searched.
const test = require("node:test");
const assert = require("node:assert/strict");

const { load, until } = require("./harness");

test("newest first lists the library's latest arrivals without a search", async () => {
  const p = load({ hash: "#home/library", routes: {
    "GET /api/library": (request) => ({
      items: request.query.get("order") === "new" ? [{ key: "k1", title: "Fresh", name: "fresh" }] : [],
      total: request.query.get("order") === "new" ? 1 : 0, status: { total: 10, read: 10 } }),
    "GET /api/library/facets": { artists: [], albums: [], genres: [] },
  } });
  await until(() => p.sent("GET", "/api/library").length, 8000);
  const order = p.$("libraryOrder");
  order.value = "new";
  order.dispatchEvent(new p.window.Event("change"));
  await until(() => p.$("libraryList").children.length === 1, 8000);
  assert.match(p.$("libraryList").textContent, /Fresh/);
  await p.close();
});
