"use strict";
// The interface's own rules on the real page: schedules, the portal, menus, locked commands, speaker.

const assert = require("node:assert/strict");
const { test } = require("node:test");
const { load, until, wait, open, deferred, STATUS, GUEST_PATHS, MEMBER_PATHS, OWNER, MARIE } = require("./harness");

test("schedules can be reordered and copied, and the week shows them", async (t) => {
  const schedules = [
    { id: "matin", name: "Le matin", enabled: true, date: null, days: [], start: "07:00", stop: "09:00",
      stop_action: "pause", list: null, announcement: "reveil", settings: { "playback.base_volume": "30" } },
    { id: "nuit", name: "La nuit", enabled: true, date: null, days: [], start: "22:00", stop: "01:30",
      stop_action: "standby", list: null, announcement: null, settings: {} },
    { id: "off", name: "Coupee", enabled: false, date: null, days: [], start: "12:00", stop: "13:00",
      stop_action: "pause", list: null, announcement: null, settings: {} },
  ];
  const page = open(t, { hash: "#settings/settings", routes: {
    "GET /api/settings": { "schedule.cutoff_enabled": "true", "schedule.cutoff_hour": "23", "schedule.cutoff_minute": "30" },
    "GET /api/announcements": [{ id: "reveil", name: "Reveil", enabled: true, trigger: "manual", hour: 7,
                                 minute: 0, file_count: 2, folder: "/home/pi/audio/reveil" }],
    "GET /api/schedules": { schedules, settings: ["playback.base_volume"] },
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
  assert.deepEqual(copy.settings, { "playback.base_volume": "30" });
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

test("Android is given a document it can scroll up, so its portal window lets go", async (t) => {
  const page = open(t, { expose: ["refusePullToRefresh"] });
  await until(() => page.$("bootOverlay").hidden);
  const calls = [];
  page.window.scrollTo = (x, y) => calls.push([x, y]);
  const refuse = page.window.__exposed.refusePullToRefresh;
  const webview = "Mozilla/5.0 (Linux; Android 16; wv) AppleWebKit/537.36 Chrome/140.0 Mobile Safari/537.36";
  assert.equal(refuse(webview), true, "the portal window is a WebView");
  assert.deepEqual(calls, [[0, 1]], "the document goes and stays one pixel down");
  assert.ok(page.document.documentElement.classList.contains("pull-guard"), "and the rule that allows it");
  calls.length = 0;
  page.window.dispatchEvent(new page.window.Event("scroll"));
  assert.deepEqual(calls, [[0, 1]], "a browser that put it back is pinned again");

  function gesture(target, moves) {
    const start = new page.window.Event("touchstart", { bubbles: true });
    start.touches = [{ clientY: 100 }];
    target.dispatchEvent(start);
    const prevented = moves.map((y) => {
      const move = new page.window.Event("touchmove", { bubbles: true, cancelable: true });
      move.touches = [{ clientY: y }];
      target.dispatchEvent(move);
      return move.defaultPrevented;
    });
    const end = new page.window.Event("touchend", { bubbles: true });
    end.changedTouches = [{ clientY: moves.length ? moves[moves.length - 1] : 100 }];
    target.dispatchEvent(end);
    return prevented;
  }

  assert.deepEqual(gesture(page.document.querySelector(".topbar"), [80, 60]), [true, true],
                   "a drag on the title bar has nothing to scroll, so the page keeps it");
  assert.deepEqual(gesture(page.$("loginPassword"), [80]), [false], "a field keeps its own drags");

  const main = page.$("main");
  main.style.overflowY = "auto";
  Object.defineProperty(main, "scrollHeight", { value: 2000, configurable: true });
  Object.defineProperty(main, "clientHeight", { value: 600, configurable: true });
  const child = page.document.createElement("div");
  main.appendChild(child);
  assert.deepEqual(gesture(child, [80, 120]), [false, false], "the one scroller keeps its own too");

  assert.equal(refuse("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15"), false,
               "an iPhone keeps its own");
  assert.equal(refuse("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0"), false,
               "a desktop browser too");
});

test("the title bar gives the pull back, and only there", async (t) => {
  const page = open(t, { expose: ["refusePullToRefresh"] });
  await until(() => page.$("bootOverlay").hidden);
  page.window.__exposed.refusePullToRefresh(
    "Mozilla/5.0 (Linux; Android 16; wv) AppleWebKit/537.36 Chrome/140.0 Mobile Safari/537.36");
  const reloads = () => page.navigations.length;
  const pill = page.$("pullRefresh");

  function pull(target, moves) {
    const start = new page.window.Event("touchstart", { bubbles: true });
    start.touches = [{ clientY: 100 }];
    target.dispatchEvent(start);
    moves.forEach((y) => {
      const move = new page.window.Event("touchmove", { bubbles: true, cancelable: true });
      move.touches = [{ clientY: y }];
      target.dispatchEvent(move);
    });
  }
  function release(target, y) {
    const end = new page.window.Event("touchend", { bubbles: true });
    end.changedTouches = [{ clientY: y }];
    target.dispatchEvent(end);
  }

  const bar = page.document.querySelector(".topbar");
  pull(bar, [130]);
  assert.equal(pill.hidden, false, "a short pull still shows the pill");
  assert.equal(pill.classList.contains("ready"), false, "not yet ready");
  release(bar, 130);
  assert.equal(pill.hidden, true, "released short, the pill goes away");
  assert.equal(reloads(), 0, "and nothing reloads");

  pull(bar, [180]);
  assert.equal(pill.classList.contains("ready"), true, "pulled past the threshold, the pill is ready");
  assert.equal(pill.style.getPropertyValue("--pull"), "1.00", "and filled");
  release(bar, 180);
  assert.equal(reloads(), 1, "released there, the page reloads");
  assert.ok(pill.classList.contains("spinning"), "with the pill turning");

  pull(bar, [130]);
  release(bar, 130);
  assert.equal(reloads(), 1, "a short pull after that reloads nothing");
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
                                guest_mode: true, auth_required: true, authenticated: false,
                                role: "guest", paths: GUEST_PATHS, accounts: [OWNER] },
    "GET /api/status": Object.assign({}, STATUS, { guest_locked: ["next", "volume"] }),
  } });
  await until(() => page.$("btnNext").hasAttribute("data-locked"));
  assert.equal(page.$("btnNext").getAttribute("aria-disabled"), "true");
  assert.equal(page.document.querySelector(".volume-row").hasAttribute("data-locked"), true);
  assert.equal(page.$("btnPrev").hasAttribute("data-locked"), false);
});

test("the owner picks the locked commands in a list, and may lock none", async (t) => {
  const page = open(t, { hash: "#network/guest", routes: {
    "GET /api/settings": { "security.guest_locked": "volume" },
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
  assert.equal(page.$("speakerBattery").textContent, "12 %");
  assert.equal(page.$("speakerBattery").getAttribute("aria-label"), "Battery 12 %");
  assert.equal(page.$("speakerBattery").dataset.state, "warn");
  assert.equal(page.$("speakerBattery").style.getPropertyValue("--battery-level"), "12%");
  assert.equal(page.$("npBattery").hidden, false, "shown on the player too");
  await until(() => /battery is low: 12 %/.test(page.$("npNotices").textContent));
});

test("a vote to skip shows the count, and a vote is sent once", async (t) => {
  const { STATUS } = require("./harness");
  let vote = { votes: 1, needed: 3, mine: false };
  const page = open(t, { routes: {
    "GET /api/status": () => Object.assign({}, STATUS, { mode: "music", skip_vote: vote }),
    "POST /api/vote/skip": () => (vote = { votes: 2, needed: 3, mine: true, skipped: false }),
  } });
  await until(() => !page.$("btnSkipVote").hidden);
  assert.equal(page.$("playerSmallRow").hidden, false, "the row is there for it");
  assert.equal(page.$("btnSkipVote").textContent, "Vote to skip (1/3)");
  page.$("btnSkipVote").click();
  await until(() => page.sent("POST", "/api/vote/skip").length === 1);
  await until(() => page.$("btnSkipVote").textContent === "You voted (2/3)");
  assert.equal(page.$("btnSkipVote").disabled, true);
});

test("the two small buttons share one row, and the row goes when they do", async (t) => {
  const { STATUS } = require("./harness");
  const stream = { enabled: true, available: true, url: "http://radio/stream.opus",
                   encoder: "opus", content_type: "audio/ogg", listeners: 0, why: "" };
  const page = open(t, { routes: { "GET /api/status": Object.assign({}, STATUS, {
    stream, skip_vote: { votes: 1, needed: 3, mine: false } }) } });
  await until(() => !page.$("btnListen").hidden && !page.$("btnSkipVote").hidden);
  const row = page.$("playerSmallRow");
  assert.equal(row.hidden, false);
  assert.ok(row.contains(page.$("btnListen")) && row.contains(page.$("btnSkipVote")),
            "one line for the two of them");

  const bare = open(t, { routes: { "GET /api/status": Object.assign({}, STATUS, {
    stream: Object.assign({}, stream, { available: false, url: "" }) }) } });
  await until(() => bare.$("playerSmallRow").hidden === true);
  assert.equal(bare.$("btnListen").hidden, true, "nothing to listen to");
  assert.equal(bare.$("btnSkipVote").hidden, true, "and nothing to vote about");
});
