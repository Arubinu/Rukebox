"use strict";

(function () {
  const PAYLOAD = window.RUKEBOX_PAYLOAD || {};
  const RELEASE = /^v\d+\.\d+\.\d+$/.test(PAYLOAD.release || "") ? PAYLOAD.release : "";
  const IMAGE = "ghcr.io/arubinu/rukebox:" + (RELEASE ? RELEASE.slice(1) : "latest");
  const REPO = "https://github.com/Arubinu/Rukebox.git";
  // Must match web_auth.py, which checks the hash the browser makes here.
  const ITERATIONS = 260000;

  const $ = (id) => document.getElementById(id);
  const steps = [...document.querySelectorAll(".wz-step")];
  const choice = (name) => (document.querySelector('input[name="' + name + '"]:checked') || {}).value;
  let current = 0;
  let hashed = { password: null, hash: "" };

  const dots = $("wzDots");
  steps.forEach(() => dots.appendChild(document.createElement("li")));

  function show(i) {
    current = Math.max(0, Math.min(steps.length - 1, i));
    steps.forEach((s, k) => { s.hidden = k !== current; });
    [...dots.children].forEach((d, k) => { d.className = k < current ? "done" : (k === current ? "on" : ""); });
    $("wzBack").hidden = current === 0;
    $("wzNext").hidden = current === steps.length - 1;
    setError(null);
    window.scrollTo({ top: 0 });
  }

  function setError(key) {
    $("wzError").hidden = !key;
    $("wzError").textContent = key ? t(key) : "";
  }

  $("wzBack").addEventListener("click", () => show(current - 1));
  $("wzNext").addEventListener("click", async () => {
    const err = validate(steps[current].dataset.step);
    if (err) { setError(err); return; }
    if (steps[current + 1].dataset.step === "result") {
      try { await paintResult(); } catch (e) { setError("server.err_crypto"); return; }
    }
    show(current + 1);
  });

  const isInt = (text, low, high) => /^\d+$/.test(text) && Number(text) >= low && Number(text) <= high;

  function validate(step) {
    const target = choice("target");
    if (step === "radio") {
      if (!$("wzName").value.trim()) return "server.err_name";
      const pw = $("wzPw").value;
      if (pw && pw.length < 8) return "setup.err_web_pw";
      if (pw !== $("wzPw2").value) return "setup.err_pw_match";
    }
    if (step === "sound" && choice("sound") === "pipewire" && !isInt($("wzUid").value, 0, 4294967294)) return "server.err_uid";
    if (step === "place") {
      if (target === "lxc") {
        if (choice("sound") === "card" && !isInt($("wzCtId").value, 100, 999999999)) return "server.err_ct_id";
        return null;
      }
      const music = $("wzMusic").value.trim(), home = $("wzHome").value.trim();
      if (!music || !home) return "server.err_folder";
      if (target === "stack" && !(music.startsWith("/") && home.startsWith("/"))) return "server.err_absolute";
      if (!isInt($("wzPort").value, 1, 65535)) return "server.err_port";
    }
    return null;
  }

  // Each target's own default, replaced only while the field still holds the other one.
  const PLACES = {
    docker: { music: "./music", home: "./" },
    stack: { music: "/opt/rukebox/music", home: "/opt/rukebox" },
  };
  function paintTarget() {
    const target = choice("target");
    const lxc = target === "lxc";
    $("wzSoundPipewire").hidden = lxc;
    $("wzSoundBluetooth").hidden = lxc;
    if (lxc && ["pipewire", "bluetooth"].includes(choice("sound"))) {
      document.querySelector('input[name="sound"][value="stream"]').checked = true;
    }
    $("wzPiperRow").hidden = !lxc;
    $("wzDockerPlace").hidden = lxc;
    $("wzLxcPlace").hidden = !lxc;
    const place = PLACES[target];
    if (place) {
      for (const [field, key] of [["wzMusic", "music"], ["wzHome", "home"]]) {
        const value = $(field).value.trim();
        if (!value || Object.values(PLACES).some((p) => p[key] === value)) $(field).value = place[key];
      }
    }
    paintSound();
  }
  function paintSound() {
    const sound = choice("sound");
    $("wzCardRow").hidden = sound !== "card";
    $("wzUidRow").hidden = sound !== "pipewire";
    $("wzHostNet").hidden = sound !== "bluetooth";
  }
  document.querySelectorAll('input[name="target"]').forEach((r) => r.addEventListener("change", paintTarget));
  document.querySelectorAll('input[name="sound"]').forEach((r) => r.addEventListener("change", paintSound));

  const zones = (Intl.supportedValuesOf && Intl.supportedValuesOf("timeZone")) || [];
  const here = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  for (const z of (zones.length ? zones : [here])) {
    const o = document.createElement("option");
    o.value = o.textContent = z;
    $("wzTz").appendChild(o);
  }
  $("wzTz").value = here;
  $("wzLang").value = ["en", "fr", "de", "es", "it", "nl"].includes(currentLang) ? currentLang : "en";

  const hex = (bytes) => [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
  const b64 = (text) => btoa(unescape(encodeURIComponent(text)));

  async function passwordHash() {
    const pw = $("wzPw").value;
    if (!pw) return "";
    // A new salt only for a new password, so going back and forth keeps the same text.
    if (hashed.password === pw) return hashed.hash;
    const salt = crypto.getRandomValues(new Uint8Array(16));
    const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(pw), "PBKDF2", false, ["deriveBits"]);
    const bits = await crypto.subtle.deriveBits({ name: "PBKDF2", hash: "SHA-256", salt, iterations: ITERATIONS }, key, 256);
    hashed = { password: pw, hash: "pbkdf2_sha256$" + ITERATIONS + "$" + hex(salt) + "$" + hex(new Uint8Array(bits)) };
    return hashed.hash;
  }

  function bundle() {
    const sound = choice("sound");
    const settings = { UPNP_NAME: $("wzName").value.trim(), SPEECH_LANGUAGE: $("wzLang").value };
    if (sound === "card") settings.AUDIO_OUTPUT = $("wzCard").value;
    if (sound === "bluetooth" || sound === "snapcast") settings.AUDIO_OUTPUT = sound;
    // The machine's own network: no port is mapped, so the radio listens on the chosen one itself.
    if (sound === "bluetooth" && choice("target") !== "lxc") settings.WEB_PORT = $("wzPort").value;
    return b64(JSON.stringify({ format: "rukebox-config", version: 1, settings }));
  }

  // Compose reads "$" as a variable: "$$" is a plain one.
  const yq = (text) => JSON.stringify(String(text).replace(/\$/g, "$$$$"));
  const sq = (text) => "'" + String(text).replace(/'/g, "'\\''") + "'";
  const under = (home, name) => {
    const base = home.replace(/\/+$/, "");
    return (base === "." ? "./" : base + "/") + name;
  };

  function compose(hash) {
    const target = choice("target"), sound = choice("sound");
    const home = $("wzHome").value.trim(), port = $("wzPort").value;
    const lines = ["# Rukebox, written by the server setup page.", "# RUKEBOX_SETUP and the password are read once, on the very first start."];
    if (target === "docker") lines.push("name: rukebox");
    lines.push("", "services:", "  rukebox:", "    image: " + IMAGE, "    container_name: rukebox", "    restart: unless-stopped");
    if (sound === "bluetooth") lines.push("    network_mode: host");
    lines.push("    environment:", "      TZ: " + yq($("wzTz").value), "      RUKEBOX_SETUP: " + yq(bundle()));
    if (hash) lines.push("      RUKEBOX_WEB_PASSWORD_HASH: " + yq(hash));
    if (sound === "pipewire") lines.push("      PIPEWIRE_RUNTIME_DIR: /run/rukebox", "      PULSE_SERVER: unix:/run/rukebox/pulse/native");
    if (sound !== "bluetooth") {
      lines.push("    ports:", "      - " + yq(port + ":80"));
      if (sound === "snapcast") lines.push('      - "1704:1704"', '      - "1780:1780"');
    }
    if (sound === "card") lines.push("    devices:", "      - /dev/snd:/dev/snd", "    group_add:", '      - "29"');
    lines.push("    volumes:", "      - " + yq(under(home, "config") + ":/config"), "      - " + yq(under(home, "data") + ":/data"),
      "      - " + yq($("wzMusic").value.trim() + ":/music" + ($("wzWritable").checked ? "" : ":ro")));
    if (sound === "pipewire") {
      const run = "/run/user/" + $("wzUid").value;
      lines.push("      - " + yq(run + "/pipewire-0:/run/rukebox/pipewire-0"), "      - " + yq(run + "/pulse:/run/rukebox/pulse"));
    }
    if (sound === "bluetooth") lines.push("      - /var/run/dbus:/var/run/dbus", "      - /run/dbus:/run/dbus");
    return lines.join("\n") + "\n";
  }

  function lxcCommand(hash) {
    const env = ["RUKEBOX_PROFILE=lxc", "RUKEBOX_BOOT_TWEAKS=no", "RUKEBOX_SYSTEM_UPGRADE=no",
      "RUKEBOX_PIPER=" + ($("wzPiper").checked ? "yes" : "no"), "RUKEBOX_TIMEZONE=" + sq($("wzTz").value),
      "RUKEBOX_WEB_PASSWORD=", "RUKEBOX_WEB_PASSWORD_HASH=" + sq(hash), "RUKEBOX_SETUP=" + sq(bundle())];
    return "apt-get update && apt-get install -y git ca-certificates && " +
      "git clone --depth 1 --branch " + (RELEASE || "main") + " " + REPO + " /opt/rukebox-src && " +
      "cd /opt/rukebox-src && " + env.join(" ") + " bash scripts/install.sh && " +
      "cd / && rm -rf /opt/rukebox-src && systemctl enable --now rukebox-daemon.service rukebox-web.service\n";
  }

  const SOUND_HOST = "printf '%s\\n' 'lxc.cgroup2.devices.allow: c 116:* rwm' " +
    "'lxc.mount.entry: /dev/snd dev/snd none bind,optional,create=dir' >> /etc/pve/lxc/{id}.conf && " +
    "echo 'SUBSYSTEM==\"sound\", MODE=\"0666\"' > /etc/udev/rules.d/99-rukebox-sound.rules && " +
    "udevadm trigger --subsystem-match=sound";

  function item(key, vars, code, sub) {
    const li = document.createElement("li");
    const text = document.createElement("span");
    text.textContent = t(key, vars);
    li.appendChild(text);
    if (sub) {
      const ul = document.createElement("ul");
      for (const subKey of sub) {
        const entry = document.createElement("li");
        entry.textContent = t(subKey);
        ul.appendChild(entry);
      }
      li.appendChild(ul);
    }
    if (code) {
      const pre = document.createElement("pre");
      pre.className = "srv-inline";
      pre.textContent = code;
      li.appendChild(pre);
    }
    return li;
  }

  let result = { text: "", file: "" };
  async function paintResult() {
    const target = choice("target"), sound = choice("sound");
    const hash = await passwordHash();
    const address = t("server.address");
    const before = [], after = [];
    if (target === "lxc") {
      before.push(item("server.lxc_create", null, "", ["server.lxc_template", "server.lxc_unprivileged",
        "server.lxc_cores", "server.lxc_memory", "server.lxc_disk", "server.lxc_network"]));
      if (sound === "card") before.push(item("server.lxc_card", null, SOUND_HOST.replace("{id}", $("wzCtId").value)));
      before.push(item("server.lxc_run"));
      after.push(item("server.lxc_time"));
      after.push(item("server.lxc_open", { url: "http://<" + address + ">/" }));
      result = { text: lxcCommand(hash), file: "" };
    } else {
      const port = $("wzPort").value;
      if (target === "docker") {
        before.push(item("server.docker_folder"));
        after.push(item("server.docker_up", null, "docker compose up -d"));
      } else {
        before.push(item("server.stack_portainer"));
        before.push(item("server.stack_dockge"));
        after.push(item("server.stack_deploy"));
      }
      after.push(item("server.open", { url: "http://<" + address + ">" + (port === "80" ? "" : ":" + port) + "/" }));
      if (sound === "card") after.push(item("server.card_group", null, "getent group audio"));
      result = { text: compose(hash), file: "docker-compose.yml" };
    }
    $("wzSteps").replaceChildren(...before);
    $("wzAfter").replaceChildren(...after);
    // One numbering across the two lists, the code block belonging to the last step before it.
    $("wzAfter").start = before.length + 1;
    $("wzCodeName").textContent = result.file || t("server.code_command");
    $("wzCode").textContent = result.text;
    $("wzSecret").hidden = !hash;
    $("wzSave").hidden = !result.file;
    $("wzCopied").textContent = "";
  }

  $("wzCopy").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(result.text);
    } catch (e) {
      const range = document.createRange();
      range.selectNodeContents($("wzCode"));
      getSelection().removeAllRanges();
      getSelection().addRange(range);
      if (!document.execCommand("copy")) { $("wzCopied").textContent = t("server.copy_failed"); return; }
    }
    $("wzCopied").textContent = t("server.copied");
  });

  $("wzSave").addEventListener("click", () => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([result.text], { type: "text/yaml" }));
    a.download = result.file;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  });

  paintTarget();
  window.LANG_CHANGE_LISTENERS.push(() => { if (steps[current].dataset.step === "result") paintResult(); });
  window.__rukeboxServer = { show, compose, lxcCommand, bundle, passwordHash, paintResult };
  show(0);
})();
