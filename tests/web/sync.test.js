"use strict";
// The sync page (web/js/init/27-sync.js): syncs and connections, the "sync here" line of
// Add music, the server key the owner accepts, and the toasts of a run.
const test = require("node:test");
const assert = require("node:assert/strict");

const { until, open } = require("./harness");

const PLACES = [{ kind: "music", id: "music", target: "/srv/music" },
                { kind: "memes", id: "memes", target: "/srv/memes" }];

function view(status) {
  return {
    rclone: true, places: PLACES, lost: [],
    connections: [{ id: "nas", name: "NAS", type: "sftp", host: "nas.local", user: "pi", path: "Music",
                    auth: "password", tls: "verify", port: 22, host_key: "", secret_set: true, secret_lost: false }],
    jobs: [{ id: "rock", name: "Rock", connection: "nas", remote: "Rock", kind: "music", target: "/srv/music/Rock",
             every_hours: 24, enabled: true, extensions: ["mp3"], max_size_mb: 500, window_start: "", window_end: "",
             at_boot: true, internet: "any", wifi: "any", ssid: "", playback: "idle", recent_list: true }],
    status: status || { running: null, seq: 0, held: {}, runs: [] },
  };
}

test("syncs and connections are listed, and a folder of the radio offers to fill itself", async (t) => {
  const page = open(t, { hash: "#home/music", view: "detailed", routes: {
    "GET /api/sync": view(), "GET /api/sync/status": view().status,
    "POST /api/sync/jobs": (request) => Object.assign(view(), { jobs: view().jobs.concat([Object.assign(
      {}, view().jobs[0], { id: "x", name: request.body.name })]) }),
  } });
  await until(() => !page.document.querySelector('.sync-here[data-sync-place="music"]').hidden);
  const here = page.document.querySelector('.sync-here[data-sync-place="music"]');
  assert.match(here.textContent, /1 sync\(s\) fill this folder/);
  assert.match(page.$("syncJobList").textContent, /Rock/);
  assert.match(page.$("syncJobList").textContent, /Music · Every day/);
  assert.match(page.$("syncConnList").textContent, /Server to check/, "an SFTP server not checked yet says so");

  here.querySelector("button").click();
  await until(() => !page.$("syncJobForm").hidden);
  assert.equal(page.$("sjPlace").value, "music");
  assert.equal(page.$("sjTarget").value, "/srv/music");
  assert.equal(page.$("sjRecentRow").hidden, false, "the recent list is offered for music");
  page.$("sjPlace").value = "memes";
  page.$("sjPlace").dispatchEvent(new page.window.Event("change"));
  assert.equal(page.$("sjTarget").value, "/srv/memes");
  assert.equal(page.$("sjRecentRow").hidden, true);
  page.$("sjName").value = "Jingles";
  page.$("sjRemote").value = "Jingles";
  page.$("syncJobForm").dispatchEvent(new page.window.Event("submit", { cancelable: true }));
  await until(() => page.sent("POST", "/api/sync/jobs").length);
  const body = page.sent("POST", "/api/sync/jobs")[0].body;
  assert.equal(body.kind, "memes");
  assert.equal(body.target, "/srv/memes");
  assert.equal(body.connection, "nas");
  assert.equal(body.every_hours, 24);
});

test("a server key is accepted only as it was shown", async (t) => {
  const posted = [];
  const page = open(t, { hash: "#home/sync", view: "detailed", routes: {
    "GET /api/sync": view(), "GET /api/sync/status": view().status,
    "POST /api/sync/connections/nas/host_key": (request) => {
      posted.push(request.body);
      return request.body.fingerprints ? view() : { fingerprints: ["ED25519 SHA256:abc", "RSA SHA256:def"] };
    },
  } });
  await until(() => page.$("syncConnList").children.length === 1);
  page.$("syncConnList").querySelector('[data-icon="key"]').click();
  await until(() => !page.$("modalOverlay").hidden);
  assert.match(page.$("modalBody").textContent, /ED25519 SHA256:abc/);
  page.$("modalChoices").querySelector("button").click();
  await until(() => posted.length === 2);
  assert.deepEqual(posted[1], { fingerprints: ["ED25519 SHA256:abc", "RSA SHA256:def"] });
});

test("a run that starts and ends is told in a toast", async (t) => {
  let status = view().status;
  const page = open(t, { hash: "#home/sync", routes: {
    "GET /api/sync": () => view(status), "GET /api/sync/status": () => status,
  } });
  await until(() => page.sent("GET", "/api/sync").length);
  const toasts = () => [...page.document.querySelectorAll(".toast")].map((toast) => toast.textContent).join(" | ");
  status = { running: { job: "rock", progress: { bytes: 5, total_bytes: 10, done: 1, total: 2 } }, seq: 1, held: {}, runs: [] };
  page.document.dispatchEvent(new page.window.CustomEvent("page-shown", { detail: { tab: "home", page: "sync" } }));
  await until(() => /Sync started: Rock/.test(toasts()));
  await until(() => !page.$("syncNow").hidden);
  assert.match(page.$("syncNowLine").textContent, /1 of 2 files, 50 %/);
  status = { running: null, seq: 2, held: {}, runs: [{ job: "rock", status: "ok", added: 3, updated: 1, ended: 1 }] };
  page.document.dispatchEvent(new page.window.CustomEvent("page-shown", { detail: { tab: "home", page: "sync" } }));
  await until(() => /Rock: 3 new, 1 updated/.test(toasts()));
});
