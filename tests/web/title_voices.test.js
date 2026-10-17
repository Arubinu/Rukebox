"use strict";
// The voices for titles in other languages (web/js/init/06-announcements.js): listed when the
// voice settings open, installed or removed from there, and hidden with titles kept in one language.
const test = require("node:test");
const assert = require("node:assert/strict");

const { until, open } = require("./harness");

function voices(installing, installed) {
  return {
    main: "fr", piper: true, installing, error: "",
    voices: ["en", "de", "es", "it", "nl"].map((lang) => ({ lang, voice: lang, installed: installed.includes(lang) })),
  };
}

test("a voice for titles is installed from the voice settings, and the toggle hides the list", async (t) => {
  let state = voices(null, ["de"]);
  const page = open(t, { hash: "#settings/settings", routes: {
    "GET /api/settings": { "schedule.dj_title_language": "true" },
    "GET /api/speech/voices": () => state,
    "POST /api/speech/voices": (request) => {
      state = voices(request.body.lang, ["de"]);
      return state;
    },
  } });
  await until(() => page.sent("GET", "/api/settings").length);
  const section = page.$("voiceSection");
  section.open = true;
  section.dispatchEvent(new page.window.Event("toggle"));
  await until(() => page.$("titleVoiceList").children.length === 5);
  const rows = [...page.$("titleVoiceList").children];
  assert.equal(rows[0].querySelector(".title-voice-name").textContent, "English");
  assert.equal(rows[0].querySelector(".title-voice-state").textContent, "Not installed");
  assert.equal(rows[1].querySelector(".title-voice-state").textContent, "Installed");
  assert.equal(rows[1].querySelector("button").dataset.icon, "trash");

  rows[0].querySelector("button").click();
  await until(() => page.sent("POST", "/api/speech/voices").length);
  assert.deepEqual(page.sent("POST", "/api/speech/voices")[0].body, { lang: "en", remove: false });
  await until(() => page.$("titleVoiceList").children[0].querySelector(".title-voice-state").textContent
    === "Downloading…");
  assert.ok([...page.$("titleVoiceList").querySelectorAll("button")].every((b) => b.disabled),
    "one download at a time");

  const toggle = page.$("djTitleLanguage");
  toggle.checked = false;
  toggle.dispatchEvent(new page.window.Event("change"));
  assert.equal(page.$("titleVoices").hidden, true);
  state = voices(null, ["de", "en"]);
});
