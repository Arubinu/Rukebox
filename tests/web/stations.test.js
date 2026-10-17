"use strict";
// Web radios (web/js/init/27-stations.js): added, listened to, offered to a schedule, and
// named so on the player while one plays.
const test = require("node:test");
const assert = require("node:assert/strict");

const { until, open, STATUS } = require("./harness");

const FIP = { id: "fip", name: "FIP", url: "https://icecast.radiofrance.fr/fip-midfi.mp3" };

test("a radio is added, listed and listened to", async (t) => {
  let stations = [];
  const page = open(t, { hash: "#home/lists", routes: {
    "GET /api/lists": { lists: [], active: null, settings: [] },
    "GET /api/stations": () => ({ stations }),
    "POST /api/stations": (request) => {
      stations = [Object.assign({ id: "fip" }, request.body)];
      return stations[0];
    },
    "POST /api/stations/fip/play": { ok: true },
  } });
  await until(() => page.$("bootOverlay").hidden && page.sent("GET", "/api/stations").length);
  page.$("stationName").value = "FIP";
  page.$("stationUrl").value = FIP.url;
  page.$("stationAdd").click();
  const sent = await until(() => page.sent("POST", "/api/stations")[0]);
  assert.deepEqual(sent.body, { name: "FIP", url: FIP.url });
  const play = await until(() => page.$("stationsList").querySelector(".station-play"));
  assert.equal(page.$("stationsEmpty").hidden, true);
  play.click();
  await until(() => page.sent("POST", "/api/stations/fip/play").length);
  assert.deepEqual(page.errors, []);
});

test("a schedule may open with one, and the player says a web radio plays", async (t) => {
  const page = open(t, { hash: "#settings/settings", routes: {
    "GET /api/stations": { stations: [FIP] },
    "GET /api/schedules": { schedules: [], settings: [] },
    "GET /api/status": Object.assign({}, STATUS, { mode: "cast", track_title: "Feeling Good",
      cast: { origin: "station", title: "FIP", now: "Feeling Good", url: FIP.url } }),
  } });
  await until(() => page.$("schedStation").options.length === 2);
  page.$("scheduleFormSection").open = true;
  assert.equal(page.$("schedStationRow").hidden, false, "a start may play it");
  assert.equal(page.$("schedStation").options[1].textContent, "FIP");
  await until(() => page.$("npMode").textContent === "Web radio");
});
