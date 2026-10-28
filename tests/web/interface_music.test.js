"use strict";
// The interface's own rules on the real page: blind test, Up next, excluded tracks, USB key, lights.

const assert = require("node:assert/strict");
const { test } = require("node:test");
const { load, until, wait, open, deferred, STATUS, GUEST_PATHS, MEMBER_PATHS, OWNER, MARIE } = require("./harness");

test("the blind test: the host starts it, a player answers once, and sees the right song after", async (t) => {
  let game = { state: "none", owner: true, round_choices: [5, 10], second_choices: [10, 20] };
  const page = open(t, { hash: "#home/game", routes: {
    "GET /api/game": () => game,
    "POST /api/game/start": () => {
      game = { state: "playing", owner: true, role: "player", round: 1, rounds: 5, remaining: 18, answered: 0, mine: null,
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
                                guest_mode: true, auth_required: true, authenticated: false,
                                role: "guest", paths: GUEST_PATHS, accounts: [OWNER] },
    "GET /api/status": Object.assign({}, STATUS, { guest_pages_off: ["game"] }),
  } });
  await until(() => page.$("bootOverlay").hidden);
  await until(() => !page.document.querySelector('.page-tile[data-page="game"]')
    || page.document.querySelector('.page-tile[data-page="game"]').hidden);
  const tile = page.document.querySelector('.page-tile[data-page="player"]');
  assert.ok(tile && !tile.hidden, "the player stays");
});

test("a song put up next may carry a message, shown in Up next with a way to remove it", async (t) => {
  const { STATUS } = require("./harness");
  let items = [{ key: "k2", title: "Fly", artist: "Hilary Duff", requested: true,
                 dedication: { from: "Fox", text: "For Marie" } }];
  const page = open(t, { hash: "#home/upnext", routes: {
    "GET /api/status": Object.assign({}, STATUS, { mode: "music", dedications: true }),
    "GET /api/memories": [{ key: "k1", title: "Paradise", artist: "Coldplay", years: 1 }],
    "GET /api/queue": () => ({ enabled: true, items }),
    "POST /api/library/queue": { started: false, position: 1 },
    "POST /api/dedications/delete": () => { items = [Object.assign({}, items[0], { dedication: null })]; return {}; },
  } });
  await until(() => page.document.querySelector(".upnext-dedication"));
  assert.match(page.document.querySelector(".upnext-dedication").textContent, /For Marie.*from Fox/);
  await until(() => page.document.querySelector("#memoriesList .library-next"));
  page.document.querySelector("#memoriesList .library-next").click();
  await until(() => !page.$("modalOverlay").hidden);
  page.document.querySelector("#modalBody textarea").value = "  Happy birthday ";
  page.$("modalChoices").children[0].click();
  await until(() => page.sent("POST", "/api/library/queue").length === 1);
  assert.deepEqual(page.sent("POST", "/api/library/queue")[0].body, { key: "k1", message: "Happy birthday" });
  page.document.querySelector(".upnext-dedication .btn-link").click();
  await until(() => page.sent("POST", "/api/dedications/delete").length === 1);
  await until(() => !page.document.querySelector(".upnext-dedication"));
});

test("voice dedications are offered, and blocked with a reason off HTTPS", async (t) => {
  const { STATUS } = require("./harness");
  const items = [{ key: "k2", title: "Fly", artist: "Hilary Duff", requested: true,
                   dedication: { from: "Fox", text: null, voice: true } }];
  const page = open(t, { hash: "#home/upnext", routes: {
    "GET /api/status": Object.assign({}, STATUS, { mode: "music", dedications: true, dedications_voice: true }),
    "GET /api/memories": [{ key: "k1", title: "Paradise", artist: "Coldplay", years: 1 }],
    "GET /api/queue": () => ({ enabled: true, items }),
    "POST /api/library/queue": { started: false, position: 1 },
  } });
  const line = await until(() => page.document.querySelector(".upnext-dedication"));
  assert.match(line.textContent, /Voice message - from Fox/);
  assert.ok([...line.querySelectorAll(".btn-link")].some((b) => b.textContent === "Listen"), "the owner may listen first");
  assert.equal(page.$("dedicationsVoiceHttps").hidden, false, "this page is not secure, and the setting says so");
  await until(() => page.document.querySelector("#memoriesList .library-next"));
  page.document.querySelector("#memoriesList .library-next").click();
  await until(() => !page.$("modalOverlay").hidden);
  const record = page.document.querySelector("#modalBody .voice-rec .btn");
  assert.ok(record && record.disabled, "no microphone without HTTPS");
  assert.match(page.document.querySelector("#modalBody .voice-rec .hint").textContent, /https/);
  page.document.querySelector("#modalBody textarea").value = "Hi";
  page.$("modalChoices").children[0].click();
  await until(() => page.sent("POST", "/api/library/queue").length === 1);
  assert.deepEqual(page.sent("POST", "/api/library/queue")[0].body, { key: "k1", message: "Hi" });
  assert.equal(page.sent("POST", "/api/dedications/voice").length, 0);
  assert.deepEqual(page.errors, []);
});

test("a reminder is added in minutes or at a time, and listed", async (t) => {
  const items = [];
  const page = open(t, { hash: "#home/reminders", routes: {
    "GET /api/reminders": () => ({ items }),
    "POST /api/reminders": (r) => {
      items.push({ id: "r1", at: Date.now() / 1000 + 600, text: r.body.text });
      return { id: "r1" };
    },
  } });
  await until(() => page.$("reminderWhen").options.length > 5);
  assert.equal(page.$("reminderWhen").value, "10");
  page.$("reminderText").value = "The cake";
  page.$("reminderForm").dispatchEvent(new page.window.Event("submit", { cancelable: true }));
  await until(() => page.sent("POST", "/api/reminders").length === 1);
  assert.deepEqual(page.sent("POST", "/api/reminders")[0].body, { text: "The cake", minutes: 10 });
  await until(() => page.$("reminderList").children.length === 1);
  page.$("reminderWhen").value = "at";
  page.$("reminderWhen").dispatchEvent(new page.window.Event("change"));
  assert.equal(page.$("reminderAtRow").hidden, false);
});

test("the search finds an option by its words and opens its page", async (t) => {
  const page = open(t, { view: "simple" });
  await until(() => page.$("bootOverlay").hidden);
  page.$("searchBtn").click();
  await until(() => page.document.querySelector(".search-box input"));
  const input = page.document.querySelector(".search-box input");
  input.value = "fondu croise";
  input.dispatchEvent(new page.window.Event("input"));
  assert.equal(page.document.querySelectorAll(".search-results button").length, 0, "no French in an English page");
  input.value = "crossfade";
  input.dispatchEvent(new page.window.Event("input"));
  const first = await until(() => page.document.querySelector(".search-results button"));
  assert.match(first.textContent, /Crossfade/);
  first.click();
  await until(() => page.window.location.hash === "#settings/playback");
  await until(() => page.$("crossfadeSec").closest(".field-row").classList.contains("search-hit"));
  page.$("searchBtn").click();
  const again = await until(() => page.document.querySelector(".search-box input"));
  again.value = "action fade";
  again.dispatchEvent(new page.window.Event("input"));
  const hit = await until(() => page.document.querySelector(".search-results button"));
  hit.click();
  await until(() => page.document.documentElement.dataset.view === "detailed");
});

test("the excluded page lists what the radio will not pick, and puts it back", async (t) => {
  let items = [
    { key: "k1", path: "/m/one.mp3", title: "One", artist: "Alpha", excluded_at: 1759500000,
      origin: "duplicate", missing: false },
    { key: "k2", path: "/m/two.mp3", title: "Two", artist: "Beta", excluded_at: 1759500000,
      origin: "filter", missing: false },
  ];
  const page = open(t, { hash: "#home/excluded", routes: {
    "GET /api/excluded": () => ({ items, count: items.length }),
    "POST /api/excluded/restore": (r) => {
      items = items.filter((item) => !r.body.keys.includes(item.key));
      return { count: 1, total: items.length };
    },
  } });
  await until(() => page.$("excludedList").children.length === 2);
  assert.equal(page.$("excludedCard").hidden, false);
  assert.match(page.$("excludedSummary").textContent, /2 tracks/);
  assert.match(page.$("excludedList").textContent, /Alpha/);
  assert.match(page.$("excludedList").textContent, /duplicate/);

  const first = page.$("excludedList").children[0];
  assert.ok(first.querySelector('button[data-icon="list"]'), "a song excluded can still be queued");
  first.querySelector('button[data-icon="refresh"]').click();
  await until(() => page.sent("POST", "/api/excluded/restore").length === 1);
  assert.deepEqual(page.sent("POST", "/api/excluded/restore")[0].body, { keys: ["k1"] });
  await until(() => page.$("excludedList").children.length === 1);
  assert.deepEqual(page.errors, []);
});

test("a long list of excluded tracks comes a page at a time", async (t) => {
  const all = [0, 1, 2].map((i) => ({ key: "k" + i, title: "T" + i, artist: "A",
                                      excluded_at: 1759500000, origin: "manual",
                                      missing: false }));
  const page = open(t, { hash: "#home/excluded", routes: {
    // Two rows a page, whatever the page asks for: the button is what is under test.
    "GET /api/excluded": (r) => {
      const offset = Number(r.query.get("offset") || 0);
      return { items: all.slice(offset, offset + 2), count: all.length };
    },
  } });
  await until(() => page.$("excludedList").children.length === 2);
  assert.equal(page.$("excludedListMore").hidden, false, "there is more to show");
  assert.match(page.$("excludedSummary").textContent, /3 tracks/,
               "the summary counts the whole list, not the page");

  page.$("excludedListMore").click();
  await until(() => page.$("excludedList").children.length === 3);
  assert.equal(page.sent("GET", "/api/excluded")[1].query.get("offset"), "2",
               "the next page starts where the last one stopped");
  assert.equal(page.$("excludedListMore").hidden, true, "nothing left to show");
  assert.deepEqual(page.errors, []);
});

test("a filter is excluded whole, and a row one track at a time", async (t) => {
  const library = [
    { key: "k1", title: "One", artist: "Alpha", excluded: false },
    { key: "k2", title: "Two", artist: "Alpha", excluded: false },
  ];
  const page = open(t, { hash: "#home/excluded", routes: {
    "GET /api/excluded": { items: [], count: 0 },
    "GET /api/library": { items: library, total: 2, status: { total: 2, read: 2 } },
    "GET /api/library/facets": { artists: [{ name: "Alpha", count: 2 }], albums: [], genres: [] },
    "POST /api/excluded": { count: 1, total: 1 },
    "POST /api/excluded/filter": { count: 2, total: 2 },
  } });
  await until(() => page.$("excludedArtist").options.length === 2);
  assert.equal(page.$("excludedFilterAdd").hidden, true, "nothing is asked for yet");

  page.$("excludedSearch").value = "Alpha";
  page.$("excludedSearch").dispatchEvent(new page.window.Event("input"));
  await until(() => page.$("excludedResults").children.length === 2);
  await until(() => !page.$("excludedFilterAdd").hidden);
  assert.match(page.$("excludedFilterAdd").textContent, /2/);

  page.$("excludedResults").children[0].querySelector('button[data-icon="circle-minus"]').click();
  await until(() => page.sent("POST", "/api/excluded").length === 1);
  assert.deepEqual(page.sent("POST", "/api/excluded")[0].body, { keys: ["k1"] });

  page.$("excludedFilterAdd").click();
  await until(() => !page.$("modalOverlay").hidden);
  page.$("modalOk").click();
  await until(() => page.sent("POST", "/api/excluded/filter").length === 1);
  assert.deepEqual(page.sent("POST", "/api/excluded/filter")[0].body, { q: "Alpha" });
  assert.deepEqual(page.errors, []);
});

test("the prepared introductions are browsed, added to and cleaned", async (t) => {
  const listing = (path) => ({
    kind: "intros", root: "/home/pi/audio/dj_announcements", path: path, parent: path ? "" : null,
    missing: false, extensions: [".opus", ".mp3"],
    dirs: path ? [] : [{ name: "LMFAO" }],
    files: path ? [{ name: "_any.opus", size: 2048, used: true },
                   { name: "notes.txt", size: 12, used: false }] : [],
  });
  const page = open(t, { hash: "#settings/djfiles", routes: {
    "GET /api/dj_announcements": { dir: "/home/pi/audio/dj_announcements", files: 46, covered: 312,
                                   tracks: 1175 },
    "GET /api/prepared/intros": (request) => listing(request.query.get("path") || ""),
    "POST /api/prepared/intros/folder": { path: "LMFAO/Sorry" },
    "DELETE /api/prepared/intros/file": {},
  } });
  await until(() => page.$("introsList").children.length);
  assert.equal(page.document.body.dataset.page, "djfiles", "the address opened the card's page");
  assert.match(page.$("introsCount").textContent, /46/);
  assert.equal(page.$("introsUp").disabled, true, "nothing above the prepared root");
  assert.equal(page.$("introsPath").hidden, true,
               "the prepared folder's own name is not a crumb: its title and the guide say it");
  assert.equal(page.$("introsPath").textContent, "");
  assert.equal(page.$("introsHint").children.length, 0, "a folder to browse is not an empty folder");
  const row = page.$("introsList").children[0];
  assert.equal(row.className.includes("is-dir"), true);

  row.querySelector("button").click();
  const asked = await until(() => page.sent("GET", "/api/prepared/intros")
    .find((r) => r.query.get("path") === "LMFAO"));
  assert.ok(asked);
  await until(() => page.$("introsList").children.length === 2);
  const [picture, other] = Array.from(page.$("introsList").children);
  assert.ok(picture.querySelector(".prepared-play"), "a sound is listened to, not looked at");
  assert.equal(other.className.includes("is-unused"), true,
               "a file this folder never uses says so");
  assert.match(other.textContent, /not used/);
  assert.equal(page.$("introsUp").disabled, false, "one level up is offered now");
  assert.match(page.$("introsPath").textContent, /LMFAO/, "the breadcrumb follows");

  page.$("introsNewFolder").click();
  await until(() => !page.$("modalOverlay").hidden);
  page.$("modalBody").querySelector("input").value = "Sorry";
  page.$("modalOk").click();
  const made = await until(() => page.sent("POST", "/api/prepared/intros/folder")[0]);
  assert.deepEqual(made.body, { path: "LMFAO", name: "Sorry" });

  picture.querySelector('button[data-icon="trash"]').click();
  await until(() => !page.$("modalOverlay").hidden);
  page.$("modalOk").click();
  const gone = await until(() => page.sent("DELETE", "/api/prepared/intros/file")[0]);
  assert.equal(gone.query.get("path"), "LMFAO/_any.opus");
  assert.deepEqual(page.errors, []);
});

test("an empty prepared folder says so, the statement on a centred line of its own", async (t) => {
  const page = open(t, { hash: "#settings/djfiles", routes: {
    "GET /api/dj_announcements": { dir: "/home/pi/audio/dj_announcements", files: 0, covered: 0,
                                   tracks: 1175 },
    "GET /api/prepared/intros": { kind: "intros", root: "/home/pi/audio/dj_announcements", path: "",
                                  parent: null, missing: false, extensions: [".wav"],
                                  dirs: [], files: [] },
  } });
  await until(() => page.$("introsHint").children.length);
  const hint = page.$("introsHint");
  assert.equal(hint.children.length, 2, "the statement and the advice, one line each");
  assert.equal(hint.children[0].className, "prepared-empty", "the statement is the centred one");
  assert.match(hint.children[0].textContent, /sound here yet\.$/);
  assert.equal(hint.children[1].className, "prepared-empty-advice");
  assert.match(hint.children[1].textContent, /_any/);
  assert.equal(page.$("introsPath").hidden, true, "and no crumb of the prepared folder's own name");
  assert.deepEqual(page.errors, []);
});

test("a USB key is offered, taken and given back", async (t) => {
  const GiB = 1024 ** 3;
  const idle = { active: false, dir: null, internal: "/home/pi/audio/music", port_mode: "host",
                 device: null, label: null, tracks: 0, bytes: 0, remembered: null, error: null,
                 space: { total: 15 * GiB, used: 9 * GiB, free: 6 * GiB, percent: 60 },
                 devices: [{ key: "uuid:1A2B", device: "/dev/sda1", label: "MUSIQUE",
                             model: "Ultra Fit", fstype: "exfat", size: "29.5G",
                             remembered: false, active: false },
                           { key: "label:DISQUE", device: "/dev/sdb1", label: "",
                             model: "Elements 25A2", fstype: "exfat", size: "1.8T",
                             remembered: false, active: false }] };
  const taken = Object.assign({}, idle, {
    active: true, dir: "/media/rukebox-usb", device: "/dev/sda1", label: "MUSIQUE", tracks: 312,
    space: { total: 30 * GiB, used: 29 * GiB, free: 1 * GiB, percent: 97 },
    // The song playing is still the one from the folder before: the card says
    // so, and offers to change it now.
    playing_from_other: true,
    remembered: { key: "uuid:1A2B", label: "MUSIQUE" },
    devices: [Object.assign({}, idle.devices[0], { active: true })],
  });
  let isTaken = false;
  const page = open(t, { hash: "#system/usb", routes: {
    "GET /api/status": () => Object.assign({}, STATUS, { usb_music: isTaken ? taken : idle }),
    "POST /api/usb_music": () => { isTaken = true; return taken; },
  } });
  await until(() => page.$("bootOverlay").hidden);
  await until(() => page.$("usbList").children.length);
  assert.match(page.$("usbNow").textContent, /home\/pi\/audio\/music/, "the Pi's own folder");
  const row = page.$("usbList").children[0];
  assert.match(row.textContent, /MUSIQUE/);
  assert.match(row.textContent, /exfat/);
  assert.match(page.$("usbList").children[1].textContent, /Elements 25A2/,
               "a device that was never renamed is called by the disk's own name");

  assert.equal(page.$("usbSpace").hidden, false, "the storage that plays is shown full");
  assert.equal(page.$("usbSpaceFill").style.width, "60%");
  assert.match(page.$("usbSpaceText").textContent, /6\.0 GB free/);
  assert.equal(page.$("usbSpaceBar").classList.contains("warn"), false, "room to spare");

  row.querySelector("button").click();
  const asked = await until(() => page.sent("POST", "/api/usb_music")[0]);
  assert.deepEqual(asked.body, { key: "uuid:1A2B" });
  await until(() => /MUSIQUE/.test(page.$("usbNow").textContent));
  assert.match(page.$("usbNow").textContent, /312/, "and how many tracks it holds");
  assert.equal(page.$("usbSpaceFill").style.width, "97%");
  assert.equal(page.$("usbSpaceBar").classList.contains("warn"), true, "nearly full");

  assert.match(page.$("usbHint").textContent, /previous folder/);
  const actions = () => page.$("usbList").children[0].querySelectorAll("button");
  assert.equal(actions().length, 2, "change the song now, or give the folder back");
  assert.equal(actions()[0].textContent, "Play now");

  actions()[0].click();
  const now = await until(() => page.sent("POST", "/api/usb_music")[1]);
  assert.deepEqual(now.body, { key: "uuid:1A2B", switch: "now" },
                   "and it is the same key, taken again with the change asked for");

  actions()[1].click();
  const forgotten = await until(() => page.sent("POST", "/api/usb_music")[2]);
  assert.deepEqual(forgotten.body, { forget: true }, "the way back to the radio's folder");
  assert.deepEqual(page.errors, []);
});

test("the storage card carries the change-on-take setting", async (t) => {
  const page = open(t, { hash: "#system/usb", routes: {
    "GET /api/settings": { "hardware.usb_music_switch": "after" },
    "POST /api/settings": {},
  } });
  await until(() => page.$("bootOverlay").hidden);
  await until(() => page.$("usbSwitch").value === "after");
  page.$("usbSwitch").value = "now";
  page.$("usbSwitch").dispatchEvent(new page.window.Event("change"));
  const saved = await until(() => page.sent("POST", "/api/settings")[0]);
  assert.deepEqual(saved.body, { "hardware.usb_music_switch": "now" },
                   "chosen once, applied to every take after that");
  assert.deepEqual(page.errors, []);
});

test("the library marks an excluded track", async (t) => {
  const page = open(t, { hash: "#home/library", routes: {
    "GET /api/library": { items: [{ key: "k1", title: "One", artist: "Alpha", excluded: true }],
                          total: 1, status: { total: 1, read: 1 } },
  } });
  await until(() => page.$("bootOverlay").hidden);
  page.$("librarySearch").value = "One";
  page.$("librarySearch").dispatchEvent(new page.window.Event("input"));
  await until(() => page.$("libraryList").children.length === 1);
  assert.equal(page.$("libraryList").querySelectorAll(".excluded-badge").length, 1);
  assert.match(page.$("libraryList").querySelector(".excluded-badge").textContent, /excluded/);
  assert.deepEqual(page.errors, []);
});

test("a list, up next and recently played mark an excluded track", async (t) => {
  const page = open(t, { hash: "#home/lists", routes: {
    "GET /api/lists": { lists: [{ id: "soir", name: "Soir", kind: "manual", count: 1 }],
                        active: null, settings: [] },
    "GET /api/lists/soir/tracks": { items: [{ key: "k1", title: "One", path: "/m/one.mp3",
                                             excluded: true }], missing: 0 },
    "GET /api/queue": { enabled: true, items: [{ key: "k1", title: "One", excluded: true }] },
    "GET /api/recent": { enabled: true, items: [
      { key: "k2", title: "Two", at: 1759500000, excluded: true }] },
  } });
  await until(() => page.$("listsList").children.length === 1);
  page.$("listsList").querySelector(".ann-head").click();
  await until(() => page.document.querySelector("#listsDetail .excluded-badge"));
  await until(() => page.$("upnextList").children.length === 1);
  assert.equal(page.$("upnextList").querySelectorAll(".excluded-badge").length, 1);
  await until(() => page.$("recentList").children.length === 1);
  assert.equal(page.$("recentList").querySelectorAll(".excluded-badge").length, 1);
  assert.deepEqual(page.errors, []);
});

test("the light strips page offers the strip's presets and tries one", async (t) => {
  const page = open(t, { hash: "#settings/lights", routes: {
    "GET /api/wled": { enabled: true, devices: [
      { host: "192.168.4.20", name: "Salon", leds: 60, ver: "0.15.0", clock_known: true },
      { host: "192.168.4.21", missing: true }],
      presets: [{ id: 1, name: "Calme" }, { id: 2, name: "Fête" }], lights: { scene: "play" } },
    "POST /api/wled/test": { failed: [] },
  } });
  const select = await until(() => {
    const s = page.$("wledScenePlay");
    return s && s.options.length === 4 && s;
  });
  assert.deepEqual([...select.options].map((o) => o.value), ["0", "-1", "1", "2"]);
  assert.equal(page.$("wledDevices").children.length, 2);
  assert.ok(page.$("wledDevices").children[1].classList.contains("is-off"), "a strip that does not answer says so");
  assert.notEqual(page.$("wledClockState").textContent, "");
  select.value = "2";
  page.document.querySelector('.wled-test[data-for="wledScenePlay"]').click();
  const sent = await until(() => page.sent("POST", "/api/wled/test")[0]);
  assert.deepEqual(sent.body, { preset: 2 });
  assert.deepEqual(page.errors, []);
});

test("the buttons page lists the Bluetooth remotes and removes one", async (t) => {
  const page = open(t, { hash: "#settings/buttons", routes: {
    "GET /api/bluetooth/remotes": { remotes: [
      { mac: "2A:07:98:10:34:FF", name: "AB Shutter3", paired: true, connected: false }] },
    "POST /api/bluetooth/remote": { remotes: [] },
  } });
  const row = await until(() => page.$("remoteList").querySelector("li"));
  assert.match(row.textContent, /AB Shutter3/);
  assert.ok(page.$("remoteEmpty").hidden, "no empty line beside a remote");
  row.querySelector("button").click();
  const sent = await until(() => page.sent("POST", "/api/bluetooth/remote")[0]);
  assert.deepEqual(sent.body, { mac: "2A:07:98:10:34:FF", on: false });
  assert.deepEqual(page.errors, []);
});

test("the Bluetooth list offers each device only the roles it can take", async (t) => {
  const devices = [
    { mac: "7C:E9:13:69:66:55", name: "Speaker", paired: true, connected: true, kind: "audio", named: true },
    { mac: "2A:07:98:10:34:FF", name: "Shutter", paired: true, connected: false, kind: "input", named: true },
    { mac: "11:22:33:44:55:66", name: "Phone", paired: false, connected: false, kind: "other", named: true },
    { mac: "AA:BB:CC:DD:EE:01", name: "Mystery", paired: false, connected: false, kind: "unknown", named: true },
  ];
  const page = open(t, { hash: "#audio/bluetooth", routes: {
    "POST /api/bluetooth/scan/start": {},
    "GET /api/bluetooth/scan/status": { running: false, devices },
    "GET /api/bluetooth/remotes": { remotes: [] },
  } });
  await until(() => page.document.body.dataset.page === "bluetooth");
  page.$("btnBtScan").click();
  await until(() => page.$("deviceList").querySelectorAll("li").length === 4);
  const roles = {};
  for (const li of page.$("deviceList").querySelectorAll("li")) {
    const name = li.querySelector(".device-name").textContent;
    roles[name] = [...li.querySelectorAll("button")].map((b) => b.textContent);
  }
  const has = (name, label) => roles[name].some((text) => text === label);
  const speaker = "Use as speaker", clock = "Use for clock", remote = "Use as a remote";
  assert.ok(has("Speaker", speaker) && !has("Speaker", remote));
  assert.ok(has("Shutter", remote) && !has("Shutter", speaker) && !has("Shutter", clock));
  assert.ok(has("Phone", clock) && !has("Phone", remote));
  assert.ok(has("Mystery", remote) && has("Mystery", speaker));
  assert.deepEqual(page.errors, []);
});

test("a guest can close the login dialog, the login at the door cannot be closed", async (t) => {
  const page = open(t, { routes: {
    "GET /api/portal/status": { enabled: true, mode: "release", on_ap: false, released: true,
                                guest_mode: true, auth_required: true, authenticated: false,
                                role: "guest", paths: GUEST_PATHS, accounts: [OWNER] },
    "POST /api/auth/login": { __raw: { ok: false, error: "wrong_password" } },
  } });
  await until(() => page.$("bootOverlay").hidden);
  page.$("guestLoginBtn").click();
  await until(() => !page.$("loginOverlay").hidden);
  assert.equal(page.$("loginClose").hidden, false, "opened by the guest: it can be closed");
  page.$("loginPassword").value = "nope";
  page.$("loginForm").dispatchEvent(new page.window.Event("submit", { cancelable: true, bubbles: true }));
  await until(() => page.sent("POST", "/api/auth/login").length === 1);
  await until(() => !page.$("loginError").hidden);
  assert.equal(page.$("loginClose").hidden, false, "a wrong password keeps the way out");
  page.$("loginClose").click();
  assert.equal(page.$("loginOverlay").hidden, true);
  assert.deepEqual(page.errors, []);
});

/* Stopping the music (web/app.js): the timer's own heads rather than a pair of
   framed buttons, and the legend under them saying what each one leaves behind. */
test("stopping the music offers the timer's own heads", async (t) => {
  const page = open(t, { routes: {
    "GET /api/status": STATUS,
    "POST /api/action/standby": () => ({ ok: true }),
  } });
  await until(() => page.$("bootOverlay").hidden);
  page.$("btnLongPress").click();
  await until(() => !page.$("modalOverlay").hidden);

  const heads = [...page.document.querySelectorAll(".choice-btn")];
  assert.deepEqual(heads.map((b) => b.querySelector(".timer-symbol").dataset.icon),
                   ["moon", "power"], "standby wears the timer's moon, off its power");
  assert.deepEqual(heads.map((b) => b.querySelector(".timer-group-title").textContent.trim()),
                   ["Standby", "Switch off"], "the name of each under its icon");
  assert.equal(page.document.querySelectorAll("#modalLegend .timer-legend-line").length, 2,
               "and the legend explains both");
  t.diagnostic("the buttons say: " + heads.map((b) => b.textContent.trim()).join(", "));

  heads[0].click();
  await until(() => page.sent("POST", "/api/action/standby").length === 1);
  assert.equal(page.$("modalOverlay").hidden, true, "the dialog is gone");
  assert.deepEqual(page.errors, []);
});
