"use strict";
// The interface's own rules, on the real page: the settings cards,
// announcements, the language and linked devices.
// (Four files rather than one: node runs files side by side, and the tests of a file in turn.)

const assert = require("node:assert/strict");
const { test } = require("node:test");
const { load, until, wait, open, deferred, STATUS, GUEST_PATHS, MEMBER_PATHS, OWNER, MARIE } = require("./harness");

test("a refused mode change leaves the switch where the radio was", async (t) => {
  const page = open(t, { routes: { "GET /api/settings": { "hardware.act_led": "default",
                                                           "hardware.usb_port_mode": "host" },
                                   "POST /api/settings": {} } });
  await until(() => page.$("bootOverlay").hidden);
  const select = page.$("usbPortMode");
  assert.equal(select.value, "host", "the switch shows the mode the radio is in");
  select.value = "gadget";
  select.dispatchEvent(new page.window.Event("change", { bubbles: true }));
  await until(() => page.$("modalOverlay").hidden === false);
  page.$("modalCancel").click();
  await wait(50);
  assert.equal(select.value, "host", "the switch goes back, it is not left on the other mode");
  assert.equal(page.sent("POST", "/api/settings").length, 0, "nothing was sent");
  assert.deepEqual(page.errors, []);
});

test("the network stream is a switch of the audio card", async (t) => {
  // The whole card as a Pi holds it, so that the switch is the only thing this
  // test changes.
  const held = { "audio.output": "bluetooth", "audio.fallback_output": "", "bluetooth.bt_audio_codecs": "",
                 "hardware.transfer_limit": "auto", "hardware.transfer_limit_kbps": "64", "hardware.transfer_limit_usb": "false",
                 "audio.stream_enabled": "false", "audio.stream_encoder": "", "audio.stream_source": "", "audio.stream_volume": "100",
                 "audio.upnp_name": "Rukebox 4821", "audio.snapcast_codec": "opus" };
  const page = open(t, { routes: { "GET /api/settings": held, "POST /api/settings": {} } });
  await until(() => page.$("bootOverlay").hidden);
  assert.equal(page.$("streamEnabled").checked, false, "the stream is off on a Pi");
  assert.equal(page.$("upnpEnabled"), null,
               "the announcement has no switch of its own: it follows the stream");
  assert.equal(page.$("streamEncoder").value, "", "the codec is picked by ffmpeg");
  page.$("streamEnabled").checked = true;
  page.$("streamEncoder").value = "mp3";
  page.$("audioOutputForm").dispatchEvent(
    new page.window.Event("submit", { bubbles: true, cancelable: true }));
  const saved = (await until(() => page.sent("POST", "/api/settings").length
    && page.sent("POST", "/api/settings")))[0].body;
  assert.deepEqual(saved, { "audio.stream_enabled": "true", "audio.stream_encoder": "mp3" });
  assert.deepEqual(page.errors, []);
});

test("the virtual output is only offered by a machine that has one", async (t) => {
  const option = (page) => page.$("audioOutputSelect").querySelector('option[value="docker"]');
  const read = (page) => until(() => page.$("audioOutputDetected").textContent.length > 0);

  const bare = open(t, { routes: { "GET /api/audio/outputs":
    { outputs: [{ name: "alsa_output.hdmi", description: "HDMI", kind: "hdmi" }] } } });
  await until(() => bare.$("bootOverlay").hidden);
  await read(bare);
  assert.equal(option(bare), null, "a Pi has no virtual output to offer");

  const container = open(t, { routes: { "GET /api/audio/outputs":
    { outputs: [{ name: "rukebox_output", description: "Rukebox output", kind: "docker" }] } } });
  await until(() => container.$("bootOverlay").hidden);
  await read(container);
  assert.ok(option(container), "a container offers it");
});

test("the Bluetooth codecs row only shows for a Bluetooth speaker", async (t) => {
  const held = { "audio.output": "jack", "audio.fallback_output": "", "bluetooth.bt_audio_codecs": "sbc_xq,sbc" };
  const page = open(t, { routes: { "GET /api/settings": held, "POST /api/settings": {} } });
  await until(() => page.$("bootOverlay").hidden);
  await until(() => page.$("audioOutputSelect").value === "jack");
  assert.equal(page.$("btCodecsRow").hidden, true,
               "a jack has no codec to negotiate: the row is not offered");

  page.$("audioOutputSelect").value = "bluetooth";
  page.$("audioOutputSelect").dispatchEvent(new page.window.Event("change"));
  assert.equal(page.$("btCodecsRow").hidden, false,
               "asking for the Bluetooth speaker brings the codecs back");
  assert.deepEqual(page.errors, []);
});

test("the announcement name only shows while the stream is on", async (t) => {
  const held = { "audio.output": "bluetooth", "audio.stream_enabled": "false", "audio.stream_encoder": "",
                 "audio.stream_source": "", "audio.stream_volume": "100",
                 "audio.upnp_name": "Rukebox 4821", "hardware.transfer_limit": "auto", "hardware.transfer_limit_kbps": "64",
                 "hardware.transfer_limit_usb": "false" };
  const page = open(t, { routes: { "GET /api/settings": held, "POST /api/settings": {} } });
  await until(() => page.$("bootOverlay").hidden);
  await until(() => page.$("upnpNeedsStream").hidden === false);

  // The stream's options are one folded group, the way the Bluetooth
  // controllers are: the card stops being a wall of rows.
  const section = page.$("streamSection");
  assert.equal(section.tagName, "DETAILS", "a fold, like the other subsections");
  assert.equal(section.open, false, "closed until it is asked for");
  assert.equal(section.querySelector("summary").textContent.trim(), "Network audio stream");
  ["streamEnabled", "streamEncoder", "streamSource", "streamVolume", "upnpName"]
    .forEach((id) => assert.ok(section.contains(page.$(id)), id + " is inside the group"));

  assert.equal(page.$("upnpNameRow").hidden, true, "nothing to announce: no name either");
  assert.equal(page.$("upnpName").value, "Rukebox 4821",
               "one field, the whole name");
  assert.equal(page.$("upnpSerialRow"), null, "there is no serial field any more");
  assert.equal(page.$("streamVolume").value, "100",
               "the stream's own volume is a setting of its own");

  const enabled = page.$("streamEnabled");
  enabled.checked = true;
  enabled.dispatchEvent(new page.window.Event("change", { bubbles: true }));
  assert.equal(page.$("upnpNameRow").hidden, false, "the stream is on: it is back");
  assert.equal(page.$("upnpNeedsStream").hidden, true);
});

test("the transfer limits are one folded group as well", async (t) => {
  const page = open(t);
  await until(() => page.$("bootOverlay").hidden);
  const section = page.$("transferLimitSection");
  assert.equal(section.tagName, "DETAILS", "a fold, like the stream's options");
  assert.equal(section.open, false, "closed until it is asked for");
  assert.equal(section.querySelector("summary").textContent.trim(), "Limit transfers");
  ["transferLimitMode", "transferLimitKbps", "transferLimitUsb"]
    .forEach((id) => assert.ok(section.contains(page.$(id)), id + " is inside the group"));
  assert.equal(section.dataset.level, "detail",
               "the rows were already for the detailed view only");
});

test("the settings that belong together are folded together", async (t) => {
  const page = open(t);
  await until(() => page.$("bootOverlay").hidden);
  const holds = (section, ids) => {
    assert.equal(page.$(section).tagName, "DETAILS", section + " is a fold");
    ids.forEach((id) => assert.ok(page.$(section).contains(page.$(id)),
                                  id + " is inside " + section));
  };

  holds("resumeSection", ["musicKeepProgressToggle", "musicResumeMode"]);
  holds("announceListsSection", ["announceOrderMode", "upcomingCount", "recentCount"]);
  holds("timersErrorsSection", ["pauseDurations", "sleepDurations", "retryMax", "retryDelay"]);
  holds("volumeChangeSection", ["volumeMode", "volumeChange", "volumeFadeSec", "volumeStep"]);
  holds("speakerVolumeSection", ["speakerVolumeLink", "speakerVolumeLock"]);
  holds("volumeCapSection", ["volumeCapOn", "volumeCapPercent", "volumeCapStart", "volumeCapEnd"]);
  holds("guestAllowedSection", ["suggestionsToggle", "dedicationsToggle", "queueFairToggle",
                                "renameInterval", "guestPagesOff", "guestLocked"]);
  holds("guestVoteSection", ["skipVoteToggle", "skipVoteShare", "actionRepeat"]);
  holds("guestCreditsSection", ["guestQuotaToggle", "guestQuotaMax", "guestQuotaRefill",
                                "guestQuotaRepeat"]);
  assert.ok(page.$("guestCreditsSection").contains(page.$("guestCostsSection")),
            "the prices fold is inside the credits group");

  // The visible rows of the volume card, in the order the owner asked for.
  const rows = Array.from(page.$("volumeForm").querySelectorAll(":scope > .field-row"));
  assert.deepEqual(rows.map((row) => row.querySelector("[data-key]").dataset.key),
                   ["playback.base_volume", "playback.audio_equalizer", "playback.audio_compression"]);
});

test("the cover setting is read into its own row", async (t) => {
  const page = open(t, { routes: { "GET /api/settings": { "playback.cover_priority": "id3" } } });
  await until(() => page.$("bootOverlay").hidden);
  await until(() => page.$("coverPriority").value === "id3");
  assert.equal(page.$("coverFolder"), null, "covers live in the music folder: no folder of their own");
});

test("the update card asks for permission first, then gathers its sources", async (t) => {
  const page = open(t);
  await until(() => page.$("bootOverlay").hidden);
  const card = page.$("updateCard");
  const rows = Array.from(card.querySelectorAll(".field-row, details.subsection > summary"));
  const labels = rows.map((row) => (row.matches("summary")
    ? row.textContent.trim()
    : (row.querySelector("label") || { textContent: "" }).textContent.trim()));
  assert.equal(labels[0], "Allow updates from the web",
               "the permission row comes before the folds");
  assert.ok(labels.indexOf("How to update") > 0, "then the instructions");

  const sources = page.$("updateSourcesSection");
  assert.equal(sources.tagName, "DETAILS", "the sources are a fold of their own");
  assert.ok(sources.contains(page.$("releaseRepo")), "the GitHub repository is in it");
  assert.equal(page.$("btnUpdateGit"), null, "updating from Git is gone");
  assert.equal(page.$("gitRepoDisplay"), null, "its label row is gone");
  assert.equal(page.$("updateAllowWeb").closest("details"), null,
               "the permission is not inside a fold");
});

test("a new announcement's form shows the rows of its trigger, and no others", async (t) => {
  const page = open(t);
  await until(() => page.$("bootOverlay").hidden);
  const shown = (id) => !page.$(id).hidden;
  const choose = (value) => {
    page.$("annTrigger").value = value;
    page.$("annTrigger").dispatchEvent(new page.window.Event("change", { bubbles: true }));
  };
  assert.deepEqual([shown("annTimeRow"), shown("annDelayRow"), shown("annRepeatRow"), shown("annAfterRow")],
                   [true, false, false, true], "a fixed time, as the page loads");
  choose("after_music");
  assert.deepEqual([shown("annTimeRow"), shown("annDelayRow"), shown("annRepeatRow"), shown("annAfterRow")],
                   [false, true, true, true]);
  choose("manual");
  assert.deepEqual([shown("annTimeRow"), shown("annDelayRow"), shown("annAfterRow"), shown("annAutoChanceRow")],
                   [false, false, false, false], "nothing follows a play started by hand");
});

test("an announcement is saved with what follows it", async (t) => {
  const page = open(t, { routes: { "POST /api/announcements": { id: "noon", name: "Noon" } } });
  await until(() => page.$("bootOverlay").hidden);
  page.$("annName").value = "Noon";
  page.$("annAfter").value = "pause";
  page.$("announcementForm").dispatchEvent(new page.window.Event("submit", { bubbles: true, cancelable: true }));
  const [sent] = await until(() => page.sent("POST", "/api/announcements").length && page.sent("POST", "/api/announcements"));
  assert.equal(sent.body.after_action, "pause");
  assert.equal(sent.body.trigger, "time");
});

test("ticking \"Connect at startup\" says so, even when an older status arrives meanwhile", async (t) => {
  const stale = deferred();
  let calls = 0;
  const page = open(t, { hash: "#network/homewifi", view: "detailed", routes: {
    "GET /api/wifi/status": () => {
      calls += 1;
      const answer = { configured: true, conn_name: "home", active: true, ip_address: "192.168.1.5/24",
                       autoconnect: false, client_here: false };
      return calls === 1 ? answer : stale.promise.then(() => answer);
    },
    "POST /api/wifi/autoconnect": {},
  } });
  // Opening its page asks again: that second answer is the one held back.
  await until(() => calls >= 2);

  const box = page.$("homeWifiAuto");
  box.checked = true;
  box.dispatchEvent(new page.window.Event("change", { bubbles: true }));
  await until(() => page.sent("POST", "/api/wifi/autoconnect").length);
  stale.resolve();
  await wait(60);

  assert.equal(page.sent("POST", "/api/wifi/autoconnect")[0].body.on, true);
  assert.equal(box.checked, true, "the box keeps what was ticked");
  const toasts = page.$("toastStack").textContent;
  assert.match(toasts, /will connect at startup/);
  assert.doesNotMatch(toasts, /stay off/);
});

test("changing the language translates the page and keeps the help buttons", async (t) => {
  const page = open(t);
  await until(() => page.$("bootOverlay").hidden);
  const title = page.document.querySelector("#annTitle [data-i18n]") || page.$("annTitle");
  const before = title.textContent;
  const helps = page.document.querySelectorAll(".help-btn").length;
  page.$("langToggleBtn").click();
  await until(() => title.textContent !== before);
  assert.equal(page.document.querySelectorAll(".help-btn").length, helps);
  assert.ok(helps > 0);
});

test("a device shows a one-time code, and another one types it", async (t) => {
  const page = open(t, { routes: {
    "GET /api/suggestions": { me: { name: "Renard bleu", rename_wait: 0, locked: false, linked: 1 },
                              owner: true, text_max: { music: 200, announcement: 500 }, items: [] },
    "POST /api/devices/link_code": { code: "123456", expires_in: 300 },
    "POST /api/devices/link_join": { name: "Loutre verte" },
  } });
  await until(() => page.$("bootOverlay").hidden && !page.$("suggestMeLine").hidden);
  page.$("suggestLinkBtn").click();
  const dialog = await until(() => page.document.querySelector("#modalBody .link-dialog"));
  assert.match(dialog.textContent, /already linked to this one: 1/);

  dialog.querySelector(".link-part button").click();
  const code = dialog.querySelector(".link-code");
  await until(() => !code.hidden);
  assert.equal(code.textContent.replace(/\s/g, ""), "123456");

  const input = page.$("linkCodeInput");
  input.value = "654 321";
  input.form.dispatchEvent(new page.window.Event("submit", { bubbles: true, cancelable: true }));
  const sent = (await until(() => page.sent("POST", "/api/devices/link_join").length
    && page.sent("POST", "/api/devices/link_join")))[0];
  assert.deepEqual(sent.body, { code: "654321" });
  await until(() => page.$("modalOverlay").hidden);
  assert.match(page.$("toastStack").textContent, /Loutre verte/);
});

test("the owner links a device to another person's, and may unlink it", async (t) => {
  const page = open(t, { hash: "#network/clients", routes: {
    "GET /api/wifi/clients": { readable: true, clients: [
      { mac: "aa:00:00:00:00:01", ip: "10.42.0.11", on_ap: true, device_id: "d1", person: "d1",
        name: "Renard bleu", linked: [] },
      { mac: "aa:00:00:00:00:02", ip: "10.42.0.12", on_ap: true, device_id: "d2", person: "d2",
        name: "Loutre verte", linked: [{ device_id: "d3", mac: "aa:00:00:00:00:03", ip: "10.42.0.13" }] },
    ] },
    "POST /api/devices/link": {},
    "POST /api/devices/unlink": {},
  } });
  await until(() => page.$("clientList").querySelectorAll(".client-link").length === 2);
  const rows = [...page.$("clientList").children];
  assert.equal(rows[0].querySelector(".client-unlink"), null, "nothing to unlink on a device alone");
  assert.match(rows[1].textContent, /10\.42\.0\.13/, "the row names what it is linked with");

  rows[0].querySelector(".client-link").click();
  const choice = await until(() => [...page.document.querySelectorAll("#modalChoices button")]
    .find((button) => button.textContent === "Loutre verte"));
  assert.equal(page.document.querySelectorAll("#modalChoices button").length, 1, "one choice per person");
  choice.click();
  const linked = (await until(() => page.sent("POST", "/api/devices/link").length
    && page.sent("POST", "/api/devices/link")))[0];
  assert.deepEqual(linked.body, { device_id: "d1", to: "d2" });

  const unlink = await until(() => page.$("clientList").querySelector(".client-unlink"));
  unlink.click();
  await until(() => !page.$("modalOverlay").hidden);
  page.$("modalOk").click();
  const gone = (await until(() => page.sent("POST", "/api/devices/unlink").length
    && page.sent("POST", "/api/devices/unlink")))[0];
  assert.deepEqual(gone.body, { device_id: "d2" });
});

test("a schedule is saved with its days, its times and the settings it holds", async (t) => {
  const page = open(t, { hash: "#settings/settings", routes: {
    "GET /api/settings": { "playback.loop": "true", "buttons.single_click_action": "next", "schedule.cutoff_hour": "23", "schedule.cutoff_minute": "30",
                           "schedule.shutdown_after_cutoff": "false" },
    "GET /api/schedules": { schedules: [
      { id: "soir", name: "Le soir", enabled: true, date: null, days: [4, 5], start: "20:00", stop: "23:00",
        stop_action: "standby", list: null, settings: { "playback.base_volume": "40", "playback.loop": "false" } },
    ], settings: ["playback.base_volume", "schedule.cutoff_hour", "schedule.cutoff_minute", "playback.loop", "buttons.single_click_action"] },
    "POST /api/schedules": (request) => Object.assign({ id: "matin" }, request.body),
    "POST /api/schedules/soir": (request) => Object.assign({ id: "soir", name: "Le soir" }, request.body),
  } });
  await until(() => page.$("scheduleList").querySelector(".ann-item") && page.$("afterCutoff").value === "false");
  const row = page.$("scheduleList").querySelector(".ann-item");
  assert.match(row.textContent, /20:00 . 23:00/);
  assert.match(row.textContent, /Standby/);

  page.$("scheduleFormSection").open = true;
  page.$("schedName").value = "Le matin";
  page.$("schedWhen").value = "days";
  page.$("schedWhen").dispatchEvent(new page.window.Event("change", { bubbles: true }));
  assert.equal(page.$("schedDays").hidden, false);
  const chips = page.$("schedDays").querySelectorAll("input");
  assert.equal(chips.length, 7);
  chips[0].checked = true;
  chips[2].checked = true;
  page.$("schedVolume").value = "30";

  const choices = [...page.$("schedAddSetting").options].map((option) => option.value);
  assert.ok(choices.includes("playback.loop") && choices.includes("schedule.cutoff_hour"));
  assert.ok(!choices.includes("playback.base_volume"), "the volume has a field of its own");
  assert.ok(!choices.includes("schedule.cutoff_minute"), "the cutoff is one time, not two numbers");
  page.$("schedAddSetting").value = "playback.loop";
  page.$("schedAddSetting").dispatchEvent(new page.window.Event("change", { bubbles: true }));
  const loop = page.$("schedOverrides").querySelector('[data-override="playback.loop"]');
  assert.equal(loop.checked, true, "it starts from the usual value");
  loop.checked = false;
  page.$("schedAddSetting").value = "schedule.cutoff_hour";
  page.$("schedAddSetting").dispatchEvent(new page.window.Event("change", { bubbles: true }));
  assert.equal(page.$("schedOverrides").querySelector('[data-override="schedule.cutoff_hour"]').value, "23:30");
  assert.equal(page.document.querySelectorAll('#main [data-key="playback.loop"]').length, 1,
               "a copy is not a settings field");

  page.$("scheduleForm").dispatchEvent(new page.window.Event("submit", { bubbles: true, cancelable: true }));
  const made = (await until(() => page.sent("POST", "/api/schedules").length
    && page.sent("POST", "/api/schedules")))[0].body;
  assert.deepEqual(made, { name: "Le matin", days: [0, 2], date: null, yearly: false, party: false, guests: false, start: "07:00", stop: null,
                           stop_action: "pause", list: null, announcement: null, say: null,
                           settings: { "playback.loop": "false", "schedule.cutoff_hour": "23", "schedule.cutoff_minute": "30",
                                       "playback.base_volume": "30" } });

  (await until(() => page.$("scheduleList").querySelector(".sched-edit"))).click();
  assert.equal(page.$("schedName").value, "Le soir");
  assert.equal(page.$("schedVolume").value, "40");
  assert.equal(page.$("schedStopOn").checked, true);
  assert.equal(page.$("schedActionRow").hidden, false);
  assert.equal(page.$("schedOverrides").querySelector('[data-override="playback.loop"]').checked, false);
  page.$("schedOverrides").querySelector(".override-remove").click();
  page.$("scheduleForm").dispatchEvent(new page.window.Event("submit", { bubbles: true, cancelable: true }));
  const changed = (await until(() => page.sent("POST", "/api/schedules/soir").length
    && page.sent("POST", "/api/schedules/soir")))[0].body;
  assert.deepEqual(changed.settings, { "playback.base_volume": "40" });
  assert.deepEqual(changed.days, [4, 5]);
  assert.deepEqual(page.errors, []);
});
