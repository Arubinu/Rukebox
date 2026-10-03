"use strict";

(function () {
  const PAYLOAD = window.RUKEBOX_PAYLOAD || {};
  const MEDIA_BUDGET = 380 * 1024 * 1024;
  const AUDIO_EXT = /\.(mp3|ogg|opus|flac|m4a|aac|wav|wma)$/i;
  const MUSIC_EXT = /\.(mp3|ogg|opus|flac|m4a|aac|wav|wma|lrc|srt|vtt|txt|jpe?g|png|webp)$/i;

  const PLACEHOLDER_HASH = "$6$raspberryradiose$sIhB.tiKCeajySW5V7Cxf.HEcDgTxmNScaWIf4ZvLug1ICXRdWe7Gy47YOpnYJUmaIrDqg/BpmQzMFeI5QKmp1";
  const HOOK = "systemd.run=/boot/firmware/firstrun.sh systemd.run_success_action=reboot systemd.unit=kernel-command-line.target";

  const $ = (id) => document.getElementById(id);
  const steps = [...document.querySelectorAll(".wz-step")];
  const state = { card: null, cardInfo: {}, bundle: null, bundleName: "", media: { music: [], memes: [], morning_announcements: [] }, written: false };
  let current = 0;

  const dots = $("wzDots");
  steps.forEach(() => dots.appendChild(document.createElement("li")));

  function show(i) {
    current = Math.max(0, Math.min(steps.length - 1, i));
    steps.forEach((s, k) => { s.hidden = k !== current; });
    [...dots.children].forEach((d, k) => { d.className = k < current ? "done" : (k === current ? "on" : ""); });
    const name = steps[current].dataset.step;
    $("wzBack").hidden = current === 0 || name === "done";
    $("wzNext").hidden = name === "write" || name === "done";
    setError(null);
    if (name === "write") paintSummary();
    if (name === "done") findPi();
    window.scrollTo({ top: 0 });
  }

  function setError(key, vars) {
    const box = $("wzError");
    box.hidden = !key;
    box.textContent = key ? t(key, vars) : "";
  }

  $("wzBack").addEventListener("click", () => show(current - 1));
  $("wzNext").addEventListener("click", () => {
    const err = validate(steps[current].dataset.step);
    if (err) { setError(err); return; }
    show(current + 1);
  });

  const choice = (name) => (document.querySelector('input[name="' + name + '"]:checked') || {}).value;
  const accountFields = () => ({ password: $("wzPw").value, key: $("wzKey").value.trim() });

  // A private key or a truncated paste would leave a Pi nobody can log into.
  const KEY_LINE = /^(ssh-ed25519|ssh-rsa|ssh-dss|ecdsa-sha2-nistp(?:256|384|521)|sk-ssh-ed25519@openssh\.com|sk-ecdsa-sha2-nistp256@openssh\.com)\s+[A-Za-z0-9+/]+={0,3}(?:\s+\S.*)?$/;

  function keyProblem(text) {
    if (/-----BEGIN [A-Z ]*PRIVATE KEY-----/.test(text)) return "setup.err_key_private";
    for (const line of text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean)) {
      if (!KEY_LINE.test(line)) return "setup.err_key_bad";
      let blob;
      try { blob = atob(line.split(/\s+/)[1]); } catch (e) { return "setup.err_key_bad"; }
      if (blob.length < 40) return "setup.err_key_bad";
    }
    return null;
  }

  function accountManage() {
    const f = accountFields();
    if (f.password) return "yes";
    if (f.key) return choice("account") === "here" ? "yes" : "key";
    return choice("account") === "here" ? "yes" : "no";
  }

  // Its own file on the card, so firstrun.sh can delete it before doing anything else.
  function accountEnv(manage) {
    return "ACCOUNT_USER=pi\nACCOUNT_MANAGE=" + manage +
      "\nACCOUNT_PASSWORD_B64='" + b64($("wzPw").value) + "'" +
      "\nACCOUNT_SSH_KEY_B64='" + b64($("wzKey").value.trim()) + "'\n";
  }

  function validate(step) {
    if (step === "card" && !state.card) return "setup.err_no_card";
    if (step === "account") {
      if (!/^[A-Za-z0-9-]{1,63}$/.test($("wzHost").value)) return "setup.err_hostname";
      const f = accountFields();
      if (f.password !== $("wzPw2").value) return "setup.err_pw_match";
      if (choice("account") === "here" && !f.password && !f.key) return "setup.err_no_login";
      if (f.key) {
        const bad = keyProblem(f.key);
        if (bad) return bad;
      }
    }
    if (step === "internet") {
      if (choice("internet") === "wifi") {
        if (!$("wzWifiSsid").value.trim()) return "setup.err_wifi_ssid";
        const pw = $("wzWifiPw").value;
        if (pw && (pw.length < 8 || pw.length > 63)) return "setup.err_wifi_pw";
      }
      if ($("wzCountry").value && !/^[A-Za-z]{2}$/.test($("wzCountry").value)) return "setup.err_country";
    }
    if (step === "ap") {
      if (!$("wzApSsid").value.trim()) return "setup.err_ap_ssid";
      const pw = $("wzApPw").value;
      if (pw && (pw.length < 8 || pw.length > 63)) return "setup.err_ap_pw";
      if (!$("wzSameWeb").checked && $("wzWebPw").value && $("wzWebPw").value.length < 8) return "setup.err_web_pw";
    }
    if (step === "files" && mediaBytes() > MEDIA_BUDGET) return "setup.err_too_big";
    return null;
  }

  const b64 = (text) => btoa(unescape(encodeURIComponent(text || "")));
  const bytesOf = (b64text) => Uint8Array.from(atob(b64text), (c) => c.charCodeAt(0));
  const mb = (n) => n < 1048576 ? Math.max(1, Math.round(n / 1024)) + " KB"
    : (n / 1048576).toFixed(n < 10485760 ? 1 : 0) + " MB";

  // Firefox and Safari can read a folder the person picks, never write into it:
  // the card is read from that folder, and what would be written goes into a ZIP.
  const DIRECT = !!(window.showDirectoryPicker || window.__rukeboxTestCard);

  function zipCard(fileList) {
    const top = new Map();
    for (const f of fileList) {
      const parts = (f.webkitRelativePath || f.name).split("/");
      const name = parts.length > 1 ? parts[1] : parts[0];
      if (!top.has(name)) top.set(name, parts.length > 2 ? null : f);
    }
    const out = new Map();
    return {
      zip: true,
      out,
      async *entries() { for (const name of top.keys()) yield [name]; },
      async readText(name) { const f = top.get(name); return f ? f.text() : null; },
      write(path, data) { out.set(path, data); },
      remove(name) { for (const k of [...out.keys()]) if (k === name || k.startsWith(name + "/")) out.delete(k); },
    };
  }

  const CRC_TABLE = (() => {
    const table = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1;
      table[n] = c >>> 0;
    }
    return table;
  })();

  function crc32(bytes) {
    let c = 0xFFFFFFFF;
    for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xFF] ^ (c >>> 8);
    return (c ^ 0xFFFFFFFF) >>> 0;
  }

  // Stored, not compressed: the card holds sound files that do not compress anyway.
  async function buildZip(files, onProgress) {
    const enc = new TextEncoder();
    const now = new Date();
    const time = (now.getHours() << 11) | (now.getMinutes() << 5) | (now.getSeconds() >> 1);
    const date = ((now.getFullYear() - 1980) << 9) | ((now.getMonth() + 1) << 5) | now.getDate();
    const parts = [];
    const central = [];
    let offset = 0;
    let count = 0;
    for (const [path, data] of files) {
      const bytes = typeof data === "string" ? enc.encode(data)
        : data instanceof Uint8Array ? data : new Uint8Array(await data.arrayBuffer());
      const name = enc.encode(path);
      const crc = crc32(bytes);
      const head = (size, sig) => {
        const v = new DataView(new ArrayBuffer(size));
        v.setUint32(0, sig, true);
        return v;
      };
      const local = head(30, 0x04034b50);
      local.setUint16(4, 20, true);
      local.setUint16(6, 0x0800, true);
      local.setUint16(10, time, true);
      local.setUint16(12, date, true);
      local.setUint32(14, crc, true);
      local.setUint32(18, bytes.length, true);
      local.setUint32(22, bytes.length, true);
      local.setUint16(26, name.length, true);
      parts.push(local.buffer, name, data instanceof Blob ? data : bytes);
      const entry = head(46, 0x02014b50);
      entry.setUint16(4, 20, true);
      entry.setUint16(6, 20, true);
      entry.setUint16(8, 0x0800, true);
      entry.setUint16(12, time, true);
      entry.setUint16(14, date, true);
      entry.setUint32(16, crc, true);
      entry.setUint32(20, bytes.length, true);
      entry.setUint32(24, bytes.length, true);
      entry.setUint16(28, name.length, true);
      entry.setUint32(42, offset, true);
      central.push(entry.buffer, name);
      offset += 30 + name.length + bytes.length;
      count += 1;
      if (onProgress) onProgress(count / files.length);
    }
    const centralSize = central.reduce((n, p) => n + p.byteLength, 0);
    const end = new DataView(new ArrayBuffer(22));
    end.setUint32(0, 0x06054b50, true);
    end.setUint16(8, count, true);
    end.setUint16(10, count, true);
    end.setUint32(12, centralSize, true);
    end.setUint32(16, offset, true);
    return new Blob([...parts, ...central, end.buffer], { type: "application/zip" });
  }

  function downloadZip() {
    if (!state.zip) return;
    const a = document.createElement("a");
    a.href = URL.createObjectURL(state.zip);
    a.download = "rukebox-card.zip";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 60000);
  }

  async function readText(dir, name) {
    if (dir.zip) return dir.readText(name);
    try { return await (await (await dir.getFileHandle(name)).getFile()).text(); } catch (e) { return null; }
  }

  async function writeFile(dir, path, data) {
    if (dir.zip) return dir.write(path, data);
    const parts = path.split("/").filter(Boolean);
    let d = dir;
    for (const part of parts.slice(0, -1)) d = await d.getDirectoryHandle(part, { create: true });
    const fh = await d.getFileHandle(parts[parts.length - 1], { create: true });
    const w = await fh.createWritable();
    await w.write(data);
    await w.close();
  }

  async function removeEntry(dir, name) {
    if (dir.zip) return dir.remove(name);
    try { await dir.removeEntry(name, { recursive: true }); } catch (e) {  }
  }

  async function projectFiles() {
    const stream = new Blob([bytesOf(PAYLOAD.project || "")]).stream().pipeThrough(new DecompressionStream("gzip"));
    const tar = new Uint8Array(await new Response(stream).arrayBuffer());
    const files = [];
    const text = (a, b) => new TextDecoder().decode(tar.subarray(a, b)).replace(/\0.*$/s, "");
    for (let off = 0; off + 512 <= tar.length;) {
      const name = text(off, off + 100);
      if (!name) break;
      const size = parseInt(text(off + 124, off + 136).trim() || "0", 8);
      const type = String.fromCharCode(tar[off + 156] || 48);
      const prefix = text(off + 345, off + 500);
      const path = prefix ? prefix + "/" + name : name;
      if (type === "0" || type === "\0") files.push({ path, data: tar.slice(off + 512, off + 512 + size) });
      off += 512 + Math.ceil(size / 512) * 512;
    }
    return files;
  }

  // The account cloud-init builds from Imager's settings never gets its key on
  // these images: take it off the card and let the card install it itself.
  const KEY_PREFIX = /(?:ssh-(?:ed25519|rsa|dss)|ecdsa-sha2-nistp(?:256|384|521)|sk-ssh-ed25519@openssh\.com)\s+\S+/;

  function imagerKeys(texts) {
    const keys = [];
    const add = (candidate) => {
      const key = (candidate || "").trim();
      if (key && KEY_PREFIX.test(key) && !keyProblem(key) && !keys.includes(key)) keys.push(key);
    };
    for (const text of texts) {
      if (!text) continue;
      for (const line of text.split(/\r?\n/)) {
        const plain = line.match(new RegExp("^\\s*(?:-\\s*)?(" + KEY_PREFIX.source + ".*)$"));
        if (plain) add(plain[1]);
        const encoded = line.match(/echo\s+'([A-Za-z0-9+/=]{40,})'\s*\|\s*base64\s+-d/);
        if (encoded) { try { add(atob(encoded[1])); } catch (e) {  } }
      }
    }
    return keys;
  }

  $("wzPickCard").addEventListener("click", async () => {
    if (!DIRECT) {
      $("wzCardFolder").click();
      return;
    }
    let dir;
    try {
      dir = window.__rukeboxTestCard || await window.showDirectoryPicker({ id: "rukebox-card", mode: "readwrite" });
    } catch (e) { return;  }
    await useCard(dir);
  });

  $("wzCardFolder").addEventListener("change", async () => {
    const files = $("wzCardFolder").files;
    if (files && files.length) await useCard(zipCard(files));
    $("wzCardFolder").value = "";
  });

  async function useCard(dir) {
    const names = new Set();
    for await (const [name] of dir.entries()) names.add(name);
    const status = $("wzCardStatus");
    if (!names.has("config.txt") || !names.has("cmdline.txt")) {
      state.card = null;
      status.textContent = t("setup.card_not_boot");
      status.classList.add("warning");
      return;
    }

    const firstrun = names.has("firstrun.sh") ? await readText(dir, "firstrun.sh") : null;
    const userData = names.has("user-data") ? await readText(dir, "user-data") : null;
    const imagerFirstrun = !!firstrun && !firstrun.includes("rukebox");
    const keys = imagerKeys([userData, imagerFirstrun ? firstrun : null]);
    if (keys.length && !$("wzKey").value.trim()) $("wzKey").value = keys.join("\n");
    let user = null;
    const m1 = userData && userData.match(/^\s*users:\s*\n\s*-\s*name:\s*["']?([A-Za-z0-9_-]+)/m);
    const m2 = imagerFirstrun && firstrun.match(/userconf(?:-pi\/userconf)?\s+'([^']+)'/);
    user = (m1 && m1[1]) || (m2 && m2[1]) || null;
    state.card = dir;
    state.cardInfo = { imagerFirstrun, imagerUser: user, imagerKey: keys.length > 0, previous: names.has("rukebox") };
    if (user) {
      document.querySelector('input[name="account"][value="imager"]').checked = true;
      paintAccount();
    }
    show(steps.findIndex((s) => s.dataset.step === "card") + 1);
  }

  function paintAccount() {
    const imager = choice("account") !== "here";
    $("wzAccountNote").hidden = !imager;
    const u = state.cardInfo.imagerUser;
    const warn = $("wzUserWarn");
    warn.hidden = !imager || !u || u === "pi";
    warn.textContent = u && u !== "pi" ? t("setup.warn_user", { name: u }) : "";
    // No key Imager can be shown to hold, and none given here: nobody could log in.
    const f = accountFields();
    const keyWarn = $("wzKeyWarn");
    keyWarn.hidden = !(imager && state.cardInfo.imagerKey !== true && !f.password && !f.key);
    keyWarn.textContent = keyWarn.hidden ? "" : t("setup.warn_no_key");
  }
  document.querySelectorAll('input[name="account"]').forEach((r) => r.addEventListener("change", paintAccount));
  ["wzPw", "wzPw2", "wzKey"].forEach((id) => $(id).addEventListener("input", paintAccount));

  function paintInternet() { $("wzWifiFields").hidden = choice("internet") !== "wifi"; }
  document.querySelectorAll('input[name="internet"]').forEach((r) => r.addEventListener("change", paintInternet));

  const region = (navigator.languages || [navigator.language || ""])
    .map((l) => (l || "").split("-")[1]).find((r) => r && /^[A-Za-z]{2}$/.test(r));
  if (region) $("wzCountry").value = region.toUpperCase();

  $("wzSameWeb").addEventListener("change", () => { $("wzWebRow").hidden = $("wzSameWeb").checked; });

  const zones = (Intl.supportedValuesOf && Intl.supportedValuesOf("timeZone")) || [];
  const here = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  for (const z of (zones.length ? zones : [here])) {
    const o = document.createElement("option");
    o.value = o.textContent = z;
    $("wzTz").appendChild(o);
  }
  $("wzTz").value = here;
  $("wzStart").addEventListener("change", () => { $("wzStartTimeRow").hidden = $("wzStart").value !== "scheduled"; });

  function paintBundle() {
    $("wzBundleDrop").classList.toggle("has-file", !!state.bundleName);
    $("wzBundleText").textContent = state.bundleName || t("setup.bundle_drop");
  }

  async function readBundle(file) {
    const status = $("wzBundleStatus");
    state.bundle = null;
    state.bundleName = file ? file.name : "";
    status.textContent = "";
    if (!file) { paintBundle(); return; }
    paintBundle();
    try {
      const data = JSON.parse(await file.text());
      if (!data || data.format !== "rukebox-config" || typeof data.settings !== "object") throw new Error("format");
      state.bundle = data;
      const s = data.settings;
      if (s.MUSIC_START_MODE) $("wzStart").value = s.MUSIC_START_MODE;
      const hm = (h, m) => String(h).padStart(2, "0") + ":" + String(m).padStart(2, "0");
      if (s.MUSIC_START_HOUR !== undefined) $("wzStartTime").value = hm(s.MUSIC_START_HOUR, s.MUSIC_START_MINUTE || 0);
      if (s.CUTOFF_HOUR !== undefined) $("wzCutoff").value = hm(s.CUTOFF_HOUR, s.CUTOFF_MINUTE || 0);
      if (s.AUDIO_OUTPUT) $("wzOutput").value = s.AUDIO_OUTPUT;
      $("wzStartTimeRow").hidden = $("wzStart").value !== "scheduled";
      status.textContent = t("setup.bundle_ok", { n: Object.keys(s).length });
      status.classList.remove("warning");
    } catch (e) {
      state.bundleName = "";
      paintBundle();
      status.textContent = t("err.not_a_bundle");
      status.classList.add("warning");
    }
  }

  function openBundlePicker() { $("wzBundle").click(); }

  const bundleDrop = $("wzBundleDrop");
  bundleDrop.addEventListener("click", openBundlePicker);
  bundleDrop.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openBundlePicker(); }
  });
  ["dragenter", "dragover"].forEach((type) => bundleDrop.addEventListener(type, (e) => {
    e.preventDefault();
    bundleDrop.classList.add("is-over");
  }));
  ["dragleave", "drop"].forEach((type) => bundleDrop.addEventListener(type, (e) => {
    e.preventDefault();
    bundleDrop.classList.remove("is-over");
  }));
  bundleDrop.addEventListener("drop", (e) => {
    const files = e.dataTransfer && e.dataTransfer.files;
    if (!files || !files.length) return;
    try { $("wzBundle").files = files; } catch (err) {  }
    readBundle(files[0]);
  });
  $("wzBundle").addEventListener("change", () => readBundle($("wzBundle").files[0]));

  // The four folders a card can carry, and the folder name each one becomes on
  // the Pi (see install.sh): they must match.
  const FILE_SOURCES = [
    ["wzMusic", "music", true],
    ["wzMemes", "memes", false],
    ["wzMorning", "morning_announcements", false],
    ["wzCutoffFiles", "cutoff_announcements", false],
  ];

  function mediaBytes() {
    return Object.values(state.media).flat().reduce((n, f) => n + f.file.size, 0);
  }
  function paintFiles() {
    const n = Object.values(state.media).flat().length;
    const bytes = mediaBytes();
    const status = $("wzFilesStatus");
    status.textContent = n ? t("setup.files_total", { n, size: mb(bytes), max: mb(MEDIA_BUDGET) }) : "";
    status.classList.toggle("warning", bytes > MEDIA_BUDGET);
    for (const [input, target] of FILE_SOURCES) {
      const chosen = (state.media[target] || []).length;
      $(input + "Count").textContent = chosen ? t("setup.files_count", { n: chosen }) : "";
      $(input).closest(".wz-file-row").classList.toggle("has-files", chosen > 0);
    }
  }
  function pick(input, target, keepTree) {
    input.addEventListener("change", () => {
      state.media[target] = [...input.files]
        .filter((f) => !f.name.startsWith(".") && (keepTree ? MUSIC_EXT : AUDIO_EXT).test(f.name))
        .map((f) => {
          const rel = keepTree && f.webkitRelativePath ? f.webkitRelativePath.split("/").slice(1).join("/") : f.name;
          return { file: f, path: target + "/" + rel };
        })
        .filter((m) => !m.path.split("/").some((p) => p.startsWith(".")));
      paintFiles();
    });
  }
  FILE_SOURCES.forEach(([input, target, keepTree]) => pick($(input), target, keepTree));

  function setupAnswers() {
    const net = choice("internet");
    const same = $("wzSameWeb").checked;
    return {
      version: 1,
      internet: net,
      hostname: $("wzHost").value,
      timezone: $("wzTz").value,
      home_wifi: net === "wifi" ? { ssid: $("wzWifiSsid").value.trim(), password: $("wzWifiPw").value } : { ssid: "", password: "" },
      wifi_country: $("wzCountry").value.toUpperCase(),
      access_point: { ssid: $("wzApSsid").value.trim(), password: $("wzApPw").value },
      web_password: same ? $("wzApPw").value : $("wzWebPw").value,
      boot_tweaks: true,
      system_upgrade: false,
    };
  }

  function radioBundle() {
    const bundle = state.bundle ? JSON.parse(JSON.stringify(state.bundle)) : { format: "rukebox-config", version: 1, settings: {} };
    const s = bundle.settings;
    const [sh, sm] = $("wzStartTime").value.split(":");
    const [ch, cm] = $("wzCutoff").value.split(":");
    s.MUSIC_START_MODE = $("wzStart").value;
    s.MUSIC_START_HOUR = String(Number(sh)); s.MUSIC_START_MINUTE = String(Number(sm));
    s.CUTOFF_HOUR = String(Number(ch)); s.CUTOFF_MINUTE = String(Number(cm));
    s.AUDIO_OUTPUT = $("wzOutput").value;
    return bundle;
  }

  function paintSummary() {
    const a = setupAnswers();
    const f = accountFields();
    const here = choice("account") === "here";
    const how = t(here ? "setup.account_here" : "setup.account_imager");
    const lines = [
      t(here || !(f.password || f.key) ? "setup.sum_account" : "setup.sum_account_plus", { how, name: a.hostname }),
      t("setup.sum_internet", { how: t("setup.net_" + a.internet) }),
      t("setup.sum_ap", { name: a.access_point.ssid, how: t(a.access_point.password ? "setup.sum_secured" : "setup.sum_open") }),
      t("setup.sum_files", { n: Object.values(state.media).flat().length, size: mb(mediaBytes()) }),
    ];
    $("wzSummary").replaceChildren(...lines.map((l) => { const li = document.createElement("li"); li.textContent = l; return li; }));
  }

  function progress(frac, key, vars) {
    $("wzBar").style.width = Math.round(frac * 100) + "%";
    if (key) $("wzWriteStatus").textContent = t(key, vars);
  }

  async function writeCard() {
    const dir = state.card;
    const manage = accountManage();
    const media = Object.values(state.media).flat();

    progress(0.02, "setup.w_project");
    const files = await projectFiles();
    await removeEntry(dir, "rukebox");
    for (let i = 0; i < files.length; i++) {
      await writeFile(dir, files[i].path, files[i].data);
      if (i % 10 === 0) progress(0.02 + 0.48 * (i / files.length));
    }

    progress(0.52, "setup.w_boot");
    if (state.cardInfo.imagerFirstrun) {
      const imager = await readText(dir, "firstrun.sh");
      await writeFile(dir, "rukebox-imager-firstrun.sh", imager);
      await removeEntry(dir, "firstrun.sh");
    }
    const firstrun = (PAYLOAD.firstrun || "")
      .split("__HOSTNAME__").join($("wzHost").value)
      .replace(/\r\n/g, "\n");
    await writeFile(dir, "firstrun.sh", firstrun);

    let cmdline = ((await readText(dir, "cmdline.txt")) || "").replace(/\s+$/, "");
    if (!/modules-load=dwc2/.test(cmdline)) cmdline = cmdline.replace("rootwait", "rootwait modules-load=dwc2");
    cmdline = cmdline.replace(/ ?systemd\.run=\S*/g, "").replace(/ ?systemd\.run_success_action=\S*/g, "")
      .replace(/ ?systemd\.unit=\S*/g, "") + " " + HOOK;
    await writeFile(dir, "cmdline.txt", cmdline);

    let config = (await readText(dir, "config.txt")) || "";
    if (!/^# rukebox: USB gadget mode/m.test(config)) {
      config += "\n[all]\n# rukebox: USB gadget mode - makes the Pi appear as a USB network\n# interface, so SSH works over the USB cable alone.\ndtoverlay=dwc2,dr_mode=otg\n";
    }
    if (!/^# rukebox: I2C/m.test(config)) {
      config += "\n[all]\n# rukebox: I2C + DS3231 real-time clock.\ndtparam=i2c_arm=on\ndtoverlay=i2c-rtc,ds3231\n";
    }
    await writeFile(dir, "config.txt", config);

    await writeFile(dir, "ssh", "");
    await writeFile(dir, "rukebox-account.env", accountEnv(manage));
    if (manage !== "no") await writeFile(dir, "userconf.txt", "pi:" + PLACEHOLDER_HASH + "\n");

    progress(0.6, "setup.w_answers");
    await writeFile(dir, "rukebox-setup.json", JSON.stringify(setupAnswers(), null, 2));
    await writeFile(dir, "rukebox-config.json", JSON.stringify(radioBundle(), null, 2));

    let done = 0;
    for (const m of media) {
      progress(0.62 + 0.36 * (done / Math.max(1, media.length)), "setup.w_files", { n: done + 1, total: media.length });
      await writeFile(dir, "rukebox-media/" + m.path, m.file);
      done++;
    }
    if (dir.zip) {
      progress(0.98, "setup.w_zip");
      state.zip = await buildZip([...dir.out.entries()]);
      downloadZip();
    }
    progress(1, "setup.w_done");
  }

  $("wzWrite").addEventListener("click", async () => {
    const btn = $("wzWrite");
    btn.disabled = true;
    $("wzBack").disabled = true;
    setError(null);
    try {
      await writeCard();
      state.written = true;
      show(steps.findIndex((s) => s.dataset.step === "done"));
    } catch (e) {
      console.error(e);
      setError("setup.err_write", { detail: String(e && e.message || e) });
    } finally {
      btn.disabled = false;
      $("wzBack").disabled = false;
    }
  });

  function probe(base) {
    return new Promise((resolve) => {
      const img = new Image();
      const timer = setTimeout(() => { img.src = ""; resolve(false); }, 4000);
      img.onload = () => { clearTimeout(timer); resolve(true); };
      img.onerror = () => { clearTimeout(timer); resolve(false); };
      img.src = base + "logo.webp?" + Date.now();
    });
  }
  let finding = false;
  async function findPi() {
    if (finding) return;
    finding = true;
    const bases = ["http://" + ($("wzHost").value || "rukebox") + ".local/", "http://169.254.7.7/", "http://10.42.0.1/"];
    const status = $("wzFindStatus");
    for (;;) {
      status.textContent = t("setup.finding");
      for (const base of bases) {
        if (await probe(base)) {
          status.textContent = t("setup.found", { url: base });
          $("wzOpen").href = base;
          $("wzOpenRow").hidden = false;
          setTimeout(() => { location.href = base; }, 2500);
          return;
        }
      }
      await new Promise((r) => setTimeout(r, 5000));
    }
  }

  if (!DIRECT) {
    $("wzUnsupported").hidden = false;
    $("wzWrite").dataset.i18n = "setup.write_zip";
    $("wzWrite").textContent = t("setup.write_zip");
    $("wzZipStep").hidden = false;
    $("wzZipAgainRow").hidden = false;
  }
  $("wzZipAgain").addEventListener("click", downloadZip);
  paintAccount();
  paintInternet();
  paintBundle();
  window.LANG_CHANGE_LISTENERS.push(() => { paintAccount(); paintFiles(); paintBundle(); if (steps[current].dataset.step === "write") paintSummary(); });
  window.__rukeboxWizard = { show, state, writeCard, steps, zipCard, useCard, buildZip };
  show(0);
})();
