"use strict";
// The interface's own rules, on the real page: what it loads, what it sends,
// and what it never asks for.

const assert = require("node:assert/strict");
const { test } = require("node:test");
const { load, until, wait } = require("./harness");

// The page is closed whatever the test does: its timers would keep the run alive.
function open(t, options) {
  const page = load(options);
  t.after(() => page.close());
  return page;
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

test("the interface starts without an error and lands on the player", async (t) => {
  const page = open(t);
  await until(() => page.$("bootOverlay").hidden);
  assert.deepEqual(page.errors, []);
  assert.equal(page.document.body.dataset.tab, "home");
  assert.equal(page.document.body.dataset.page, "player");
  assert.equal(page.window.location.hash, "#home/player");
});

test("an address with a page opens that page", async (t) => {
  const page = open(t, { hash: "#settings/announcements" });
  await until(() => page.document.body.dataset.page === "announcements");
  assert.equal(page.document.body.dataset.tab, "settings");
});

test("a menu is built from the cards: one tile per page that has something to show", async (t) => {
  const page = open(t);
  await until(() => page.$("bootOverlay").hidden);
  const player = page.document.querySelector('.page-tile[data-page="player"]');
  assert.ok(player && !player.hidden, "the player is a page");
  const offered = [...player.parentElement.querySelectorAll(".page-tile")]
    .filter((tile) => !tile.hidden).map((tile) => tile.dataset.page);
  const empty = [...page.document.querySelectorAll('.card[data-tab="home"][hidden]')].map((card) => card.dataset.page);
  assert.ok(empty.length > 0, "the fakes leave some cards with nothing to show");
  for (const name of empty) assert.ok(!offered.includes(name), name + " has nothing to show, so no tile");
  for (const name of offered) {
    const card = page.document.querySelector('.card[data-tab="home"][data-page="' + name + '"]');
    assert.ok(card && !card.hidden, name + " is a card that shows something");
  }
});

test("with a password and no guest access, only the login is shown", async (t) => {
  const page = open(t, { routes: {
    "GET /api/portal/status": { enabled: true, mode: "release", on_ap: false, released: true,
                                guest_mode: false, auth_required: true, authenticated: false },
  } });
  await until(() => !page.$("loginOverlay").hidden);
  assert.equal(page.sent("GET", "/api/status").length, 0, "nothing is asked before the login");
  assert.equal(page.sent("GET", "/api/settings").length, 0);
});

test("a guest never asks for what a guest may not have", async (t) => {
  const page = open(t, { routes: {
    "GET /api/portal/status": { enabled: true, mode: "release", on_ap: false, released: true,
                                guest_mode: true, auth_required: true, authenticated: false },
  } });
  await until(() => page.$("bootOverlay").hidden);
  assert.equal(page.document.body.dataset.access, "guest");
  assert.ok(page.sent("GET", "/api/status").length > 0, "the status is a guest's");
  for (const path of ["/api/settings", "/api/wifi/ap", "/api/wifi/clients", "/api/journal/summary",
                      "/api/system/info", "/api/lists", "/api/likes"]) {
    assert.equal(page.sent("GET", path).length, 0, path + " is the owner's");
  }
  assert.ok(!page.document.querySelector('.page-tile[data-page="music"]:not([hidden])'),
            "adding music is the owner's, although its card sits on Home");
  assert.deepEqual(page.errors, []);
});

test("every area of the tab bar has pages, and every page belongs to an area of the bar", async (t) => {
  const page = open(t);
  await until(() => page.$("bootOverlay").hidden);
  const areas = [...page.document.querySelectorAll(".tab-btn")].map((btn) => btn.dataset.tab);
  assert.deepEqual(areas, ["home", "settings", "audio", "network", "system", "stats"]);
  const cards = [...page.document.querySelectorAll(".card[data-tab][data-page]")];
  for (const area of areas) assert.ok(cards.some((card) => card.dataset.tab === area), area + " has a page");
  for (const card of cards) assert.ok(areas.includes(card.dataset.tab), card.dataset.page + " is in the bar");
  const where = (name) => cards.filter((card) => card.dataset.page === name).map((card) => card.dataset.tab);
  assert.deepEqual(where("clock"), ["system"]);
  assert.deepEqual(where("accesspoint"), ["network"]);
  assert.deepEqual(where("guest"), ["network"]);
  assert.deepEqual(where("music"), ["home"]);
});

test("saving a settings card sends only what was changed", async (t) => {
  const submit = (one) => one.$("volumeForm").dispatchEvent(
    new one.window.Event("submit", { bubbles: true, cancelable: true }));
  const saved_by = async (one) => (await until(
    () => one.sent("POST", "/api/settings").length && one.sent("POST", "/api/settings")))[0];

  // A page that was told nothing sends the whole card: that is what the Pi would hold.
  const blank = open(t, { routes: { "POST /api/settings": {} } });
  await until(() => blank.$("bootOverlay").hidden);
  submit(blank);
  const stored = (await saved_by(blank)).body;
  assert.ok(Object.keys(stored).length > 5);

  const page = open(t, { routes: { "GET /api/settings": stored, "POST /api/settings": {} } });
  await until(() => page.$("bootOverlay").hidden);
  submit(page);
  await wait(100);
  assert.equal(page.sent("POST", "/api/settings").length, 0, "nothing changed, nothing sent");

  const field = page.$("baseVolume");
  field.value = "55";
  field.dispatchEvent(new page.window.Event("input", { bubbles: true }));
  field.dispatchEvent(new page.window.Event("change", { bubbles: true }));
  submit(page);
  const saved = await saved_by(page);
  assert.deepEqual(Object.keys(saved.body), ["BASE_VOLUME"]);
  assert.equal(String(saved.body.BASE_VOLUME), "55");
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
    "GET /api/settings": { MUSIC_LOOP: "true", SINGLE_CLICK_ACTION: "next", CUTOFF_HOUR: "23", CUTOFF_MINUTE: "30",
                           SHUTDOWN_AFTER_CUTOFF: "false" },
    "GET /api/schedules": { schedules: [
      { id: "soir", name: "Le soir", enabled: true, date: null, days: [4, 5], start: "20:00", stop: "23:00",
        stop_action: "standby", list: null, settings: { BASE_VOLUME: "40", MUSIC_LOOP: "false" } },
    ], settings: ["BASE_VOLUME", "CUTOFF_HOUR", "CUTOFF_MINUTE", "MUSIC_LOOP", "SINGLE_CLICK_ACTION"] },
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
  assert.ok(choices.includes("MUSIC_LOOP") && choices.includes("CUTOFF_HOUR"));
  assert.ok(!choices.includes("BASE_VOLUME"), "the volume has a field of its own");
  assert.ok(!choices.includes("CUTOFF_MINUTE"), "the cutoff is one time, not two numbers");
  page.$("schedAddSetting").value = "MUSIC_LOOP";
  page.$("schedAddSetting").dispatchEvent(new page.window.Event("change", { bubbles: true }));
  const loop = page.$("schedOverrides").querySelector('[data-override="MUSIC_LOOP"]');
  assert.equal(loop.checked, true, "it starts from the usual value");
  loop.checked = false;
  page.$("schedAddSetting").value = "CUTOFF_HOUR";
  page.$("schedAddSetting").dispatchEvent(new page.window.Event("change", { bubbles: true }));
  assert.equal(page.$("schedOverrides").querySelector('[data-override="CUTOFF_HOUR"]').value, "23:30");
  assert.equal(page.document.querySelectorAll('#main [data-key="MUSIC_LOOP"]').length, 1,
               "a copy is not a settings field");

  page.$("scheduleForm").dispatchEvent(new page.window.Event("submit", { bubbles: true, cancelable: true }));
  const made = (await until(() => page.sent("POST", "/api/schedules").length
    && page.sent("POST", "/api/schedules")))[0].body;
  assert.deepEqual(made, { name: "Le matin", days: [0, 2], date: null, start: "07:00", stop: null,
                           stop_action: "pause", list: null, announcement: null,
                           settings: { MUSIC_LOOP: "false", CUTOFF_HOUR: "23", CUTOFF_MINUTE: "30",
                                       BASE_VOLUME: "30" } });

  (await until(() => page.$("scheduleList").querySelector(".sched-edit"))).click();
  assert.equal(page.$("schedName").value, "Le soir");
  assert.equal(page.$("schedVolume").value, "40");
  assert.equal(page.$("schedStopOn").checked, true);
  assert.equal(page.$("schedActionRow").hidden, false);
  assert.equal(page.$("schedOverrides").querySelector('[data-override="MUSIC_LOOP"]').checked, false);
  page.$("schedOverrides").querySelector(".override-remove").click();
  page.$("scheduleForm").dispatchEvent(new page.window.Event("submit", { bubbles: true, cancelable: true }));
  const changed = (await until(() => page.sent("POST", "/api/schedules/soir").length
    && page.sent("POST", "/api/schedules/soir")))[0].body;
  assert.deepEqual(changed.settings, { BASE_VOLUME: "40" });
  assert.deepEqual(changed.days, [4, 5]);
  assert.deepEqual(page.errors, []);
});

test("schedules can be reordered and copied, and the week shows them", async (t) => {
  const schedules = [
    { id: "matin", name: "Le matin", enabled: true, date: null, days: [], start: "07:00", stop: "09:00",
      stop_action: "pause", list: null, announcement: "reveil", settings: { BASE_VOLUME: "30" } },
    { id: "nuit", name: "La nuit", enabled: true, date: null, days: [], start: "22:00", stop: "01:30",
      stop_action: "standby", list: null, announcement: null, settings: {} },
    { id: "off", name: "Coupee", enabled: false, date: null, days: [], start: "12:00", stop: "13:00",
      stop_action: "pause", list: null, announcement: null, settings: {} },
  ];
  const page = open(t, { hash: "#settings/settings", routes: {
    "GET /api/settings": { CUTOFF_ENABLED: "true", CUTOFF_HOUR: "23", CUTOFF_MINUTE: "30" },
    "GET /api/announcements": [{ id: "reveil", name: "Reveil", enabled: true, trigger: "manual", hour: 7,
                                 minute: 0, file_count: 2, folder: "/home/pi/audio/reveil" }],
    "GET /api/schedules": { schedules, settings: ["BASE_VOLUME"] },
    "POST /api/schedule_order": (request) => ({
      schedules: request.body.order.map((id) => schedules.find((one) => one.id === id)) }),
    "POST /api/schedules": (request) => Object.assign({ id: "copie" }, request.body),
  } });
  const week = await until(() => page.$("scheduleWeek").querySelectorAll(".week-row:not(.week-axis)").length === 7
    && page.$("scheduleWeek").querySelector(".week-mark.is-cutoff") && page.$("scheduleWeek"));
  const first = week.querySelector(".week-row");
  const bars = [...first.querySelectorAll(".week-seg")].map((bar) => [bar.textContent, bar.title]);
  assert.deepEqual(bars, [["La nuit", "La nuit 00:00\u201301:30"], ["Le matin", "Le matin 07:00\u201309:00"],
                          ["La nuit", "La nuit 22:00\u201300:00"]],
                   "last night's end, this morning, tonight's start - and nothing for the one switched off");
  assert.match(first.querySelector(".week-track").getAttribute("aria-label"), /daily cutoff 23:30/);

  const rows = () => [...page.$("scheduleList").querySelectorAll(".ann-item")].map((row) => row.dataset.id);
  const second = page.$("scheduleList").querySelectorAll(".ann-item")[1];
  assert.equal(page.$("scheduleList").querySelector('.sched-move[data-step="-1"]').disabled, true,
               "the first one cannot go higher");
  second.querySelector('.sched-move[data-step="-1"]').click();
  const order = (await until(() => page.sent("POST", "/api/schedule_order").length
    && page.sent("POST", "/api/schedule_order")))[0].body;
  assert.deepEqual(order, { order: ["nuit", "matin", "off"] });
  await until(() => rows()[0] === "nuit");

  page.$("scheduleList").querySelector('.ann-item[data-id="matin"] .sched-copy').click();
  assert.equal(page.$("schedName").value, "Le matin (copy)");
  assert.equal(page.$("schedAnnouncement").value, "reveil");
  assert.equal(page.$("schedAnnouncementRow").hidden, false);
  page.$("scheduleForm").dispatchEvent(new page.window.Event("submit", { bubbles: true, cancelable: true }));
  const copy = (await until(() => page.sent("POST", "/api/schedules").length
    && page.sent("POST", "/api/schedules")))[0].body;
  assert.equal(copy.name, "Le matin (copy)");
  assert.equal(copy.announcement, "reveil");
  assert.deepEqual(copy.settings, { BASE_VOLUME: "30" });
  assert.equal(page.sent("POST", "/api/schedules/matin").length, 0, "a copy is a new schedule, not a change");

  const cutoff = page.$("cutoffEnabled");
  assert.equal(page.$("cutoffTime").closest(".field-row").hidden, false);
  cutoff.checked = false;
  cutoff.dispatchEvent(new page.window.Event("change", { bubbles: true }));
  assert.equal(page.$("cutoffTime").closest(".field-row").hidden, true);
  assert.equal(page.$("afterCutoff").closest(".field-row").hidden, true);
  assert.deepEqual(page.errors, []);
});

test("an announcement can be copied into a new one on the same folder", async (t) => {
  const page = open(t, { hash: "#settings/announcements", routes: {
    "GET /api/announcements": [{ id: "reveil", name: "Reveil", enabled: true, trigger: "time", hour: 7,
                                 minute: 15, file_count: 2, folder: "/home/pi/audio/reveil",
                                 after_action: "pause", auto_chance: "1/2", manual_chance: "1/1",
                                 delay_min: 30, repeat_times: 1 }],
    "POST /api/announcements": (request) => Object.assign({ id: "reveil-copy" }, request.body),
  } });
  (await until(() => page.$("announcementList").querySelector(".ann-duplicate"))).click();
  assert.equal(page.$("annName").value, "Reveil (copy)");
  assert.equal(page.$("annFolder").value, "/home/pi/audio/reveil");
  assert.equal(page.$("annTime").value, "07:15");
  assert.equal(page.$("annSubmit").dataset.i18n, "common.add", "it will be added, not saved over the first");
  page.$("announcementForm").dispatchEvent(new page.window.Event("submit", { bubbles: true, cancelable: true }));
  const made = (await until(() => page.sent("POST", "/api/announcements").length
    && page.sent("POST", "/api/announcements")))[0].body;
  assert.equal(made.name, "Reveil (copy)");
  assert.equal(made.after_action, "pause");
  assert.equal(page.sent("POST", "/api/announcements/reveil").length, 0);
});

test("the player says which schedule runs, and which one comes next", async (t) => {
  const { STATUS } = require("./harness");
  const running = open(t, { routes: {
    "GET /api/status": Object.assign({}, STATUS, { schedule: { id: "soir", name: "Le soir", until: "23:00" } }),
  } });
  await until(() => /Le soir/.test(running.$("npNotices").textContent));
  assert.match(running.$("npNotices").textContent, /until 23:00/);

  const waiting = open(t, { routes: {
    "GET /api/status": Object.assign({}, STATUS, { mode: "idle", music_start_mode: "action",
      schedule_next: { id: "matin", name: "Le matin", at: "07:00", date: "2026-10-02" } }),
  } });
  await until(() => /Le matin/.test(waiting.$("npNotices").textContent));
  assert.match(waiting.$("npNotices").textContent, /Le matin, 07:00/);
});

test("only a captive window is sent to its system's probe after the release", async (t) => {
  const page = open(t, { expose: ["captiveExit"] });
  await until(() => page.$("bootOverlay").hidden);
  const exit = page.window.__exposed.captiveExit;
  const webkit = "AppleWebKit/605.1.15 (KHTML, like Gecko)";
  const apple = "http://captive.apple.com/hotspot-detect.html";
  assert.equal(exit("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) " + webkit + " Mobile/15E148"), apple,
               "the iPhone's portal window");
  assert.equal(exit("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) " + webkit), apple, "the Mac's");
  assert.equal(exit("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) " + webkit
                    + " Version/17.5 Mobile/15E148 Safari/604.1"), null, "Safari stays on the interface");
  assert.equal(exit("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) " + webkit
                    + " CriOS/126.0 Mobile/15E148 Safari/604.1"), null, "so does Chrome on an iPhone");
  assert.equal(exit("Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/126.0 Mobile Safari/537.36"),
               "http://connectivitycheck.gstatic.com/generate_204", "Android is as before");
});

test("the side menu says what plays, and leads to the player", async (t) => {
  const page = open(t, { hash: "#settings/playback" });
  await until(() => !page.$("navNow").hidden);
  assert.equal(page.$("navNowTitle").textContent, "Song");
  assert.equal(page.$("navNowArtist").textContent, "Artist");
  assert.match(page.$("navNow").getAttribute("aria-label"), /Song . Artist/);
  page.$("navNow").click();
  assert.equal(page.document.body.dataset.tab + "/" + page.document.body.dataset.page, "home/player");

  const { STATUS } = require("./harness");
  const idle = open(t, { routes: { "GET /api/status": Object.assign({}, STATUS, { mode: "idle" }) } });
  await until(() => idle.$("bootOverlay").hidden);
  await wait(60);
  assert.equal(idle.$("navNow").hidden, true, "nothing plays, nothing to say");
});

test("a linked device can leave by itself, after being asked", async (t) => {
  const page = open(t, { routes: {
    "GET /api/suggestions": { me: { name: "Renard bleu", rename_wait: 0, locked: false, linked: 2 },
                              owner: false, text_max: { music: 200, announcement: 500 }, items: [] },
    "POST /api/devices/link_leave": {},
  } });
  await until(() => page.$("bootOverlay").hidden && !page.$("suggestMeLine").hidden);
  page.$("suggestLinkBtn").click();
  (await until(() => page.document.querySelector("#modalBody .link-leave"))).click();
  await until(() => !page.$("modalOk").hidden && !page.$("modalOverlay").hidden);
  assert.equal(page.sent("POST", "/api/devices/link_leave").length, 0, "not before the answer");
  page.$("modalOk").click();
  await until(() => page.sent("POST", "/api/devices/link_leave").length);

  const alone = open(t, { routes: {
    "GET /api/suggestions": { me: { name: "Renard bleu", rename_wait: 0, locked: false, linked: 0 },
                              owner: false, text_max: { music: 200, announcement: 500 }, items: [] },
  } });
  await until(() => alone.$("bootOverlay").hidden && !alone.$("suggestMeLine").hidden);
  alone.$("suggestLinkBtn").click();
  await until(() => alone.document.querySelector("#modalBody .link-dialog"));
  assert.equal(alone.document.querySelector("#modalBody .link-leave"), null);
});

test("the network diagnostic is asked for and shown as it came", async (t) => {
  const text = ["Network diagnostic", "findings:", " - nothing wrong"].join("\n");
  const page = open(t, { hash: "#system/health", routes: {
    "POST /api/diag/network": { report: text },
  } });
  await until(() => page.$("bootOverlay").hidden);
  page.$("btnNetDiag").click();
  const shown = await until(() => page.document.querySelector("#modalBody .diag-text"));
  assert.equal(shown.textContent, text);
  assert.equal(page.sent("POST", "/api/diag/network").length, 1);
  assert.equal(page.sent("POST", "/api/diag/audio").length, 0);
});

test("new suggestions show a badge to the owner until the page is opened", async (t) => {
  const item = (id, extra) => Object.assign({ id, kind: "music", text: "Song " + id, author: "Renard bleu",
                                               created_at: id, status: "open", up: 0, down: 0, my_vote: 0,
                                               mine: false }, extra);
  const list = { me: { name: "Moi", rename_wait: 0, locked: false, linked: 0 }, owner: true,
                 text_max: { music: 200, announcement: 500 },
                 items: [item(4), item(6), item(7, { mine: true }), item(8, { status: "declined" })] };
  const page = open(t, { hash: "#home", storage: { rukebox_suggest_seen: "5" },
                         routes: { "GET /api/suggestions": list } });
  const tile = await until(() => page.document.querySelector('.page-tile[data-page="suggest"] .page-tile-icon[data-badge]'));
  assert.equal(tile.dataset.badge, "1", "only the open one, newer than the last seen, not the owner's own");
  assert.equal(page.document.querySelector('.tab-btn[data-tab="home"] .tab-icon').dataset.badge, "1");
  assert.equal(page.document.querySelector('.rail-page[data-page="suggest"]').dataset.badge, "1");

  page.document.querySelector('.page-tile[data-page="suggest"]').click();
  await until(() => !page.document.querySelector('.tab-btn[data-tab="home"] .tab-icon').dataset.badge);
  assert.equal(page.window.localStorage.getItem("rukebox_suggest_seen"), "8");

  const first = open(t, { hash: "#home", routes: { "GET /api/suggestions": list } });
  await until(() => first.window.localStorage.getItem("rukebox_suggest_seen") === "8");
  assert.equal(first.document.querySelector("[data-badge]"), null, "a first visit counts from now");

  const guest = open(t, { hash: "#home", storage: { rukebox_suggest_seen: "0" },
                          routes: { "GET /api/suggestions": Object.assign({}, list, { owner: false }) } });
  await until(() => guest.$("bootOverlay").hidden);
  await wait(80);
  assert.equal(guest.document.querySelector("[data-badge]"), null, "only the owner is told");
});

test("arriving on the player, its own answers come first and the rest after the loading screen", async (t) => {
  const page = open(t);
  await until(() => page.sent("GET", "/api/status").length);
  await wait(50);
  assert.equal(page.$("bootOverlay").hidden || page.$("bootOverlay").classList.contains("boot-gone"), false);
  assert.equal(page.sent("GET", "/api/settings").length, 0, "nothing the player does not show yet");
  assert.equal(page.sent("GET", "/api/system/info").length, 0);
  await until(() => page.$("bootOverlay").classList.contains("boot-gone") || page.$("bootOverlay").hidden);
  await until(() => page.sent("GET", "/api/settings").length);

  const deep = open(t, { hash: "#system/health" });
  await until(() => deep.sent("GET", "/api/status").length);
  await until(() => deep.sent("GET", "/api/settings").length);
  assert.equal(deep.$("bootOverlay").hidden, false, "another page asks for everything at once, as before");
});

test("a command locked for guests shows a padlock and cannot be pressed", async (t) => {
  const { STATUS } = require("./harness");
  const page = open(t, { routes: {
    "GET /api/portal/status": { enabled: true, mode: "release", on_ap: false, released: true,
                                guest_mode: true, auth_required: true, authenticated: false },
    "GET /api/status": Object.assign({}, STATUS, { guest_locked: ["next", "volume"] }),
  } });
  await until(() => page.$("btnNext").hasAttribute("data-locked"));
  assert.equal(page.$("btnNext").getAttribute("aria-disabled"), "true");
  assert.equal(page.document.querySelector(".volume-row").hasAttribute("data-locked"), true);
  assert.equal(page.$("btnPrev").hasAttribute("data-locked"), false);
});

test("the owner picks the locked commands in a list, and may lock none", async (t) => {
  const page = open(t, { hash: "#network/guest", routes: {
    "GET /api/settings": { GUEST_LOCKED: "volume" },
  } });
  const field = await until(() => page.$("guestLocked").querySelector(".codec-open"));
  await until(() => page.$("guestLocked").value === "volume");
  field.click();
  const boxes = await until(() => page.document.querySelectorAll("#modalBody .codec-list input"));
  assert.equal(boxes.length, 10);
  const volume = [...boxes].find((box) => box.value === "volume");
  assert.equal(volume.disabled, false, "the last one can be unticked: nothing locked is allowed");
  volume.checked = false;
  volume.dispatchEvent(new page.window.Event("change", { bubbles: true }));
  assert.equal(page.$("guestLocked").value, "");
});

test("beside the menu, the portal button sits in the menu's foot, not over its pages", async (t) => {
  const held = { "GET /api/portal/status": { enabled: true, mode: "release", on_ap: true, released: false } };
  const wide = open(t, { routes: held, media: (query) => query.includes("640px") });
  await until(() => !wide.$("portalReleaseBtn").hidden);
  await until(() => wide.$("guestRelease").parentElement === wide.$("navFoot"));
  assert.equal(wide.$("portalReleaseBtn").parentElement, wide.$("guestRelease"));

  const phone = open(t, { routes: held });
  await until(() => !phone.$("portalReleaseBtn").hidden);
  assert.equal(phone.$("guestRelease").parentElement, phone.$("main"), "a phone keeps it at the top of the grid");
});

test("the header's menu opens on its button and closes on any other tap", async (t) => {
  const page = open(t);
  await until(() => page.sent("GET", "/api/status").length);
  const bar = page.document.querySelector(".topbar");
  page.$("topbarMoreBtn").click();
  assert.equal(bar.classList.contains("menu-open"), true);
  assert.equal(page.$("topbarMoreBtn").getAttribute("aria-expanded"), "true");
  page.$("main").click();
  assert.equal(bar.classList.contains("menu-open"), false, "a tap elsewhere closes it");
  page.$("topbarMoreBtn").click();
  page.$("themeToggleBtn").click();
  await wait(20);
  assert.equal(bar.classList.contains("menu-open"), false, "choosing an entry closes it too");
});

test("a card off screen is not polled, and is brought up to date when its page opens", async (t) => {
  const page = open(t);
  await until(() => page.sent("GET", "/api/status").length);
  await until(() => page.sent("GET", "/api/wifi/ap").length);
  const before = page.sent("GET", "/api/wifi/ap").length;
  page.window.location.hash = "#network/accesspoint";
  await until(() => page.sent("GET", "/api/wifi/ap").length > before);
});

test("a speaker known by its address only is said to need pairing, not just to be off", async (t) => {
  const { STATUS } = require("./harness");
  const page = open(t, { routes: {
    "GET /api/status": Object.assign({}, STATUS, { speaker_mac: "7C:E9:13:69:66:55",
      speaker_connected: false, speaker_paired: false,
      audio_output: { server: true, sink: null, output: "bluetooth" } }),
  } });
  await until(() => /not paired yet/.test(page.$("npNotices").textContent));
  assert.equal(page.$("speakerBadge").textContent, "Not paired");
  assert.doesNotMatch(page.$("npNotices").textContent, /is not connected\./);
});

test("the speaker's battery shows beside it, and a low one is said on the player", async (t) => {
  const { STATUS } = require("./harness");
  const page = open(t, { routes: {
    "GET /api/status": Object.assign({}, STATUS, { speaker_mac: "7C:E9:13:69:66:55",
      speaker_connected: true, speaker_paired: true, speaker_battery: 12, speaker_battery_low: 15 }),
  } });
  await until(() => !page.$("speakerBattery").hidden);
  assert.equal(page.$("speakerBattery").textContent, "Battery 12 %");
  assert.equal(page.$("speakerBattery").dataset.state, "warn");
  await until(() => /battery is low: 12 %/.test(page.$("npNotices").textContent));
});

test("a vote to skip shows the count, and a vote is sent once", async (t) => {
  const { STATUS } = require("./harness");
  let vote = { votes: 1, needed: 3, mine: false };
  const page = open(t, { routes: {
    "GET /api/status": () => Object.assign({}, STATUS, { mode: "music", skip_vote: vote }),
    "POST /api/vote/skip": () => (vote = { votes: 2, needed: 3, mine: true, skipped: false }),
  } });
  await until(() => !page.$("skipVoteRow").hidden);
  assert.equal(page.$("btnSkipVote").textContent, "Vote to skip (1/3)");
  page.$("btnSkipVote").click();
  await until(() => page.sent("POST", "/api/vote/skip").length === 1);
  await until(() => page.$("btnSkipVote").textContent === "You voted (2/3)");
  assert.equal(page.$("btnSkipVote").disabled, true);
});

test("the blind test: the host starts it, a player answers once, and sees the right song after", async (t) => {
  let game = { state: "none", owner: true, round_choices: [5, 10], second_choices: [10, 20] };
  const page = open(t, { hash: "#home/game", routes: {
    "GET /api/game": () => game,
    "POST /api/game/start": () => {
      game = { state: "playing", owner: true, round: 1, rounds: 5, remaining: 18, answered: 0, mine: null,
               choices: ["A - 1", "B - 2", "C - 3", "D - 4"], scores: [] };
      return {};
    },
    "POST /api/game/answer": () => (game = Object.assign({}, game, { mine: 2, answered: 1 })),
  } });
  await until(() => !page.$("gameStartForm").hidden);
  assert.equal(page.$("gameRounds").value, "10");
  page.$("gameStartForm").dispatchEvent(new page.window.Event("submit", { cancelable: true }));
  await until(() => page.$("gameChoices").children.length === 4);
  assert.match(page.$("gameRound").textContent, /Round 1 \/ 5/);
  page.$("gameChoices").children[2].click();
  await until(() => page.sent("POST", "/api/game/answer").length === 1);
  await until(() => page.$("gameChoices").children[2].classList.contains("is-mine"));
  assert.ok([...page.$("gameChoices").children].every((b) => b.disabled), "one answer a round");
  game = Object.assign({}, game, { state: "reveal", answer: 1, gain: 0, fastest: "Fox" });
  await until(() => page.$("gameChoices").children[1].classList.contains("is-right"));
  assert.ok(page.$("gameChoices").children[2].classList.contains("is-wrong"));
  assert.match(page.$("gameResult").textContent, /Wrong this time\. Fastest: Fox\./);
});

test("a page the owner hid from guests gets no tile on the guest's menu", async (t) => {
  const { STATUS } = require("./harness");
  const page = open(t, { hash: "#home", routes: {
    "GET /api/portal/status": { enabled: true, mode: "release", on_ap: false, released: true,
                                guest_mode: true, auth_required: true, authenticated: false },
    "GET /api/status": Object.assign({}, STATUS, { guest_pages_off: ["game"] }),
  } });
  await until(() => page.$("bootOverlay").hidden);
  await until(() => !page.document.querySelector('.page-tile[data-page="game"]')
    || page.document.querySelector('.page-tile[data-page="game"]').hidden);
  const tile = page.document.querySelector('.page-tile[data-page="player"]');
  assert.ok(tile && !tile.hidden, "the player stays");
});
