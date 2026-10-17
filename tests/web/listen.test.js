"use strict";
// "Listen here" (web/js/init/03-player-status.js): from the tap until a moment after the
// sound starts, the button says the stream runs behind, then offers to stop.
const test = require("node:test");
const assert = require("node:assert/strict");

const { until, open, STATUS } = require("./harness");

test("listening says the stream runs behind before it offers to stop", async (t) => {
  const stream = { enabled: true, available: true, url: "http://radio/stream.opus" };
  const page = open(t, { routes: { "GET /api/status": Object.assign({}, STATUS, { stream }) } });
  const button = await until(() => !page.$("btnListen").hidden && page.$("btnListen"));
  const label = () => button.querySelector("span").textContent;
  assert.equal(label(), "Listen here");
  const made = [];
  const RealAudio = page.window.Audio;
  page.window.Audio = function (url) {
    const audio = new RealAudio(url);
    made.push(audio);
    return audio;
  };
  button.click();
  await until(() => made.length);
  assert.equal(label(), "Slight delay…", "said at once, while the stream connects");
  made[0].dispatchEvent(new page.window.Event("playing"));
  assert.equal(label(), "Slight delay…", "still said as the sound starts");
  await until(() => label() === "Stop listening", 4000);
  button.click();
  assert.equal(label(), "Listen here");
});
