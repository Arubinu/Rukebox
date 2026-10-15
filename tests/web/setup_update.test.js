"use strict";
// "To finish" says when the radio was updated under a page, and its button
// reloads with fresh copies of every file (web/app.js, src/web_server.py).
const test = require("node:test");
const assert = require("node:assert/strict");

const { load, until, STATUS } = require("./harness");

/* The radio serves one answer at a time, so a test can move the version under the page. */
function withRadio() {
  const answer = { items: ["music"], hidden: [], web_version: "aaaa1111" };
  const page = load({
    routes: {
      "GET /api/status": STATUS,
      "GET /api/setup/pending": () => Object.assign({}, answer),
    },
  });
  page.answer = answer;
  return page;
}

/* What the page itself does when it comes back to the foreground: every refresh
   registered without a page runs again, the one that reads this card included. */
async function wake(page) {
  const before = page.sent("GET", "/api/setup/pending").length;
  page.document.dispatchEvent(new page.window.Event("visibilitychange"));
  await until(() => page.sent("GET", "/api/setup/pending").length > before);
}

async function booted(page) {
  await until(() => page.document.querySelectorAll("#setupList li").length === 1);
  assert.equal(page.$("setupCard").hidden, false, "one thing to finish, one card");
}

test("the card says the radio was updated under the page", async (t) => {
  const page = withRadio();
  await booted(page);

  page.answer.items = [];
  page.answer.web_version = "bbbb2222";
  await wake(page);

  const row = await until(() => page.$("setupWebUpdate"));
  t.diagnostic("the line says: " + row.textContent.trim());
  assert.equal(page.document.querySelectorAll("#setupList li").length, 1,
               "the update is what is left to finish");
  assert.equal(page.$("setupCard").hidden, false, "and the card comes back for it");
  assert.match(row.textContent, /updated/i);
  await page.close();
});

test("a page in step with the radio says nothing", async () => {
  const page = withRadio();
  await booted(page);

  page.answer.items = [];
  await wake(page);

  await until(() => page.$("setupCard").hidden);
  assert.equal(page.$("setupWebUpdate"), null, "nothing to reload");
  await page.close();
});

test("its button asks for fresh copies of every file, then reloads", async () => {
  const page = withRadio();
  await booted(page);

  page.answer.web_version = "bbbb2222";
  await wake(page);
  const row = await until(() => page.$("setupWebUpdate"));

  row.querySelector("button").click();
  const files = ["/", "/app.js", "/style.css", "/i18n.js", "/qr.js"];
  await until(() => files.every((name) => page.sent("GET", name).some((r) => r.cache === "reload")));
  await until(() => page.navigations.length);
  assert.match(page.navigations[0], /navigation/, "and the page leaves");
  await page.close();
});
