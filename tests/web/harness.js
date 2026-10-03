"use strict";
// Loads the real index.html, i18n.js, qr.js and app.js into jsdom, with the
// API answered by a table of fakes. No stylesheet: layout is not jsdom's job.

const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const WEB = path.join(__dirname, "..", "..", "web");
const read = (name) => fs.readFileSync(path.join(WEB, name), "utf8");

const STATUS = {
  mode: "music", volume: 70, paused: false, muted: false, position: 12, duration: 200,
  current_track: "Song.opus", track_title: "Song", track_artist: "Artist", track_key: "k1",
  speaker_connected: true, system_time: "2026-10-02 10:00:00 CEST", epoch: 1790928000,
  timezone: "Europe/Paris", clock_established: true, loop_mode: "off", timers: {},
  audio_output: { server: true, sink: "Speaker", output: "bluetooth", missing: false },
};

const DEFAULTS = {
  "GET /api/portal/status": { enabled: true, mode: "release", on_ap: false, released: true,
                              guest_mode: false, auth_required: false, authenticated: false },
  "GET /api/auth/status": { auth_required: false, authenticated: false },
  "GET /api/status": STATUS,
  "GET /api/settings": {},
  "GET /api/announcements": [],
  "GET /api/wifi/status": { configured: true, conn_name: "home", active: true, ip_address: "192.168.1.5/24",
                            autoconnect: false, client_here: false },
};

function load(options = {}) {
  const routes = Object.assign({}, DEFAULTS, options.routes || {});
  const requests = [];
  let closed = false;
  const html = read("index.html").replace(/<script src="[^"]+"><\/script>/g, "");
  const dom = new JSDOM(html, {
    url: "http://rukebox.local/" + (options.hash || ""),
    runScripts: "outside-only",
    pretendToBeVisual: true,
  });
  const { window } = dom;

  // options.media(query) answers a media query; every query is false otherwise (a phone).
  window.matchMedia = (query) => ({ matches: !!(options.media && options.media(query)),
                                    addEventListener() {}, removeEventListener() {},
                               addListener() {}, removeListener() {} });
  class Observer { observe() {} unobserve() {} disconnect() {} }
  window.ResizeObserver = window.ResizeObserver || Observer;
  window.IntersectionObserver = window.IntersectionObserver || Observer;
  window.CSS = window.CSS || {};
  window.CSS.escape = window.CSS.escape || ((value) => String(value).replace(/[^a-zA-Z0-9_-]/g, "\\$&"));
  window.Element.prototype.scrollIntoView = function () {};
  window.Element.prototype.scrollTo = function () {};
  window.scrollTo = () => {};
  window.HTMLMediaElement.prototype.play = () => Promise.resolve();
  window.HTMLMediaElement.prototype.pause = () => {};
  window.HTMLMediaElement.prototype.load = () => {};

  window.fetch = (url, init = {}) => {
    const method = (init.method || "GET").toUpperCase();
    const pathname = new window.URL(url, window.location.href).pathname;
    let body = null;
    if (typeof init.body === "string") {
      try { body = JSON.parse(init.body); } catch (e) { body = init.body; }
    }
    const request = { method, path: pathname, body };
    if (closed || pathname === "/api/status/wait") return new Promise(() => {});
    requests.push(request);
    let answer = routes[method + " " + pathname];
    if (typeof answer === "function") answer = answer(request);
    return Promise.resolve(answer).then((data) => {
      const payload = data === undefined
        ? { ok: false, error: "not_found" }
        : (data && data.__raw) ? data.__raw : { ok: true, data };
      const status = payload.ok ? 200 : 404;
      return { ok: payload.ok, status, json: async () => payload, text: async () => JSON.stringify(payload) };
    });
  };

  const errors = [];
  window.addEventListener("error", (event) => errors.push(event.error || event.message));
  window.addEventListener("unhandledrejection", (event) => errors.push(event.reason));

  // One evaluation: separate ones would not share their top-level const/let.
  // `expose` names top-level functions of the page to hand to a test: the evaluation keeps them to itself.
  Object.entries(options.storage || {}).forEach(([key, value]) => window.localStorage.setItem(key, value));
  const exposed = options.expose ? "\n;window.__exposed = { " + options.expose.join(", ") + " };" : "";
  window.eval(["i18n.js", "qr.js", "app.js"].map(read).join("\n;\n") + exposed);

  return {
    window,
    document: window.document,
    requests,
    errors,
    $: (id) => window.document.getElementById(id),
    sent: (method, pathname) => requests.filter((r) => r.method === method && r.path === pathname),
    // Nothing new is answered, what is under way settles, then the window goes:
    // closing at once leaves the page's own code running against no document.
    close: async () => {
      closed = true;
      await wait(120);
      window.close();
    },
  };
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// Until `check` answers something truthy, or the time is up.
async function until(check, ms = 3000) {
  const end = Date.now() + ms;
  for (;;) {
    const value = check();
    if (value) return value;
    if (Date.now() > end) throw new Error("timed out waiting for: " + check);
    await wait(20);
  }
}

module.exports = { load, until, wait, STATUS };
