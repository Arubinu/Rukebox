"use strict";
// The server setup page (bootstrap/server/): what it writes for Docker, a Portainer or Dockge stack,
// and a Proxmox container, and the password hash the radio must be able to check.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const nodeCrypto = require("node:crypto");
const { JSDOM } = require("jsdom");

const ROOT = path.join(__dirname, "..", "..");
const read = (...parts) => fs.readFileSync(path.join(ROOT, ...parts), "utf8");

function load(release = "v1.16.0") {
  const html = read("bootstrap", "server", "server.html").replace(/<script>[\s\S]*?<\/script>/g, "");
  const dom = new JSDOM(html, { url: "http://localhost/", runScripts: "outside-only" });
  const { window } = dom;
  window.scrollTo = () => {};
  Object.defineProperty(window, "crypto", { value: nodeCrypto.webcrypto });
  window.TextEncoder = TextEncoder;
  window.localStorage.setItem("rukebox_lang", "en");
  window.RUKEBOX_PAYLOAD = { release };
  window.eval(read("web", "i18n.js") + "\n;\n" + read("bootstrap", "server", "server.js") +
    "\n;window.__I18N = I18N;");
  const $ = (id) => window.document.getElementById(id);
  const pick = (name, value) => { window.document.querySelector(`input[name="${name}"][value="${value}"]`).click(); };
  return { window, $, pick, api: window.__rukeboxServer };
}

const env = (text, key) => {
  const line = text.split("\n").find((l) => l.trim().startsWith(key + ":"));
  return line && JSON.parse(line.slice(line.indexOf(":") + 1).trim());
};
const setupOf = (b64) => JSON.parse(Buffer.from(b64, "base64").toString("utf8"));

test("a Docker compose pins the release and hands the answers, the hash among them", async () => {
  const p = load();
  p.$("wzName").value = "Kitchen radio";
  p.$("wzPw").value = p.$("wzPw2").value = "correct horse";
  const hash = await p.api.passwordHash();
  const text = p.api.compose(hash);
  assert.match(text, /^name: rukebox$/m);
  assert.match(text, /image: ghcr\.io\/arubinu\/rukebox:1\.16\.0$/m);
  assert.match(text, /- "8080:80"/);
  assert.match(text, /- "\.\/config:\/config"/);
  assert.match(text, /- "\.\/music:\/music:ro"/);
  assert.doesNotMatch(text, /network_mode|devices|1704/);
  // The hash travels inside the base64: no "$" for compose or a .env file to read as a variable.
  assert.doesNotMatch(text, /\$/);
  const setup = setupOf(env(text, "RUKEBOX_SETUP"));
  assert.deepEqual(setup.settings, { UPNP_NAME: "Kitchen radio", SPEECH_LANGUAGE: "en" });
  assert.equal(setup.web_password_hash, hash);
});

test("variables apart: the compose reads them, the .env holds them, with nothing to escape", async () => {
  const p = load();
  p.$("wzTz").value = "Europe/Paris";
  p.$("wzPw").value = "correct horse";
  const hash = await p.api.passwordHash();
  const text = p.api.compose(hash, true);
  assert.match(text, /^ {6}TZ: \$\{TZ\}\n {6}RUKEBOX_SETUP: \$\{RUKEBOX_SETUP\}$/m);
  const lines = p.api.envFile(hash).trim().split("\n");
  assert.equal(lines[0], "TZ=Europe/Paris");
  assert.match(lines[1], /^RUKEBOX_SETUP=[A-Za-z0-9+/=]+$/);
  assert.equal(setupOf(lines[1].slice("RUKEBOX_SETUP=".length)).web_password_hash, hash);

  p.pick("target", "stack");
  p.$("wzEnvFile").checked = true;
  await p.api.paintResult();
  const steps = [...p.$("wzSteps").children];
  assert.equal(steps.length, 4, "the stack, its variables, deploy, open");
  assert.match(steps[0].querySelector("ul").textContent, /Portainer.*Dockge/s);
  assert.equal(steps[1].querySelector("strong").textContent, ".env");
  assert.equal(steps[1].querySelectorAll("button").length, 1, "a .env is copied, not downloaded");
  assert.equal(steps[0].querySelectorAll("button").length, 2);
});

test("the hash is the one the radio checks: PBKDF2-SHA256, 260000 rounds, a 16-byte salt", async () => {
  const p = load();
  p.$("wzPw").value = "correct horse";
  const hash = await p.api.passwordHash();
  const [algorithm, rounds, salt, digest] = hash.split("$");
  assert.equal(algorithm, "pbkdf2_sha256");
  assert.equal(rounds, "260000");
  assert.equal(salt.length, 32);
  const expected = nodeCrypto.pbkdf2Sync("correct horse", Buffer.from(salt, "hex"), 260000, 32, "sha256").toString("hex");
  assert.equal(digest, expected);
  assert.equal(await p.api.passwordHash(), hash, "the same password keeps its salt");
  p.$("wzPw").value = "";
  assert.equal(await p.api.passwordHash(), "");
});

test("each sound gives its own compose: card, host PipeWire, Bluetooth, multiroom", () => {
  const p = load();
  p.pick("sound", "card");
  p.$("wzCard").value = "usb";
  let text = p.api.compose("");
  assert.match(text, /devices:\n {6}- \/dev\/snd:\/dev\/snd\n {4}group_add:\n {6}- "29"/);
  assert.equal(setupOf(env(text, "RUKEBOX_SETUP")).settings.AUDIO_OUTPUT, "usb");
  assert.equal(setupOf(env(text, "RUKEBOX_SETUP")).web_password_hash, undefined);

  p.pick("sound", "pipewire");
  p.$("wzUid").value = "1001";
  text = p.api.compose("");
  assert.match(text, /"\/run\/user\/1001\/pipewire-0:\/run\/rukebox\/pipewire-0"/);
  assert.match(text, /PULSE_SERVER: unix:\/run\/rukebox\/pulse\/native/);
  assert.equal(setupOf(env(text, "RUKEBOX_SETUP")).settings.AUDIO_OUTPUT, undefined);

  p.pick("sound", "bluetooth");
  text = p.api.compose("");
  assert.match(text, /network_mode: host/);
  assert.doesNotMatch(text, /ports:/);
  assert.match(text, /- \/run\/dbus:\/run\/dbus/);
  assert.deepEqual(setupOf(env(text, "RUKEBOX_SETUP")).settings,
    { UPNP_NAME: "Rukebox", SPEECH_LANGUAGE: "en", AUDIO_OUTPUT: "bluetooth", WEB_PORT: "8080" });

  p.pick("sound", "snapcast");
  text = p.api.compose("");
  assert.match(text, /- "1704:1704"\n {6}- "1780:1780"/);
});

test("a stack takes full paths, and its folders change only while they hold the defaults", () => {
  const p = load();
  p.pick("target", "stack");
  assert.equal(p.$("wzHome").value, "/opt/rukebox");
  p.$("wzMusic").value = "/srv/My Music";
  p.$("wzWritable").checked = true;
  const text = p.api.compose("");
  assert.doesNotMatch(text, /^name:/m);
  assert.match(text, /- "\/opt\/rukebox\/config:\/config"/);
  assert.match(text, /- "\/srv\/My Music:\/music"$/m);
  p.pick("target", "docker");
  assert.equal(p.$("wzHome").value, "./");
  assert.equal(p.$("wzMusic").value, "/srv/My Music");
  p.pick("target", "stack");
  p.$("wzHome").value = "data";
  p.api.show(3);
  p.$("wzNext").click();
  assert.equal(p.$("wzError").hidden, false);
});

test("a Proxmox container gets one command, from the release tag, with the sound cards when asked", async () => {
  const p = load();
  p.pick("target", "lxc");
  assert.equal(p.$("wzSoundBluetooth").hidden, true);
  p.pick("sound", "card");
  p.$("wzPiper").checked = false;
  p.$("wzTz").value = "Europe/Paris";
  p.$("wzPw").value = "it's a secret";
  const hash = await p.api.passwordHash();
  const command = p.api.lxcCommand(hash);
  assert.match(command, /git clone --depth 1 --branch v1\.16\.0 https:\/\/github\.com\/Arubinu\/Rukebox\.git/);
  assert.match(command, /RUKEBOX_PROFILE=lxc .*RUKEBOX_PIPER=no RUKEBOX_TIMEZONE='Europe\/Paris' RUKEBOX_WEB_PASSWORD= /);
  const setup = command.match(/RUKEBOX_SETUP='([^']+)'/)[1];
  assert.equal(setupOf(setup).web_password_hash, hash);
  assert.match(command, /systemctl enable --now rukebox-daemon\.service rukebox-web\.service\n$/);
  await p.api.paintResult();
  assert.equal(p.$("wzEnvRow").hidden, true);
  const steps = [...p.$("wzSteps").children];
  assert.equal(steps.length, 6);
  assert.match(steps[1].textContent, /\/etc\/pve\/lxc\/120\.conf/);
  assert.match(steps[2].textContent, /Console/);
  assert.doesNotMatch(steps[2].textContent, /pct/);
  assert.equal(p.$("wzPctRow").hidden, false);
  p.$("wzPct").checked = true;
  await p.api.paintResult();
  assert.match(p.$("wzSteps").children[2].textContent, /pct start 120 && pct enter 120/);
  assert.equal(steps[3].querySelectorAll("button").length, 1, "a command is copied, not downloaded");
});

test("a page built from no release fetches the latest image and the main branch", () => {
  const p = load("v1.16.0-3-gabcdef-dirty");
  assert.match(p.api.compose(""), /rukebox:latest$/m);
  assert.match(p.api.lxcCommand(""), /--branch main /);
});

test("the radio step refuses a short or mismatched password", () => {
  const p = load();
  p.api.show(1);
  p.$("wzPw").value = "short";
  p.$("wzPw2").value = "short";
  p.$("wzNext").click();
  assert.equal(p.$("wzError").hidden, false);
  p.$("wzPw").value = "long enough";
  p.$("wzNext").click();
  assert.match(p.$("wzError").textContent, /differ/i);
});

test("every text of the page exists in the six languages", () => {
  const p = load();
  const keys = new Set([...read("bootstrap", "server", "server.html").matchAll(/data-i18n="([^"]+)"/g)].map((m) => m[1]));
  for (const m of read("bootstrap", "server", "server.js").matchAll(/"(server\.[a-z_]+|setup\.[a-z_]+)"/g)) keys.add(m[1]);
  const I18N = p.window.__I18N;
  for (const lang of ["en", "fr", "de", "es", "it", "nl"]) {
    for (const key of keys) assert.ok(key in I18N[lang], lang + " lacks " + key);
  }
});

test("the settings folder says where its config and data folders land", () => {
  const p = load();
  assert.equal(p.$("wzHomeResult").textContent, "Settings in ./config, statistics in ./data");
  p.$("wzHome").value = "/srv/radio/";
  p.$("wzHome").dispatchEvent(new p.window.Event("input"));
  assert.equal(p.$("wzHomeResult").textContent, "Settings in /srv/radio/config, statistics in /srv/radio/data");
});
