"use strict";
// The schedule form's party and recorded message (web/js/init/07-schedules.js): a party has no
// days or times but may open the radio to guests, and a message waits for the schedule's id.
const test = require("node:test");
const assert = require("node:assert/strict");

const { until, open } = require("./harness");

const SCHEDULES = { schedules: [
  { id: "soir", name: "Le soir", enabled: true, date: null, days: [], start: "20:00", stop: null,
    stop_action: "pause", list: null, settings: {}, voice: false },
  { id: "fete", name: "La fête", enabled: true, party: true, guests: true, date: null, days: [],
    start: null, stop: null, stop_action: "pause", list: null, settings: {}, voice: false },
], settings: ["playback.base_volume"] };

function withSchedules(t) {
  return open(t, { hash: "#settings/settings", routes: {
    "GET /api/schedules": SCHEDULES,
    "POST /api/schedules": (request) => Object.assign({ id: "nouvelle" }, request.body),
    "POST /api/schedules/soir/voice": { seconds: 2 },
  } });
}

function choose(page, value) {
  page.$("schedWhen").value = value;
  page.$("schedWhen").dispatchEvent(new page.window.Event("change", { bubbles: true }));
}

test("a party has no days or times, may open the radio to guests, and says so in the list", async (t) => {
  const page = withSchedules(t);
  const rows = await until(() => {
    const found = page.$("scheduleList").querySelectorAll(".ann-item");
    return found.length === 2 && found;
  });
  assert.match(rows[1].textContent, /On demand: the party/);
  assert.match(rows[1].textContent, /open to guests/);

  page.$("scheduleFormSection").open = true;
  page.$("schedName").value = "Grande fête";
  choose(page, "party");
  assert.equal(page.$("schedStartRow").hidden, true);
  assert.equal(page.$("schedStopRow").hidden, true);
  assert.equal(page.$("schedDays").hidden, true);
  assert.equal(page.$("schedGuestsRow").hidden, false);
  assert.equal(page.$("schedPartyHint").hidden, false);
  assert.equal(page.$("schedSayRow").hidden, false, "a party says what a start says");
  page.$("schedGuests").checked = true;
  page.$("scheduleForm").dispatchEvent(new page.window.Event("submit", { bubbles: true, cancelable: true }));
  const made = (await until(() => page.sent("POST", "/api/schedules")[0])).body;
  assert.equal(made.party, true);
  assert.equal(made.guests, true);
  assert.deepEqual([made.start, made.stop, made.date, made.days], [null, null, null, []]);

  choose(page, "daily");
  assert.equal(page.$("schedGuestsRow").hidden, true, "guests belong to a party only");
  assert.equal(page.$("schedStartRow").hidden, false);
});

test("a recorded message waits for the schedule to exist, then is sent to its own address", async (t) => {
  const page = withSchedules(t);
  await until(() => page.$("scheduleList").querySelectorAll(".ann-item").length === 2);
  page.$("scheduleFormSection").open = true;
  assert.equal(page.$("schedVoice").disabled, true, "a schedule being made has no id yet");
  assert.equal(page.$("schedVoiceLater").hidden, false);

  page.$("scheduleList").querySelector(".sched-edit").click();
  await until(() => !page.$("schedVoice").disabled);
  assert.equal(page.$("schedVoiceLater").hidden, true);
  page.$("schedVoice").click();
  await until(() => !page.$("modalOverlay").hidden);
  assert.match(page.$("modalOverlay").textContent, /https:\/\//, "off HTTPS the microphone is said to need it");
  const picker = page.$("modalOverlay").querySelector('input[type="file"]');
  const sound = new page.window.Blob(["RIFF"], { type: "audio/wav" });
  Object.defineProperty(picker, "files", { value: [sound] });
  const keep = [...page.$("modalOverlay").querySelectorAll("button")]
    .find((button) => button.textContent.trim() === "Keep it");
  keep.click();
  await until(() => page.sent("POST", "/api/schedules/soir/voice").length);
  await until(() => page.sent("GET", "/api/schedules").length > 1);
  assert.deepEqual(page.errors, []);
});

test("what is said before the end: only with an end, a suggestion filled in, and sent with the schedule", async (t) => {
  const page = withSchedules(t);
  await until(() => page.$("scheduleList").querySelectorAll(".ann-item").length === 2);
  page.$("scheduleFormSection").open = true;
  page.$("schedName").value = "Apéro";
  choose(page, "daily");
  assert.equal(page.$("schedEndBox").hidden, true, "no stop, nothing to count down to");
  page.$("schedStopOn").checked = true;
  page.$("schedStopOn").dispatchEvent(new page.window.Event("change", { bubbles: true }));
  assert.equal(page.$("schedEndBox").hidden, false);

  page.$("schedEndAdd").click();
  const row = page.$("schedEndList").querySelector(".end-row");
  row.querySelector(".end-min").value = "10";
  const suggest = row.querySelector(".end-suggest");
  suggest.value = "schedules.end_suggest_left";
  suggest.dispatchEvent(new page.window.Event("change", { bubbles: true }));
  assert.equal(row.querySelector(".end-text").value, "10 more minutes of Apéro.", "filled in, still editable");

  page.$("schedEndAdd").click();
  const second = page.$("schedEndList").querySelectorAll(".end-row")[1];
  second.querySelector(".end-min").value = "0";
  second.querySelector(".end-kind").value = "voice";
  second.querySelector(".end-kind").dispatchEvent(new page.window.Event("change", { bubbles: true }));
  const voice = second.querySelector(".end-voice");
  assert.equal(voice.hidden, false);
  assert.equal(voice.disabled, true, "a schedule being made has no id to record for");
  assert.equal(second.querySelector(".end-text").hidden, true);

  page.$("scheduleForm").dispatchEvent(new page.window.Event("submit", { bubbles: true, cancelable: true }));
  const made = (await until(() => page.sent("POST", "/api/schedules")[0])).body;
  assert.deepEqual(made.before_end, [{ min: 10, kind: "text", text: "10 more minutes of Apéro." },
                                     { min: 0, kind: "voice" }]);
  assert.deepEqual(page.errors, []);
});
