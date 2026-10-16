/* The curve the owner drew, as the daemon reads it: the same rule as
   src/volume_curve.py, here for the strip to answer while a dot is dragged. */
const CURVE_MINUTES = 1440;
const CURVE_HEIGHT = 110;
const CURVE_LEVEL_PAD = 10;

function curveLevelAt(points, minute) {
  if (!points.length) return null;
  const at = ((minute % CURVE_MINUTES) + CURVE_MINUTES) % CURVE_MINUTES;
  for (let i = 0; i < points.length; i += 1) {
    const one = points[i];
    const next = points[(i + 1) % points.length];
    const span = ((next.at - one.at) % CURVE_MINUTES + CURVE_MINUTES) % CURVE_MINUTES;
    if (span === 0) continue;
    const walked = ((at - one.at) % CURVE_MINUTES + CURVE_MINUTES) % CURVE_MINUTES;
    if (walked < span) return one.level + (next.level - one.level) * walked / span;
  }
  return points[points.length - 1].level;
}

function curveIsCut(points, minute) {
  return points.filter((one) => one.at === minute).length > 1;
}

function curveY(level) {
  return CURVE_HEIGHT - CURVE_LEVEL_PAD - level * (CURVE_HEIGHT - 2 * CURVE_LEVEL_PAD) / 100;
}

function curveLevelFromY(y) {
  const raw = (CURVE_HEIGHT - CURVE_LEVEL_PAD - y) / (CURVE_HEIGHT - 2 * CURVE_LEVEL_PAD) * 100;
  return Math.max(0, Math.min(100, Math.round(raw)));
}

let volumeCurvePoints = [];
let volumeCurveDrag = null;
// The radio's clock, told with the points: a phone's own clock may be far off.
let volumeCurveNow = null;

function curveSvg() {
  return document.getElementById("volumeCurveSvg");
}

function curveMinuteAt(clientX) {
  const svg = curveSvg();
  const box = svg.getBoundingClientRect();
  if (!box.width) return 0;
  const ratio = (clientX - box.left) / box.width * CURVE_MINUTES;
  return Math.max(0, Math.min(CURVE_MINUTES - 1, Math.round(ratio)));
}

function curveLevelAtPointer(clientY) {
  const box = curveSvg().getBoundingClientRect();
  if (!box.height) return 0;
  return curveLevelFromY((clientY - box.top) / box.height * CURVE_HEIGHT);
}

function curveHour(minute) {
  return String(Math.floor(minute / 60)).padStart(2, "0") + ":"
    + String(minute % 60).padStart(2, "0");
}

function curveReadout(minute, level) {
  const chosen = Number(document.getElementById("baseVolume").value);
  const shown = Number.isFinite(chosen) ? chosen : null;
  const line = t("settings.volume_curve_readout", { at: curveHour(minute), level: Math.round(level) });
  return shown === null ? line
    : line + " " + t("settings.volume_curve_readout_played",
                     { played: Math.round(shown * level / 100) });
}

function paintVolumeCurve(readoutMinute) {
  const svg = curveSvg();
  const points = volumeCurvePoints;
  const parts = [];
  const line = (x1, y1, x2, y2, cls) =>
    parts.push('<line class="' + cls + '" x1="' + x1 + '" y1="' + y1
               + '" x2="' + x2 + '" y2="' + y2 + '"/>');
  for (let i = 0; i < points.length; i += 1) {
    const one = points[i];
    const next = points[(i + 1) % points.length];
    const span = ((next.at - one.at) % CURVE_MINUTES + CURVE_MINUTES) % CURVE_MINUTES;
    if (span === 0) {
      // Two dots at the same time: the level changes without any fade.
      line(one.at, curveY(one.level), one.at, curveY(next.level), "curve-cut");
      continue;
    }
    if (next.at > one.at) {
      line(one.at, curveY(one.level), next.at, curveY(next.level), "curve-line");
    } else {
      const midnight = curveLevelAt([one, next], 0);
      line(one.at, curveY(one.level), CURVE_MINUTES, curveY(midnight), "curve-line");
      line(0, curveY(midnight), next.at, curveY(next.level), "curve-line");
    }
  }
  if (points.length) {
    const now = new Date();
    const minute = volumeCurveNow === null ? now.getHours() * 60 + now.getMinutes()
      : volumeCurveNow;
    line(minute, 0, minute, CURVE_HEIGHT, "curve-now");
    if (readoutMinute === undefined) readoutMinute = minute;
  }
  for (let i = 0; i < points.length; i += 1) {
    const one = points[i];
    parts.push('<circle class="curve-dot" data-index="' + i + '" cx="' + one.at
               + '" cy="' + curveY(one.level) + '" r="10" tabindex="0" role="slider"'
               + ' aria-valuemin="0" aria-valuemax="100" aria-valuenow="' + one.level + '"'
               + ' aria-label="' + curveHour(one.at) + '"/>');
  }
  svg.innerHTML = parts.join("");
  const readout = document.getElementById("volumeCurveReadout");
  if (points.length) {
    const level = curveLevelAt(points, readoutMinute);
    readout.textContent = curveReadout(readoutMinute, level);
    readout.hidden = false;
  } else {
    readout.textContent = t("settings.volume_curve_empty");
  }
  paintVolumeCurveSummary();
}

function paintVolumeCurveSummary() {
  const list = document.getElementById("volumeCurveSummary");
  const points = volumeCurvePoints;
  const lines = [];
  for (let i = 0; i < points.length; i += 1) {
    const one = points[i];
    const previous = points[(i - 1 + points.length) % points.length];
    const span = ((one.at - previous.at) % CURVE_MINUTES + CURVE_MINUTES) % CURVE_MINUTES;
    if (previous.level === one.level && span) continue;
    const values = { at: curveHour(one.at), from: previous.level, to: one.level };
    let key = "settings.volume_curve_line_hold";
    if (span === 0) key = "settings.volume_curve_line_cut";
    else if (one.level > previous.level) key = "settings.volume_curve_line_rise";
    else if (one.level < previous.level) key = "settings.volume_curve_line_fall";
    lines.push(t(key, Object.assign({}, values, { minutes: span,
                                                  seconds: span * 60 })));
  }
  list.replaceChildren(...lines.map((line) => {
    const item = document.createElement("li");
    item.textContent = line;
    return item;
  }));
}

async function refreshVolumeCurve() {
  const result = await apiGet("/api/volume_curve");
  if (!result.ok || !result.data) return;
  volumeCurvePoints = (result.data.points || []).map((one) => ({ at: one.at, level: one.level }));
  if (result.data.now && typeof result.data.now.minute === "number") {
    volumeCurveNow = result.data.now.minute;
  }
  paintVolumeCurve();
}

async function saveVolumeCurve() {
  const result = await apiPost("/api/volume_curve", { points: volumeCurvePoints });
  if (!result.ok) {
    showError(result.error);
    return;
  }
  showToast(t("alert.settings_saved"));
}

function curvePointIndexAt(target) {
  const dot = target && target.closest ? target.closest(".curve-dot") : null;
  return dot ? Number(dot.dataset.index) : null;
}

/* A finger on a phone fires pointer events; jsdom, and a mouse anywhere else,
   only has the mouse ones. One path of code, whichever pair is there. */
const CURVE_DOWN = window.PointerEvent ? "pointerdown" : "mousedown";
const CURVE_MOVE = window.PointerEvent ? "pointermove" : "mousemove";
const CURVE_UP = window.PointerEvent ? "pointerup" : "mouseup";

function curveMove(event) {
  if (volumeCurveDrag === null) return;
  const point = volumeCurvePoints[volumeCurveDrag];
  if (!point) return;
  point.at = curveMinuteAt(event.clientX);
  point.level = curveLevelAtPointer(event.clientY);
  paintVolumeCurve(point.at);
}

function curveEnd() {
  if (volumeCurveDrag === null) return;
  volumeCurveDrag = null;
  document.removeEventListener(CURVE_MOVE, curveMove);
  document.removeEventListener(CURVE_UP, curveEnd);
  saveVolumeCurve();
}

function curveKey(event) {
  const index = curvePointIndexAt(event.target);
  if (index === null) return;
  const point = volumeCurvePoints[index];
  if (!point) return;
  const step = event.shiftKey ? 1 : 15;
  const levelStep = event.shiftKey ? 1 : 5;
  if (event.key === "ArrowLeft") point.at = (point.at - step + CURVE_MINUTES) % CURVE_MINUTES;
  else if (event.key === "ArrowRight") point.at = (point.at + step) % CURVE_MINUTES;
  else if (event.key === "ArrowUp") point.level = Math.min(100, point.level + levelStep);
  else if (event.key === "ArrowDown") point.level = Math.max(0, point.level - levelStep);
  else if (event.key === "Delete" || event.key === "Backspace") {
    if (volumeCurvePoints.length > 1) volumeCurvePoints.splice(index, 1);
  } else {
    return;
  }
  event.preventDefault();
  paintVolumeCurve();
  saveVolumeCurve();
}

function curveDown(event) {
  const index = curvePointIndexAt(event.target);
  if (index === null) {
    // A double-click on the strip adds a dot; a single one only reads out.
    return;
  }
  event.preventDefault();
  volumeCurveDrag = index;
  document.addEventListener(CURVE_MOVE, curveMove);
  document.addEventListener(CURVE_UP, curveEnd);
}

function curveAdd(event) {
  if (volumeCurveDrag !== null) return;
  event.preventDefault();
  const at = curveMinuteAt(event.clientX);
  const level = curveLevelAtPointer(event.clientY);
  volumeCurvePoints = volumeCurvePoints.filter((one) => one.at !== at);
  volumeCurvePoints.push({ at, level });
  volumeCurvePoints.sort((one, other) => one.at - other.at);
  paintVolumeCurve(at);
  saveVolumeCurve();
}

function curveRemove(event) {
  const index = curvePointIndexAt(event.target);
  if (index === null) return;
  event.preventDefault();
  if (volumeCurvePoints.length <= 1) return;
  volumeCurvePoints.splice(index, 1);
  paintVolumeCurve();
  saveVolumeCurve();
}

function setupVolumeCurve() {
  const svg = curveSvg();
  if (!svg) return;
  svg.addEventListener(CURVE_DOWN, curveDown);
  svg.addEventListener("dblclick", curveAdd);
  svg.addEventListener("contextmenu", curveRemove);
  svg.addEventListener("keydown", curveKey);
  svg.addEventListener("mousemove", (event) => {
    if (volumeCurveDrag !== null) return;
    const at = curveMinuteAt(event.clientX);
    paintVolumeCurve(at);
  });
}

window.LANG_CHANGE_LISTENERS.push(paintVolumeShape);
paintVolumeShape();
setupVolumeCurve();
window.LANG_CHANGE_LISTENERS.push(() => {
  paintWledScenes();
  if (wledLast) {
    paintWledDevices(wledLast.devices || []);
    paintWledClock(wledLast);
  }
});

document.getElementById("wledDiscover").addEventListener("click", async (e) => {
  const button = e.currentTarget;
  button.disabled = true;
  const result = await apiPost("/api/wled/discover", {});
  button.disabled = false;
  if (!result.ok) {
    showError(result.error);
    return;
  }
  const found = (result.data && result.data.devices) || [];
  if (!found.length) {
    showToast(t("lights.found_none"), t("lights.found_none_hint"));
    return;
  }
  const field = document.getElementById("wledHosts");
  const hosts = field.value.split(/[\s,;]+/).filter(Boolean);
  found.forEach((device) => { if (!hosts.includes(device.host)) hosts.push(device.host); });
  field.value = hosts.join(", ");
  field.dispatchEvent(new Event("input", { bubbles: true }));
  paintWledDevices(found);
  showToast(t("lights.found", { n: found.length }), t("lights.found_save"));
});

document.querySelectorAll(".wled-test").forEach((button) => {
  button.addEventListener("click", async () => {
    const select = document.getElementById(button.dataset.for);
    const preset = Number(select.value);
    if (!preset) {
      showToast(t("lights.keep_nothing"));
      return;
    }
    const result = await apiPost("/api/wled/test", { preset });
    if (!result.ok) showError(result.error);
  });
});

document.getElementById("wledRefresh").addEventListener("click", async () => {
  const result = await apiPost("/api/wled/refresh", {});
  if (!result.ok) {
    showError(result.error);
    return;
  }
  showToast(t("lights.clock_sent"));
  setTimeout(refreshWled, 3000);
});

let folderPickerTarget = null;
let folderPickerPath = "";

function closeFolderPicker() {
  document.getElementById("folderOverlay").hidden = true;
  folderPickerTarget = null;
}

async function showFolder(path) {
  const list = document.getElementById("folderList");
  const status = document.getElementById("folderStatus");
  status.textContent = "";
  const result = await apiGet("/api/browse" + (path ? "?path=" + encodeURIComponent(path) : ""));
  if (!result.ok) {
    if (path) return showFolder("");
    status.textContent = errorLabel(result.error);
    return;
  }
  const data = result.data;
  folderPickerPath = data.path;
  document.getElementById("folderPath").textContent = data.path;
  document.getElementById("folderUp").disabled = !data.parent;
  document.getElementById("folderUp").dataset.parent = data.parent || "";

  const roots = document.getElementById("folderRoots");
  roots.innerHTML = "";
  (data.roots || []).forEach((root) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "breadcrumb-item";
    btn.textContent = root;
    btn.classList.toggle("active", folderPickerPath === root);
    btn.addEventListener("click", () => showFolder(root));
    roots.appendChild(btn);
  });

  list.innerHTML = "";
  if (!data.dirs.length) {
    const li = document.createElement("li");
    li.textContent = t("browse.empty");
    list.appendChild(li);
    return;
  }

  data.dirs.forEach((dir) => {
    const li = document.createElement("li");
    const row = document.createElement("button");
    row.type = "button";
    row.className = "folder-row";
    const name = document.createElement("span");
    name.className = "folder-name";
    name.dataset.icon = "folder";
    name.textContent = dir.name;
    row.appendChild(name);
    if (dir.audio_files) {
      const count = document.createElement("span");
      count.className = "folder-count";
      count.dataset.icon = "music";
      count.textContent = dir.audio_files;
      count.title = t("browse.audio_count", { n: dir.audio_files });
      row.appendChild(count);
      row.setAttribute("aria-label", dir.name + " - " + t("browse.audio_count", { n: dir.audio_files }));
    }
    row.addEventListener("click", () => showFolder(dir.path));
    li.appendChild(row);
    list.appendChild(li);
  });
}

document.getElementById("btnAnnFolderBrowse").addEventListener("click", () => {
  const input = document.getElementById("annFolder");
  folderPickerTarget = input;
  document.getElementById("folderOverlay").hidden = false;
  showFolder(input.value.trim());
});

document.getElementById("folderUp").addEventListener("click", () => {
  const parent = document.getElementById("folderUp").dataset.parent;
  if (parent) showFolder(parent);
});

document.getElementById("folderChoose").addEventListener("click", () => {
  if (!folderPickerTarget) return;
  folderPickerTarget.value = folderPickerPath;

  folderPickerTarget.dispatchEvent(new Event("input", { bubbles: true }));
  folderPickerTarget.dispatchEvent(new Event("change", { bubbles: true }));
  closeFolderPicker();
});

document.getElementById("folderCancel").addEventListener("click", closeFolderPicker);

function formatHM(hour, minute) {
  return String(hour).padStart(2, "0") + ":" + String(minute).padStart(2, "0");
}

let customAnnouncementsCache = [];

