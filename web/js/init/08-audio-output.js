function paintAudioOutputChoices() {
  // Never hidden before the outputs are read, nor while it is the chosen one.
  if (!audioOutputsKnown) return;
  const select = document.getElementById("audioOutputSelect");
  const snap = select.querySelector('option[value="snapcast"]') || paintAudioOutputChoices.snap;
  if (snap) {
    paintAudioOutputChoices.snap = snap;
    const offered = canDo("snapcast") || select.value === "snapcast";
    if (offered && !snap.isConnected) select.appendChild(snap);
    if (!offered && snap.isConnected) snap.remove();
  }
  const option = select.querySelector('option[value="docker"]');
  if (!option) return;
  const wanted = audioOutputs.some((o) => o.kind === "docker") || select.value === "docker";
  if (wanted && !option.isConnected) select.appendChild(option);
  if (!wanted && option.isConnected) option.remove();
}

function paintAudioDetected() {
  const kind = document.getElementById("audioOutputSelect").value;
  const line = document.getElementById("audioOutputDetected");
  const found = audioOutputs.find((o) => o.kind === kind);
  if (kind === "snapcast") {
    // Every listener is a second behind: the one thing to know before choosing it.
    const running = !!(snapcastState && snapcastState.running);
    const listening = running ? snapcastState.clients.filter((c) => c.connected).length : 0;
    line.textContent = t(running ? "audioout.snapcast_line" : "audioout.snapcast_off", { n: listening });
    line.classList.toggle("warning", !running);
    return;
  }
  if (kind === "docker") {
    // The virtual output is heard, not played: saying where it is means
    // saying what has to be listening on the other end.
    line.textContent = t(found ? "audioout.network_found" : "audioout.network_missing");
    line.classList.toggle("warning", !found);
    return;
  }
  if (found) {
    line.textContent = t("audioout.found", { name: found.description });
    line.classList.remove("warning");
  } else if (kind === "bluetooth") {
    line.textContent = t("audioout.bluetooth_none");
    line.classList.remove("warning");
  } else {
    line.textContent = t("audioout.not_found");
    line.classList.add("warning");
  }
}

async function refreshAudioOutputs() {
  const result = await apiGet("/api/audio/outputs");
  if (!result.ok || !result.data || !Array.isArray(result.data.outputs)) return;
  audioOutputs = result.data.outputs;
  audioOutputsKnown = true;
  if (document.getElementById("audioOutputSelect").value === "snapcast") {
    const snap = await apiGet("/api/snapcast/clients");
    snapcastState = snap.ok ? snap.data : null;
  }
  paintAudioOutputChoices();
  paintAudioDetected();
}

document.getElementById("audioOutputSelect").addEventListener("change", () => {
  paintAudioDetected();
  if (document.getElementById("audioOutputSelect").value === "snapcast") refreshAudioOutputs();
});

/* One line of the devices dialog: its name and mute on top, its volume across below. */
function snapcastLine(title, detail, volumeLabel) {
  const li = document.createElement("li");
  const head = document.createElement("div");
  head.className = "snapcast-head";
  const text = document.createElement("span");
  text.className = "snapcast-name";
  const name = document.createElement("span");
  name.className = "device-name";
  name.textContent = title;
  const sub = document.createElement("span");
  sub.className = "device-sub";
  sub.textContent = detail;
  text.append(name, sub);
  const mute = document.createElement("button");
  mute.type = "button";
  mute.className = "btn btn-icon btn-small";
  head.append(text, mute);
  const volume = document.createElement("input");
  volume.type = "range";
  volume.min = "0";
  volume.max = "100";
  volume.setAttribute("aria-label", volumeLabel);
  volume.addEventListener("input", () => setVolumeFill(volume));
  li.append(head, volume);
  return { li, head, mute, volume };
}

function paintSnapcastMute(button, muted, onKey, offKey) {
  button.dataset.icon = muted ? "volume-x" : "volume-low";
  button.setAttribute("aria-pressed", muted ? "true" : "false");
  button.setAttribute("aria-label", t(muted ? offKey : onKey));
  button.title = button.getAttribute("aria-label");
}

function snapcastRow(client, onChange) {
  const line = snapcastLine(client.name,
    t(client.connected ? "snapcast.connected" : "snapcast.disconnected")
      + (client.ip ? " · " + client.ip.replace(/^::ffff:/, "") : ""),
    t("snapcast.volume_of", { name: client.name }));
  const paint = () => {
    line.volume.value = String(client.volume);
    setVolumeFill(line.volume);
    paintSnapcastMute(line.mute, client.muted, "snapcast.mute", "snapcast.unmute");
  };
  paint();
  line.volume.addEventListener("change", async () => {
    const r = await apiPost("/api/snapcast/client", { id: client.id, volume: Number(line.volume.value) });
    if (!r.ok) {
      showError(r.error);
      return;
    }
    client.volume = Number(line.volume.value);
    onChange();
  });
  line.mute.addEventListener("click", async () => {
    const r = await apiPost("/api/snapcast/client", { id: client.id, muted: !client.muted });
    if (!r.ok) {
      showError(r.error);
      return;
    }
    client.muted = !client.muted;
    paint();
    onChange();
  });
  if (!client.connected) {
    // Snapcast cannot send away a device still connected: it would come straight back.
    const forget = document.createElement("button");
    forget.type = "button";
    forget.className = "btn btn-icon btn-small btn-danger-outline";
    forget.dataset.icon = "trash";
    forget.setAttribute("aria-label", t("snapcast.forget"));
    forget.title = t("snapcast.forget");
    forget.addEventListener("click", async () => {
      const r = await apiDelete("/api/snapcast/client", { id: client.id });
      if (!r.ok) showError(r.error);
      else line.li.remove();
    });
    line.head.append(forget);
  }
  return { li: line.li, paint };
}

document.getElementById("snapcastManage").addEventListener("click", async () => {
  const r = await apiGet("/api/snapcast/clients");
  const data = r.ok ? r.data : { running: false, clients: [] };
  snapcastState = data;
  const box = document.createElement("div");
  box.className = "snapcast-box";
  const how = document.createElement("p");
  how.className = "hint";
  how.textContent = t(data.running ? "snapcast.how" : "snapcast.not_running");
  box.append(how);
  if (data.running) {
    if (data.clients.length) {
      const list = document.createElement("ul");
      list.className = "device-list snapcast-list";
      const clients = data.clients;
      const all = snapcastLine(t("snapcast.all"), t("snapcast.all_desc"), t("snapcast.volume_all"));
      all.li.classList.add("snapcast-all");
      const paintAll = () => {
        const heard = clients.filter((c) => c.connected);
        const counted = heard.length ? heard : clients;
        all.volume.value = String(Math.round(counted.reduce((sum, c) => sum + c.volume, 0) / counted.length));
        setVolumeFill(all.volume);
        paintSnapcastMute(all.mute, counted.every((c) => c.muted), "snapcast.mute_all", "snapcast.unmute_all");
      };
      const rows = clients.map((client) => snapcastRow(client, paintAll));
      const setAll = async (change) => {
        const done = await apiPost("/api/snapcast/clients", change);
        if (!done.ok) {
          showError(done.error);
          paintAll();
          return;
        }
        clients.forEach((c) => Object.assign(c, change));
        rows.forEach((row) => row.paint());
        paintAll();
      };
      all.volume.addEventListener("change", () => setAll({ volume: Number(all.volume.value) }));
      all.mute.addEventListener("click", () => setAll({ muted: all.mute.getAttribute("aria-pressed") !== "true" }));
      paintAll();
      list.append(all.li, ...rows.map((row) => row.li));
      box.append(list);
      const keep = document.createElement("p");
      keep.className = "hint";
      keep.textContent = t("snapcast.keep_out");
      box.append(keep);
    } else {
      const none = document.createElement("p");
      none.className = "hint";
      none.textContent = t("snapcast.none");
      box.append(none);
    }
    if (data.web) {
      const link = document.createElement("a");
      link.className = "btn-link";
      link.href = "http://" + window.location.hostname + ":" + data.port + "/";
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = t("snapcast.browser");
      box.append(link);
    }
  }
  await openModal({ title: t("snapcast.clients"), bodyNode: box, modalClass: "modal-dedication", actions: false });
  refreshAudioOutputs();
});
document.getElementById("btnAudioTest").addEventListener("click", async () => {
  const btn = document.getElementById("btnAudioTest");
  btn.disabled = true;
  try {
    const result = await apiPost("/api/audio/test", { output: document.getElementById("audioOutputSelect").value });
    if (result.ok) showToast(t("audioout.test_sent"));
    else showError(result.error);
  } finally {
    btn.disabled = false;
  }
});
refreshAudioOutputs();
refreshEvery(refreshAudioOutputs, 20000, ["audio/output"]);

const streamEnabled = document.getElementById("streamEnabled");
streamEnabled.addEventListener("change", paintStreamRows);
paintStreamRows();

let recentPlayingKey;
let recentRetry = null;

