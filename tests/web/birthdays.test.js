"use strict";
// The birthdays card (web/js/init/29-birthdays.js): a name and a day, and when the radio wishes them.
const test = require("node:test");
const assert = require("node:assert/strict");

const { until, open } = require("./harness");

test("a birthday is added with its day, and the moment is chosen from the card", async (t) => {
  const sent = [];
  let people = [{ id: "marie", name: "Marie", date: "03-14" }];
  const page = open(t, { hash: "#home/birthdays", storage: { rukebox_lang: "en" }, routes: {
    "GET /api/birthdays": () => ({ people, wish: "start", today: [] }),
    "POST /api/birthdays": (request) => {
      sent.push(request.body);
      if (request.body.name) people = people.concat([{ id: "paul", name: request.body.name, date: request.body.date }]);
      return request.body.name ? { id: "paul", name: request.body.name, date: request.body.date } : { wish: request.body.wish };
    },
  } });
  await until(() => page.$("birthdayList").children.length === 1, 8000);
  assert.match(page.$("birthdayList").textContent, /Marie.*March 14/);
  page.$("birthdayName").value = "Paul";
  page.$("birthdayDate").querySelector(".season-day").value = "2";
  page.$("birthdayDate").querySelector(".season-month").value = "7";
  page.$("birthdayForm").dispatchEvent(new page.window.Event("submit", { cancelable: true }));
  await until(() => page.$("birthdayList").children.length === 2);
  assert.deepEqual(sent[0], { name: "Paul", date: "07-02" });
  page.$("birthdayWish").value = "button";
  page.$("birthdayWish").dispatchEvent(new page.window.Event("change"));
  await until(() => sent.length === 2);
  assert.deepEqual(sent[1], { wish: "button" });
  const actions = [...page.$("singleClickAction").options].map((o) => o.value);
  assert.ok(actions.includes("birthday"), "a button can wish them");
});
